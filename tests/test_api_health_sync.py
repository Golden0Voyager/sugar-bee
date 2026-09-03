"""Apple Health sync API: bind endpoints"""
from unittest.mock import MagicMock, patch


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


def _bind_device(client):
    """helper: 完成一次完整的设备绑定流程，返回 (device_id, device_token)"""
    # 先登录
    with client.session_transaction() as sess:
        from core import config as _core_config
        from user_manager import UserManager
        um = UserManager(_core_config.DB_NAME)
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


class TestHealthSyncSync:
    """Apple Health 数据同步测试"""

    def _bind_device(self, client):
        return _bind_device(client)

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

class TestHealthSyncUnbind:
    """解除绑定测试"""

    def test_unbind_success(self, client):
        """成功解除绑定"""
        device_id, device_token = _bind_device(client)

        # 确认已绑定
        confirm_resp = client.get('/api/v1/health-sync/confirm_binding')
        assert confirm_resp.json['data']['device_id'] is not None

        # 解除绑定
        result = client.post('/api/v1/health-sync/unbind')
        assert result.status_code == 200

        # 确认已解除
        confirm_resp2 = client.get('/api/v1/health-sync/confirm_binding')
        assert confirm_resp2.json['data']['device_id'] is None

    def test_unbind_requires_auth(self, client):
        """未登录返回 401"""
        result = client.post('/api/v1/health-sync/unbind', content_type='application/json')
        assert result.status_code == 401


class TestHealthSyncCoverage:
    """覆盖边缘分支：异常处理、rowcount 检查等"""

    def test_verify_device_auth_exception(self):
        """_verify_device_auth 数据库异常返回 None"""
        from routes.api_health_sync import _verify_device_auth
        with patch('routes.api_health_sync.get_db') as mock_g:
            mock_g.side_effect = Exception("DB error")
            assert _verify_device_auth('d', 't') is None

    def test_bind_from_shortcut_rowcount_zero(self, client_authenticated):
        """bind_from_shortcut 遇到 rowcount==0 返回 409"""
        with patch('routes.api_health_sync.get_db') as mock_g:
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [{'id': 99, 'user_id': 1}, None]
            mock_c.rowcount = 0
            mock_g.return_value.cursor.return_value = mock_c
            result = client_authenticated.post(
                '/api/v1/health-sync/bind_from_shortcut',
                json={'code': '999999', 'device_name': 'Test'},
            )
            assert result.status_code == 409
            assert '冲突' in result.json['message']

    def test_bind_from_shortcut_exception(self, client_authenticated):
        """bind_from_shortcut 异常返回 500"""
        with patch('routes.api_health_sync.get_db') as mock_g:
            mock_g.side_effect = Exception("DB error")
            result = client_authenticated.post(
                '/api/v1/health-sync/bind_from_shortcut',
                json={'code': '123456', 'device_name': 'Test'},
            )
            assert result.status_code == 500

    def test_confirm_binding_exception(self, client_authenticated):
        """confirm_binding 异常返回 500"""
        with patch('routes.api_health_sync.get_db') as mock_g:
            mock_g.side_effect = Exception("DB error")
            result = client_authenticated.get('/api/v1/health-sync/confirm_binding')
            assert result.status_code == 500

    def test_sync_empty_body(self, client):
        """sync 空请求体（无 JSON body）返回 400"""
        device_id, device_token = _bind_device(client)
        result = client.post(
            '/api/v1/health-sync/sync',
            headers={'X-Device-Id': device_id, 'X-Device-Token': device_token},
            content_type='application/json',
            data='{}',
        )
        assert result.status_code == 400

    def test_sync_exception(self):
        """sync 异常返回 500"""
        with patch('routes.api_health_sync._verify_device_auth') as mock_vda, \
             patch('routes.api_health_sync.get_db') as mock_g:
            mock_vda.return_value = 1
            mock_g.side_effect = Exception("DB commit error")
            from flask import Flask
            _test_app = Flask(__name__)
            with _test_app.test_request_context(
                '/api/v1/health-sync/sync',
                method='POST',
                headers={'X-Device-Id': 'd', 'X-Device-Token': 't'},
            ):
                from routes.api_health_sync import sync_health_data
                resp, code = sync_health_data()
                assert code == 500

    def test_unbind_exception(self, client_authenticated):
        """unbind 异常返回 500"""
        with patch('routes.api_health_sync.get_db') as mock_g:
            mock_g.side_effect = Exception("DB error")
            result = client_authenticated.post('/api/v1/health-sync/unbind')
            assert result.status_code == 500


class TestDownloadShortcut:
    """下载 iOS 绑定捷径测试"""

    def test_download_requires_auth(self, client):
        """未登录的页面请求重定向到登录页"""
        result = client.get('/api/v1/health-sync/download_shortcut')
        assert result.status_code == 302
        assert '/login' in result.headers['Location']

    def test_download_success_https_url(self, client_authenticated):
        """生成的捷径使用请求的 scheme/host，不再写死 http 和 :80"""
        import plistlib

        with patch('routes.api_health_sync.subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("shortcuts CLI 不可用")
            result = client_authenticated.get(
                '/api/v1/health-sync/download_shortcut',
                base_url='https://localhost',
            )
        assert result.status_code == 200
        shortcut = plistlib.loads(result.data)
        urls = [
            a['WFWorkflowActionParameters']['WFURLActionURL']
            for a in shortcut['WFWorkflowActions']
            if a['WFWorkflowActionIdentifier'] == 'is.workflow.actions.url'
        ]
        assert urls == ['https://localhost/api/v1/health-sync/bind_from_shortcut']

    def test_download_success_localhost(self, client_authenticated):
        """本地开发环境保留 host 与端口"""
        import plistlib

        with patch('routes.api_health_sync.subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("shortcuts CLI 不可用")
            result = client_authenticated.get(
                '/api/v1/health-sync/download_shortcut',
                base_url='http://localhost:5001',
            )
        assert result.status_code == 200
        shortcut = plistlib.loads(result.data)
        urls = [
            a['WFWorkflowActionParameters']['WFURLActionURL']
            for a in shortcut['WFWorkflowActions']
            if a['WFWorkflowActionIdentifier'] == 'is.workflow.actions.url'
        ]
        assert urls == ['http://localhost:5001/api/v1/health-sync/bind_from_shortcut']
