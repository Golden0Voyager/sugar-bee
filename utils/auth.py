import hmac
import os
from functools import wraps
from flask import session, request, jsonify, redirect, url_for, g

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
            from core.config import DB_NAME
            if not UserManager(DB_NAME).get_user(uid):
                return api_error(f"Unknown user_id: {uid}",
                                 status_code=404, error_type="agent_auth")
            g.current_user_id = uid
            return f(*args, **kwargs)
        return login_required(f)(*args, **kwargs)
    return decorated
