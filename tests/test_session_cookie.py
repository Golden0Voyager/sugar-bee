"""会话 cookie 加固守卫。

IP 明文 HTTP + 无域名，cookie 是唯一凭证，三个开关缺一不可：
  HttpOnly  脚本读不到会话（XSS 偷不走）
  SameSite  跨站请求不带 cookie（CSRF 基础防护）
  有效期    闲置超时即失效（utils.auth.start_session 里标记 permanent 才生效）
"""
import datetime

import pytest


def test_cookie_flags(app):
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True
    assert app.config['SESSION_COOKIE_SAMESITE'] in ('Lax', 'Strict')


def test_session_lifetime_default_is_one_day(app):
    assert app.config['PERMANENT_SESSION_LIFETIME'] == datetime.timedelta(hours=24)


def test_login_sets_permanent_cookie(client_authenticated):
    """登录后的 cookie 必须带过期时间（permanent），否则超时配置形同虚设。"""
    resp = client_authenticated.get('/login')
    set_cookie = resp.headers.get('Set-Cookie', '')
    # 未登录的 GET /login 可能不重发 cookie；用一次真实登录再断言
    login = client_authenticated.post(
        '/login', data={'username': '_test', 'password': '_test_pass'},
        follow_redirects=False,
    )
    set_cookie = login.headers.get('Set-Cookie', set_cookie)
    assert 'Expires=' in set_cookie or 'Max-Age=' in set_cookie
    assert 'HttpOnly' in set_cookie
    assert 'SameSite=Lax' in set_cookie


def test_login_clears_previous_session(client_authenticated):
    """会话固定防护：登录时先清空旧会话内容。"""
    client = client_authenticated
    with client.session_transaction() as sess:
        sess['current_user_id'] = 99999   # 攻击者预置的脏值
        sess['username'] = 'attacker'

    client.post('/login', data={'username': '_test', 'password': '_test_pass'})

    with client.session_transaction() as sess:
        assert sess['current_user_id'] != 99999
        assert sess['username'] == '_test'


@pytest.mark.parametrize('hours', [1, 24, 168])
def test_lifetime_is_configurable(hours, monkeypatch):
    """SESSION_LIFETIME_HOURS 改了要真的生效（模块级读取，需重新加载 app）。"""
    import importlib

    monkeypatch.setenv('SESSION_LIFETIME_HOURS', str(hours))
    import app as app_mod
    reloaded = importlib.reload(app_mod)
    try:
        assert reloaded.app.config['PERMANENT_SESSION_LIFETIME'] == datetime.timedelta(hours=hours)
    finally:
        monkeypatch.delenv('SESSION_LIFETIME_HOURS', raising=False)
        importlib.reload(app_mod)
