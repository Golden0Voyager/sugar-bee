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