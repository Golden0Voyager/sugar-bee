"""多用户 Garmin 绑定：token 按用户隔离、绑定 CRUD、逐用户同步与路由门禁。"""
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from services import garmin_service


@pytest.fixture
def garmin_env(tmp_path, monkeypatch):
    """隔离 token 目录 + 独立的 garmin_bindings 测试库，清理老配置环境变量。"""
    monkeypatch.setattr(garmin_service, 'TOKEN_DIR', str(tmp_path))
    monkeypatch.delenv('GARMIN_USER_ID', raising=False)
    monkeypatch.delenv('GARMIN_EMAIL', raising=False)
    monkeypatch.delenv('GARMIN_IS_CN', raising=False)

    db_path = str(tmp_path / 'garmin_test.db')
    conn = sqlite3.connect(db_path)
    conn.execute('''CREATE TABLE garmin_bindings (
        user_id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        is_cn BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

    def fake_get_raw_conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(garmin_service, 'get_raw_conn', fake_get_raw_conn)
    monkeypatch.setattr(garmin_service, 'put_raw_conn', lambda conn: conn.close())
    return tmp_path


def _make_token(dir_path):
    os.makedirs(dir_path, exist_ok=True)
    with open(os.path.join(dir_path, 'garmin_tokens.json'), 'w') as f:
        f.write('{}')


class TestTokenIsolation:
    def test_per_user_token_dir_wins(self, garmin_env):
        per_user = garmin_service._user_token_dir(2)
        _make_token(per_user)
        assert garmin_service._resolve_token_dir(2) == per_user

    def test_legacy_user_falls_back_to_global_dir(self, garmin_env, monkeypatch):
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        assert garmin_service._resolve_token_dir(1) == str(garmin_env)
        # 其他用户不共享老配置的全局 token
        assert garmin_service._resolve_token_dir(2) == garmin_service._user_token_dir(2)

    def test_binding_crud_and_unbind(self, garmin_env):
        garmin_service._upsert_binding(2, 'a@example.com', True)
        garmin_service._upsert_binding(3, 'b@example.com', False)
        assert garmin_service.get_binding(2)['email'] == 'a@example.com'
        assert garmin_service.get_binding(2)['is_cn'] is True
        assert len(garmin_service.list_bindings()) == 2

        # 重复绑定同一用户是覆盖而不是新增
        garmin_service._upsert_binding(2, 'new@example.com', False)
        assert garmin_service.get_binding(2)['email'] == 'new@example.com'
        assert len(garmin_service.list_bindings()) == 2

        per_user = garmin_service._user_token_dir(2)
        _make_token(per_user)
        garmin_service.unbind_account(2)
        assert garmin_service.get_binding(2) is None
        assert not os.path.exists(per_user)
        # 解绑不影响其他用户
        assert garmin_service.get_binding(3)['email'] == 'b@example.com'

    def test_legacy_binding_requires_server_side_unbind(self, garmin_env, monkeypatch):
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_EMAIL', 'old@example.com')
        garmin_service._upsert_binding(1, 'old@example.com', False)
        with pytest.raises(RuntimeError, match='服务器旧配置'):
            garmin_service.unbind_account(1)
        assert garmin_service.get_binding(1) is not None


class TestBindAccount:
    def test_bind_success_saves_token_and_binding(self, garmin_env):
        with patch.object(garmin_service, 'Garmin') as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            result = garmin_service.bind_account(2, 'a@example.com', 'secret', is_cn=True)

        assert result['email'] == 'a@example.com'
        mock_cls.assert_called_once_with(email='a@example.com', password='secret', is_cn=True)
        client.login.assert_called_once_with(garmin_service._user_token_dir(2))
        assert garmin_service.get_binding(2)['is_cn'] is True

    def test_bind_auth_failure_leaves_no_binding(self, garmin_env):
        with patch.object(garmin_service, 'Garmin') as mock_cls:
            client = MagicMock()
            client.login.side_effect = garmin_service.GarminConnectAuthenticationError('bad credentials')
            mock_cls.return_value = client
            with pytest.raises(RuntimeError, match='认证失败'):
                garmin_service.bind_account(2, 'a@example.com', 'wrong')
        assert garmin_service.get_binding(2) is None

    def test_has_binding_checks_legacy_env(self, garmin_env, monkeypatch):
        assert garmin_service.has_garmin_binding(1) is False
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_EMAIL', 'old@example.com')
        assert garmin_service.has_garmin_binding(1) is True
        assert garmin_service.has_garmin_binding(2) is False

    def test_legacy_token_can_be_restored_from_gcs(self, garmin_env, monkeypatch):
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_EMAIL', 'old@example.com')

        def fake_restore(gcs_path, local_path):
            _make_token(os.path.dirname(local_path))

        with patch.object(garmin_service, 'sync_file_from_gcs', side_effect=fake_restore) as mock_restore, \
                patch.object(garmin_service, 'Garmin') as mock_cls:
            client = mock_cls.return_value
            assert garmin_service._get_client(1) is client

        mock_restore.assert_called_once_with(
            garmin_service.TOKEN_GCS_PATH, garmin_service._legacy_token_file())
        client.login.assert_called_once_with(str(garmin_env))


class TestSyncAllBindings:
    def test_each_user_synced_and_error_isolated(self, garmin_env):
        garmin_service._upsert_binding(1, 'a@example.com', False)
        garmin_service._upsert_binding(2, 'b@example.com', False)
        calls = []

        def fake_sync(uid, days=30):
            calls.append(uid)
            if uid == 1:
                raise RuntimeError('token 失效')
            return {'inserted': 3, 'skipped': 1}

        with patch.object(garmin_service, 'sync_activities', side_effect=fake_sync):
            results = garmin_service.sync_all_bindings()

        assert calls == [1, 2]
        assert 'error' in results[1]
        assert results[2]['inserted'] == 3

    def test_legacy_user_included_once(self, garmin_env, monkeypatch):
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_EMAIL', 'old@example.com')
        garmin_service._upsert_binding(1, 'old@example.com', False)
        garmin_service._upsert_binding(2, 'b@example.com', False)
        calls = []
        with patch.object(garmin_service, 'sync_activities',
                          side_effect=lambda uid, days=30: calls.append(uid) or {'inserted': 0}):
            garmin_service.sync_all_bindings()
        assert calls == [1, 2]

    def test_ensure_legacy_binding_registers_once(self, garmin_env, monkeypatch):
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_EMAIL', 'old@example.com')
        # 没有全局 token 时不登记
        garmin_service.ensure_legacy_binding()
        assert garmin_service.get_binding(1) is None
        # 有全局 token 才登记，且幂等
        _make_token(str(garmin_env))
        garmin_service.ensure_legacy_binding()
        assert garmin_service.get_binding(1)['email'] == 'old@example.com'
        garmin_service._upsert_binding(1, 'changed@example.com', False)
        garmin_service.ensure_legacy_binding()
        assert garmin_service.get_binding(1)['email'] == 'changed@example.com'


class TestGarminRoutes:
    def test_status_unbound_then_bound(self, client_authenticated, monkeypatch):
        monkeypatch.delenv('GARMIN_USER_ID', raising=False)
        monkeypatch.delenv('GARMIN_EMAIL', raising=False)
        resp = client_authenticated.get('/api/garmin/status')
        assert resp.status_code == 200
        assert resp.get_json()['data']['bound'] is False

        with patch.object(garmin_service, '_account_for',
                          return_value={'email': 'a@example.com', 'is_cn': True}):
            resp = client_authenticated.get('/api/garmin/status')
            data = resp.get_json()['data']
            assert data['bound'] is True
            assert data['email'] == 'a@example.com'
            assert data['is_cn'] is True

    def test_bind_validates_input(self, client_authenticated):
        assert client_authenticated.post('/api/garmin/bind', json={}).status_code == 400
        assert client_authenticated.post(
            '/api/garmin/bind', json={'email': 'a@example.com'}).status_code == 400

    def test_bind_success_and_failure(self, client_authenticated):
        with patch.object(garmin_service, 'bind_account',
                          return_value={'user_id': 1, 'email': 'a@example.com', 'is_cn': False}) as mock_bind:
            resp = client_authenticated.post('/api/garmin/bind', json={
                'email': 'a@example.com', 'password': 'secret', 'is_cn': False,
            })
            assert resp.status_code == 200
            assert mock_bind.call_args[0][1] == 'a@example.com'
            assert mock_bind.call_args[0][2] == 'secret'

        with patch.object(garmin_service, 'bind_account',
                          side_effect=RuntimeError('Garmin 认证失败')):
            resp = client_authenticated.post('/api/garmin/bind', json={
                'email': 'a@example.com', 'password': 'bad',
            })
            assert resp.status_code == 400
            assert '认证失败' in resp.get_json()['message']

    def test_sync_requires_binding(self, client_authenticated):
        with patch.object(garmin_service, 'has_garmin_binding', return_value=False):
            assert client_authenticated.post('/sync_garmin').status_code == 403

    def test_unbind_route(self, client_authenticated):
        with patch.object(garmin_service, 'unbind_account') as mock_unbind:
            resp = client_authenticated.post('/api/garmin/unbind')
            assert resp.status_code == 200
            mock_unbind.assert_called_once()

    def test_index_contains_binding_card(self, client_authenticated):
        html = client_authenticated.get('/').data.decode('utf-8')
        assert 'id="garminBindingCard"' in html
        assert 'id="garminBindForm"' in html
        assert 'function bindGarmin' in html
        assert 'function loadGarminStatus' in html
