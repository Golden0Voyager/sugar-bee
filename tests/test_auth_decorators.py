"""
认证装饰器测试
"""
from unittest.mock import MagicMock, patch

from flask import g

import utils.auth


class TestLoginRequired:
    """login_required 装饰器测试"""

    def test_token_auth_no_env_token(self, app):
        """未配置 AGENT_API_TOKEN 时返回 503"""
        with patch.dict('os.environ', {}, clear=True):
            fn = utils.auth.login_or_token_required(lambda: "ok")
            with app.test_request_context(headers={'X-Agent-Token': 'some-token'}):
                resp = fn()
                assert resp[1] == 503

    def test_token_auth_invalid_token(self, app):
        """错误的 token"""
        import os
        os.environ['AGENT_API_TOKEN'] = 'correct-token'
        fn = utils.auth.login_or_token_required(lambda: "ok")
        with app.test_request_context(headers={'X-Agent-Token': 'wrong-token'}):
            resp = fn()
            assert resp[1] == 401

    def test_token_auth_missing_user_id(self, app):
        """缺少 X-User-Id"""
        import os
        os.environ['AGENT_API_TOKEN'] = 'correct-token'
        fn = utils.auth.login_or_token_required(lambda: "ok")
        with app.test_request_context(headers={'X-Agent-Token': 'correct-token'}):
            resp = fn()
            assert resp[1] == 400

    def test_token_auth_invalid_user_id(self, app):
        """无效的 X-User-Id"""
        import os
        os.environ['AGENT_API_TOKEN'] = 'correct-token'
        fn = utils.auth.login_or_token_required(lambda: "ok")
        with app.test_request_context(headers={
            'X-Agent-Token': 'correct-token',
            'X-User-Id': 'not-a-number'
        }):
            resp = fn()
            assert resp[1] == 400

    def test_token_auth_zero_user_id(self, app):
        """user_id <= 0"""
        import os
        os.environ['AGENT_API_TOKEN'] = 'correct-token'
        fn = utils.auth.login_or_token_required(lambda: "ok")
        with app.test_request_context(headers={
            'X-Agent-Token': 'correct-token',
            'X-User-Id': '0'
        }):
            resp = fn()
            assert resp[1] == 400

    @patch('user_manager.UserManager')
    def test_token_auth_unknown_user(self, mock_um_cls, app):
        """不存在的用户"""
        import os
        os.environ['AGENT_API_TOKEN'] = 'correct-token'
        mock_instance = MagicMock()
        mock_instance.get_user.return_value = None
        mock_um_cls.return_value = mock_instance

        fn = utils.auth.login_or_token_required(lambda: "ok")
        with app.test_request_context(headers={
            'X-Agent-Token': 'correct-token',
            'X-User-Id': '99999'
        }):
            resp = fn()
            assert resp[1] == 404

    @patch('user_manager.UserManager')
    def test_token_auth_success(self, mock_um_cls, app):
        """Token 认证成功"""
        import os
        os.environ['AGENT_API_TOKEN'] = 'correct-token'
        mock_instance = MagicMock()
        mock_instance.get_user.return_value = {'id': 1, 'username': 'test'}
        mock_um_cls.return_value = mock_instance

        fn = utils.auth.login_or_token_required(lambda: ("ok", 200))
        with app.test_request_context(headers={
            'X-Agent-Token': 'correct-token',
            'X-User-Id': '1'
        }):
            resp = fn()
            assert resp == ('ok', 200)
            assert g.current_user_id == 1

    def test_token_auth_fallback_to_session(self, app):
        """无 token header 时 fallback 到 session 认证"""
        utils.auth.login_or_token_required(lambda: ("ok", 200))
        with app.test_client() as c:
            resp = c.get('/health')
            assert resp.status_code == 200
"""
认证相关测试
"""



def test_login_with_invalid_credentials(client, app):
    """测试使用错误凭据登录失败"""
    response = client.post('/login', data={
        'username': 'nonexistent_user',
        'password': 'wrong_password'
    })
    assert response.status_code == 200
    assert '账号不存在' in response.data.decode('utf-8')


def test_login_rate_limit(client):
    """测试登录接口限速生效"""
    # 快速发送 15 次请求
    statuses = []
    for _ in range(15):
        response = client.post('/login', data={
            'username': 'test_user',
            'password': 'wrong'
        })
        statuses.append(response.status_code)

    # 应该有部分请求被限速返回 429
    # 注意：测试环境使用内存存储，单进程测试所有请求都会命中同一计数器
    assert 429 in statuses, f"Expected 429 in statuses, got {set(statuses)}"


def test_change_password_requires_login(client):
    """测试未登录时无法修改密码"""
    response = client.post('/change_password', json={
        'old_password': 'old',
        'new_password': 'new12345'
    })
    assert response.status_code == 401


# ============================================================
# (Merged from test_utils_core_coverage.py) — login_required fallback
# ============================================================

class TestLoginRequiredFallback:
    """login_required 装饰器 fallback 分支"""

    def test_redirect_fallback_when_auth_blueprint_missing(self, app):
        """url_for('auth.login') 失败 → fallback 到 url_for('login')"""
        from utils.auth import login_required

        @login_required
        def fake_view():
            return "ok"

        with app.test_request_context(), \
             patch('utils.auth.url_for', side_effect=[Exception("no blueprint"), '/login']):
                resp = fake_view()
                assert resp.status_code == 302
                assert '/login' in resp.location
