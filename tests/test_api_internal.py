"""测试内部 API 路由（/internal/*）。"""
from __future__ import annotations

import pytest


@pytest.fixture
def internal_token(monkeypatch):
    """设置内部 API token 并同步更新路由模块常量。"""
    token = 'test-internal-token-12345'
    from routes import api_internal
    monkeypatch.setattr(api_internal, 'INTERNAL_API_TOKEN', token)
    yield token


class TestInternalBackup:
    def test_missing_token_config(self, client, monkeypatch):
        from routes import api_internal
        monkeypatch.setattr(api_internal, 'INTERNAL_API_TOKEN', '')
        resp = client.post('/internal/backup')
        assert resp.status_code == 503
        assert resp.json['status'] == 'error'

    def test_unauthorized(self, client, internal_token):
        resp = client.post('/internal/backup')
        assert resp.status_code == 401

    def test_wrong_token(self, client, internal_token):
        resp = client.post(
            '/internal/backup',
            headers={'Authorization': 'Bearer wrong-token'},
        )
        assert resp.status_code == 401

    def test_success(self, client, internal_token, monkeypatch):
        from routes import api_internal
        called = []
        monkeypatch.setattr(api_internal, 'backup_db_to_gcs', lambda: called.append(True))
        resp = client.post(
            '/internal/backup',
            headers={'Authorization': f'Bearer {internal_token}'},
        )
        assert resp.status_code == 200
        assert resp.json['status'] == 'success'
        assert called

    def test_postgres_returns_not_backed_up(self, client, internal_token, monkeypatch):
        from routes import api_internal
        called = []
        monkeypatch.setattr(api_internal, 'backup_db_to_gcs', lambda: called.append(True))
        monkeypatch.setattr(api_internal, 'DB_TYPE', 'postgres')
        resp = client.post(
            '/internal/backup',
            headers={'Authorization': f'Bearer {internal_token}'},
        )
        assert resp.status_code == 200
        assert resp.json['status'] == 'success'
        assert resp.json['data']['backed_up'] is False
        assert 'Cloud SQL' in resp.json['message']
        assert not called


class TestInternalGarminSync:
    def test_unauthorized(self, client, internal_token):
        resp = client.post('/internal/garmin-sync')
        assert resp.status_code == 401

    def test_not_configured(self, client, internal_token, monkeypatch):
        monkeypatch.delenv('GARMIN_EMAIL', raising=False)
        monkeypatch.delenv('GARMIN_USER_ID', raising=False)
        resp = client.post(
            '/internal/garmin-sync',
            headers={'Authorization': f'Bearer {internal_token}'},
        )
        assert resp.status_code == 200
        assert resp.json['data']['synced'] is False

    def test_missing_token_file(self, client, internal_token, monkeypatch, tmp_path):
        monkeypatch.setenv('GARMIN_EMAIL', 'test@example.com')
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_TOKEN_DIR', str(tmp_path))
        resp = client.post(
            '/internal/garmin-sync',
            headers={'Authorization': f'Bearer {internal_token}'},
        )
        assert resp.status_code == 503
        assert resp.json['error_type'] == 'garmin_auth'

    def test_success(self, client, internal_token, monkeypatch, tmp_path):
        monkeypatch.setenv('GARMIN_EMAIL', 'test@example.com')
        monkeypatch.setenv('GARMIN_USER_ID', '1')
        monkeypatch.setenv('GARMIN_TOKEN_DIR', str(tmp_path))
        token_file = tmp_path / 'garmin_tokens.json'
        token_file.write_text('{}')

        from services import garmin_service
        monkeypatch.setattr(
            garmin_service,
            'sync_activities',
            lambda user_id, days: {'added': 3},
        )

        resp = client.post(
            '/internal/garmin-sync',
            headers={'Authorization': f'Bearer {internal_token}'},
        )
        assert resp.status_code == 200
        assert resp.json['data']['synced'] is True
        assert resp.json['data']['result']['added'] == 3
