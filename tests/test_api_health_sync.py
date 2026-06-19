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


class TestHealthSyncSync:
    """Apple Health 数据同步测试"""

    def _bind_device(self, client):
        """helper: 完成一次完整的设备绑定流程，返回 (device_id, device_token)"""
        # 先登录
        with client.session_transaction() as sess:
            from user_manager import UserManager
            from core.config import DB_NAME
            um = UserManager(DB_NAME)
            import uuid as _uuid
            unique_name = f'_sync_test_{_uuid.uuid4().hex[:8]}'
            user_id = um.create_user(unique_name, 'Sync测试', {})

            # 清理可能的旧数据
            from utils.db import get_raw_conn, put_raw_conn
            clean_conn = get_raw_conn()
            clean_conn.execute("DELETE FROM device_bindings WHERE user_id = ?", (user_id,))
            clean_conn.commit()
            put_raw_conn(clean_conn)

            sess['current_user_id'] = user_id

        # 生成绑定码
        resp = client.post('/api/v1/health-sync/bind')
        code = resp.json['data']['bind_code']

        # 完成绑定
        resp = client.post('/api/v1/health-sync/bind_from_shortcut',
                            json={'code': code, 'device_name': 'Test Phone'})
        data = resp.json['data']
        return data['device_id'], data['device_token']

    def test_sync_success(self, client):
        """成功同步 Apple Health 数据"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'start_date': '2026-06-19T00:00:00',
                'end_date': '2026-06-19T23:59:59',
                'records': [{
                    'external_id': 'apple_health:test-001',
                    'type': '血糖',
                    'value': 6.2,
                    'unit': 'mmol/L',
                    'timestamp': '2026-06-19T07:15:00+08:00',
                }, {
                    'external_id': 'apple_health:test-002',
                    'type': '步数',
                    'value': 8500,
                    'unit': 'steps',
                    'timestamp': '2026-06-19T12:00:00+08:00',
                }],
            },
        )
        assert result.status_code == 200
        assert result.json['data']['inserted'] == 2
        assert result.json['data']['skipped'] == 0

    def test_sync_dedup(self, client):
        """重复 external_id 应跳过"""
        device_id, device_token = self._bind_device(client)

        # 第一次：插入
        client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:dedup-001',
                    'type': '血糖',
                    'value': 5.5,
                    'timestamp': '2026-06-19T08:00:00+08:00',
                }],
            },
        )

        # 第二次：相同 external_id 应跳过
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:dedup-001',
                    'type': '血糖',
                    'value': 5.5,
                    'timestamp': '2026-06-19T08:00:00+08:00',
                }, {
                    'external_id': 'apple_health:dedup-002',
                    'type': '体重',
                    'value': 72.0,
                    'unit': 'kg',
                    'timestamp': '2026-06-19T08:05:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 1
        assert result.json['data']['skipped'] == 1

    def test_sync_no_auth_header(self, client):
        """缺少鉴权头返回 401"""
        result = client.post(
            '/api/v1/health-sync/sync',
            json={'records': []},
        )
        assert result.status_code == 401

    def test_sync_invalid_token(self, client):
        """无效 device_token 返回 401"""
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': 'fake-id', 'X-Device-Token': 'fake-token'},
            json={'records': [{'type': '血糖', 'value': 5.0, 'timestamp': '2026-06-19T10:00:00'}]},
        )
        assert result.status_code == 401

    def test_sync_empty_records(self, client):
        """空 records 返回错误"""
        device_id, device_token = self._bind_device(client)
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={'records': []},
        )
        assert result.status_code == 400

    def test_sync_blood_pressure(self, client):
        """血压记录应正确拆分"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:bp-sys',
                    'type': '血压收缩压',
                    'value': 120,
                    'unit': 'mmHg',
                    'timestamp': '2026-06-19T09:00:00+08:00',
                }, {
                    'external_id': 'apple_health:bp-dia',
                    'type': '血压舒张压',
                    'value': 80,
                    'unit': 'mmHg',
                    'timestamp': '2026-06-19T09:00:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 2

    def test_sync_heart_rate(self, client):
        """心率和血氧记录"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:hr-001',
                    'type': '心率',
                    'value': 72,
                    'unit': 'bpm',
                    'timestamp': '2026-06-19T10:00:00+08:00',
                }, {
                    'external_id': 'apple_health:spo2-001',
                    'type': '血氧',
                    'value': 98,
                    'unit': '%',
                    'timestamp': '2026-06-19T10:05:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 2

    def test_sync_missing_required_fields(self, client):
        """缺少必填字段的记录应被跳过"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [
                    {'type': '血糖', 'value': 6.0, 'timestamp': '2026-06-19T10:00:00+08:00'},  # valid
                    {'type': '血糖', 'timestamp': '2026-06-19T10:00:00+08:00'},  # missing value
                    {'value': 6.0, 'timestamp': '2026-06-19T10:00:00+08:00'},  # missing type
                    {'type': '血糖', 'value': 6.0},  # missing timestamp
                ],
            },
        )
        assert result.json['data']['inserted'] == 1
        assert result.json['data']['skipped'] == 3

    def test_sync_weight(self, client):
        """体重记录"""
        device_id, device_token = self._bind_device(client)

        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            json={
                'records': [{
                    'external_id': 'apple_health:weight-001',
                    'type': '体重',
                    'value': 72.5,
                    'unit': 'kg',
                    'timestamp': '2026-06-19T07:00:00+08:00',
                }],
            },
        )
        assert result.json['data']['inserted'] == 1