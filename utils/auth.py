from functools import wraps
from flask import session, request, jsonify, redirect, url_for

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
