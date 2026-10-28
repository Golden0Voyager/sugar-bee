import hmac
import os
from functools import wraps

from flask import g, jsonify, redirect, request, session, url_for

from utils.responses import api_error


def login_required(f):
    """
    要求登录的装饰器。
    如果未登录，对于 JSON 请求返回 401 错误，对于页面请求重定向到登录页。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'current_user_id' not in session:
            # 判断是否为 API 请求
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'status': 'error', 'message': '请先登录'}), 401
            # 否则重定向到登录页面
            # 注意：由于使用了 Blueprint，通常应该是 'auth.login'
            # 但为了保持向后兼容，如果 'auth.login' 无法解析，则回退到 'login'
            try:
                return redirect(url_for('auth.login'))
            except Exception:
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def get_admin_usernames():
    """管理员用户名集合，来自环境变量 SUGAR_BEE_ADMIN_USERS（逗号分隔）。

    默认为空 = 没有管理员。切换用户这类调试能力因此默认对所有人关闭，
    只有部署方显式配置后才开启。
    """
    raw = os.environ.get('SUGAR_BEE_ADMIN_USERS', '')
    return {u.strip() for u in raw.split(',') if u.strip()}


def is_current_user_admin():
    """当前登录用户是否在管理员名单里。未登录或查不到用户都视为非管理员。"""
    user_id = session.get('current_user_id')
    if not user_id:
        return False
    admins = get_admin_usernames()
    if not admins:
        return False
    from utils.db import get_db
    c = get_db().cursor()
    c.execute("SELECT username FROM app_users WHERE id = ?", (user_id,))
    row = c.fetchone()
    return bool(row and row['username'] in admins)


def admin_required(f):
    """要求当前登录用户是管理员，否则返回 403。配合 login_required 使用。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_current_user_admin():
            return api_error("仅管理员可执行此操作", status_code=403, error_type="admin_only")
        return f(*args, **kwargs)
    return decorated


def login_or_token_required(f):
    """
    支持两种鉴权（任一通过即可）：
    1. 请求头携带 X-Agent-Token + X-User-Id → token 路径，user_id 写入 flask.g
    2. 否则 fallback 到 login_required（cookie session）

    token 路径仅在服务端配置了 AGENT_API_TOKEN 环境变量时启用。
    比较使用 hmac.compare_digest 防计时攻击。
    user_id 必须存在于 app_users 表中。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token_hdr = request.headers.get('X-Agent-Token')
        if token_hdr:
            env_token = os.environ.get('AGENT_API_TOKEN', '')
            if not env_token:
                return api_error("Agent token not configured on server",
                                 status_code=503, error_type="agent_auth")
            if not hmac.compare_digest(env_token, token_hdr):
                return api_error("Invalid agent token",
                                 status_code=401, error_type="agent_auth")
            uid_hdr = request.headers.get('X-User-Id', '')
            try:
                uid = int(uid_hdr)
                if uid <= 0:
                    raise ValueError("non-positive user_id")
            except (ValueError, TypeError):
                return api_error("X-User-Id header missing or invalid",
                                 status_code=400, error_type="agent_auth")
            from user_manager import UserManager
            if not UserManager().get_user(uid):
                return api_error(f"Unknown user_id: {uid}",
                                 status_code=404, error_type="agent_auth")
            g.current_user_id = uid
            return f(*args, **kwargs)
        return login_required(f)(*args, **kwargs)
    return decorated
