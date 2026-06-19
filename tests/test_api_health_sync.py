"""Apple Health sync API: bind endpoints"""
import json
from unittest.mock import patch, MagicMock


class TestHealthSyncBind:
    """生成绑定码测试"""

    def test_bind_success(self, client_authenticated):
        """成功生成 6 位绑定码"""
        result = client_authenticated.post('/api/v1/health-sync/bind')
        assert result.status_code == 200
        data = result.json['data']
        assert len(data['bind_code']) == 6
        assert data['bind_code'].isdigit()
        assert data['expires_in'] == 1800

    def test_bind_requires_auth(self, client):
        """未登录返回 401"""
        result = client.post('/api/v1/health-sync/bind', content_type='application/json')
        assert result.status_code == 401

    def test_bind_db_error(self, client_authenticated):
        """数据库异常返回 500"""
        with patch('routes.api_health_sync.get_db') as mock_get_db:
            mock_get_db.side_effect = Exception("DB error")
            result = client_authenticated.post('/api/v1/health-sync/bind')
            assert result.status_code == 500
            assert 'error' in result.json['status']

    def test_confirm_binding_no_device(self, client_authenticated):
        """未绑定时返回 device_id: None"""
        result = client_authenticated.get('/api/v1/health-sync/confirm_binding')
        assert result.status_code == 200
        assert result.json['data']['device_id'] is None


class TestHealthSyncBindFromShortcut:
    """通过绑定码完成设备绑定测试"""

    def _create_valid_bind_code(self, client_authenticated):
        """helper: 先调 bind 生成一个有效绑定码并返回"""
        result = client_authenticated.post('/api/v1/health-sync/bind')
        return result.json['data']['bind_code']

    def test_bind_from_shortcut_success(self, client_authenticated):
        """iOS 捷径用有效绑定码完成绑定"""
        code = self._create_valid_bind_code(client_authenticated)
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone 15'},
        )
        assert result.status_code == 200
        data = result.json['data']
        assert len(data['device_id']) == 36  # UUID v4
        assert len(data['device_token']) > 30

    def test_bind_from_shortcut_invalid_code(self, client_authenticated):
        """无效绑定码返回 404"""
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': '000000', 'device_name': 'iPhone'},
        )
        assert result.status_code == 404

    def test_bind_from_shortcut_wrong_format(self, client_authenticated):
        """非数字或非 6 位绑定码返回 400"""
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': 'abc123', 'device_name': 'iPhone'},
        )
        assert result.status_code == 400

        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': '12345', 'device_name': 'iPhone'},
        )
        assert result.status_code == 400

    def test_bind_from_shortcut_code_already_used(self, client_authenticated):
        """绑定码只能使用一次（已用则返回 404，因 bind_code 已被清空）"""
        code = self._create_valid_bind_code(client_authenticated)
        # 第一次使用
        client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone 15'},
        )
        # 再次使用同一码（bind_code 已被设为 NULL → 404）
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone 15'},
        )
        assert result.status_code == 404

    def test_bind_from_shortcut_expired_code(self, client_authenticated, app):
        """过期绑定码返回 404"""
        code = self._create_valid_bind_code(client_authenticated)
        # 模拟过期：通过 Flask app 上下文 + get_raw_conn 修改数据库
        from utils.db import get_raw_conn, put_raw_conn
        _conn = get_raw_conn()
        try:
            _conn.execute(
                "UPDATE device_bindings SET code_expires_at = '2020-01-01' WHERE bind_code = ?",
                (code,),
            )
            _conn.commit()
        finally:
            put_raw_conn(_conn)

        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': code, 'device_name': 'iPhone'},
        )
        assert result.status_code == 404

    def test_bind_from_shortcut_empty_body(self, client_authenticated):
        """空请求体返回 400"""
        result = client_authenticated.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={},
        )
        assert result.status_code == 400

    def test_bind_from_shortcut_no_auth(self, client):
        """bind_from_shortcut 不需要登录（设备没有 session）"""
        result = client.post(
            '/api/v1/health-sync/bind_from_shortcut',
            json={'code': '123456', 'device_name': 'iPhone'},
        )
        # 不能验证是否成功（因为没有有效 code），但至少不返回 401
        assert result.status_code != 401