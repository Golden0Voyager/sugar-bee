from flask import Blueprint, request, session, redirect, url_for, render_template
import re
import sqlite3
from werkzeug.security import check_password_hash, generate_password_hash

from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.auth import login_required

bp_auth = Blueprint('auth', __name__)
user_manager = UserManager(DB_NAME)


@bp_auth.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'GET':
        if 'current_user_id' in session:
            return redirect(url_for('index'))
        return render_template('login.html', error=None, success=None, set_password_mode=False)

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    if not username:
        return render_template('login.html', error='请输入用户名 / 手机号 / 邮箱', set_password_mode=False)

    user_info = None
    if re.match(r'^1[3-9]\d{9}$', username):
        found_uid = user_manager.find_user_by_provider('phone', username)
        if found_uid:
            user_info = user_manager.get_user_by_username_or_id(found_uid)
    elif '@' in username:
        found_uid = user_manager.find_user_by_provider('email', username)
        if found_uid:
            user_info = user_manager.get_user_by_username_or_id(found_uid)

    if not user_info:
        user_info = user_manager.get_user_by_username(username)
    if not user_info:
        return render_template('login.html', error='账号不存在', set_password_mode=False)

    if not user_info.get('password_hash'):
        return render_template('login.html', set_password_mode=True, username=username)

    if check_password_hash(user_info['password_hash'], password):
        session['current_user_id'] = user_info['id']
        session['username'] = user_info['username']
        return redirect(url_for('index'))
    else:
        return render_template('login.html', error='密码错误', set_password_mode=False)

@bp_auth.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))

@bp_auth.route('/set_password', methods=['POST'])
def set_password():
    username = request.form.get('username')
    password = request.form.get('password')
    if not username or not password:
        return render_template('login.html', error='参数错误', set_password_mode=False)
    
    user = user_manager.get_user_by_username(username)
    if not user:
        return render_template('login.html', error='用户不存在', set_password_mode=False)
    
    pw_hash = generate_password_hash(password)
    db = sqlite3.connect(DB_NAME)
    db.execute("UPDATE app_users SET password_hash = ? WHERE id = ?", (pw_hash, user['id']))
    db.commit()
    db.close()
    
    return render_template('login.html', success='密码设置成功，请登录', set_password_mode=False)


@bp_auth.route('/change_password', methods=['POST'])
@login_required
def change_password():
    """修改密码（已登录用户）"""
    data = request.json
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not new_password or len(new_password) < 4:
        return api_error('新密码至少需要4个字符', status_code=400, error_type='validation_error')

    user_id = user_manager.get_current_user_id()
    user = user_manager.get_user(user_id)
    if not user:
        return api_error('用户不存在', status_code=404)

    # 如果已有密码，需要验证旧密码
    if user_manager.has_password(user_id):
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT username FROM app_users WHERE id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        if not row or not user_manager.authenticate(row['username'], old_password):
            return api_error('旧密码错误', status_code=400, error_type='auth_error')

    user_manager.set_password(user_id, new_password)
    return api_success(message='密码修改成功')
