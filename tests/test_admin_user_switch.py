"""切换用户仅管理员可用：环境变量名单、接口门禁与界面入口。

普通用户不应看到「切换用户」入口，后端 /switch_user 也必须返回 403 ——
只藏前端不堵接口等于没关门。管理员名单由部署方通过
SUGAR_BEE_ADMIN_USERS（逗号分隔用户名）配置，默认为空 = 全员关闭。
"""
from pathlib import Path

from flask import session

from utils.auth import get_admin_usernames, is_current_user_admin

TEMPLATE_HTML = Path(__file__).resolve().parent.parent / 'templates' / 'index.html'
APP_JS = Path(__file__).resolve().parent.parent / 'static/js/app.js'


def _frontend():
    """P1b：页面 JS 已拆到 app.js，拼接后结构/JS 断言都能命中。"""
    return TEMPLATE_HTML.read_text(encoding='utf-8') + '\n' + APP_JS.read_text(encoding='utf-8')

class TestAdminUsernames:
    def test_default_empty(self, monkeypatch):
        monkeypatch.delenv('SUGAR_BEE_ADMIN_USERS', raising=False)
        assert get_admin_usernames() == set()

    def test_parse_and_strip(self, monkeypatch):
        monkeypatch.setenv('SUGAR_BEE_ADMIN_USERS', ' alice, bob ,, ')
        assert get_admin_usernames() == {'alice', 'bob'}


class TestIsCurrentUserAdmin:
    def test_not_logged_in(self, app, monkeypatch):
        monkeypatch.setenv('SUGAR_BEE_ADMIN_USERS', '_test')
        with app.test_request_context('/'):
            assert is_current_user_admin() is False

    def test_user_row_missing(self, app, monkeypatch):
        """session 里的 user_id 在库里查不到 -> 非管理员，不能放行"""
        monkeypatch.setenv('SUGAR_BEE_ADMIN_USERS', '_test')
        with app.test_request_context('/'):
            session['current_user_id'] = 999999
            assert is_current_user_admin() is False


class TestSwitchUserGate:
    def test_forbidden_without_admin_config(self, client_authenticated, monkeypatch):
        monkeypatch.delenv('SUGAR_BEE_ADMIN_USERS', raising=False)
        resp = client_authenticated.post('/switch_user/1', json={})
        assert resp.status_code == 403
        assert resp.json['error_type'] == 'admin_only'

    def test_forbidden_for_non_admin(self, client_authenticated, monkeypatch):
        """名单里有别人、没有自己 -> 同样 403"""
        monkeypatch.setenv('SUGAR_BEE_ADMIN_USERS', 'someone_else')
        resp = client_authenticated.post('/switch_user/1', json={})
        assert resp.status_code == 403
        assert resp.json['error_type'] == 'admin_only'

    def test_login_still_required(self, client):
        resp = client.post('/switch_user/1', json={})
        assert resp.status_code in (401, 302)


class TestIndexGating:
    def test_menu_hidden_for_normal_user(self, client_authenticated, monkeypatch):
        monkeypatch.delenv('SUGAR_BEE_ADMIN_USERS', raising=False)
        resp = client_authenticated.get('/')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="userSwitcherList"' not in html
        # 注释里也可能带「切换用户」字样，这里守的是菜单标题本身
        assert '<small class="text-muted fw-semibold">切换用户</small>' not in html
        assert '切换家人' not in html  # 新手引导也不再讲不存在的入口
        assert 'isAdmin: false' in html  # P1b：已并入 window.__BOOT__

    def test_menu_visible_for_admin(self, client_admin):
        resp = client_admin.get('/')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="userSwitcherList"' in html
        assert '切换用户' in html
        assert '切换家人' in html
        assert 'isAdmin: true' in html  # P1b：已并入 window.__BOOT__


class TestSettingsSwitchButtonGating:
    def test_settings_switch_button_requires_admin(self):
        """设置页用户列表里的「切换」按钮同样只对管理员渲染"""
        html = _frontend()
        assert '${!isCurrent && IS_ADMIN ? `' in html
