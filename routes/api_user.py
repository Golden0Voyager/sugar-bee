import os
import re
import traceback

from flask import Blueprint, current_app, jsonify, request, session

import branding
import settings
from user_manager import UserManager
from utils.auth import admin_required, login_required
from utils.avatars import is_default_avatar
from utils.db import get_db
from utils.responses import api_error, api_success
from utils.timezone import now as app_now

# 不传 db_path：路径在每次访问时动态解析，测试改环境变量即可隔离
user_manager = UserManager()
bp_user = Blueprint('user', __name__)

ALL_MODULES = branding.ALL_MODULES

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

@bp_user.route('/switch_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def switch_user(user_id):
    try:
        user = user_manager.get_user(user_id)
        if not user:
            return api_error("用户不存在", status_code=404)

        if user_manager.has_password(user_id):
            data = request.json or {}
            password = data.get('password', '')
            db = get_db()
            c = db.cursor()
            c.execute("SELECT username FROM app_users WHERE id = ?", (user_id,))
            row = c.fetchone()
            if not row or not user_manager.authenticate(row['username'], password):
                return api_error("密码错误", status_code=401)

        session['current_user_id'] = user_id
        return api_success(data={"user": user}, message="User switched successfully")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)

@bp_user.route('/get_users', methods=['GET'])
@login_required
def get_users():
    try:
        users = user_manager.get_all_users()
        for u in users:
            u['has_password'] = user_manager.has_password(u['id'])
        return jsonify(users)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_user.route('/get_current_user', methods=['GET'])
@login_required
def get_current_user_api():
    try:
        user_id = user_manager.get_current_user_id()
        user = user_manager.get_user(user_id)
        return jsonify(user) if user else jsonify({'id': 1, 'username': 'default'})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_user.route('/create_user', methods=['POST'])
@login_required
def create_user():
    try:
        data = request.json
        username = data.get('username', '').strip()
        display_name = data.get('display_name', '').strip()
        password = data.get('password', '')
        if not username or not display_name or not password:
            return api_error("Username, display name and password are required", status_code=400)

        user_id = user_manager.create_user(username, display_name, {}, password=password)
        return api_success(data={"id": user_id}, message="User created")
    except Exception as e:
        return api_error(str(e), status_code=500)

@bp_user.route('/settings', methods=['GET'])
@login_required
def get_settings():
    return jsonify(settings.load_config(user_manager.get_current_user_id()))

@bp_user.route('/settings', methods=['POST'])
@login_required
def update_settings():
    try:
        new_config = request.json or {}
        user_manager.update_user_profile_partial(
            user_manager.get_current_user_id(), new_config
        )
        return api_success(message="Settings updated")
    except Exception as e:
        return api_error(str(e), status_code=500)

@bp_user.route('/api/user/modules', methods=['GET'])
@login_required
def get_user_modules():
    """返回当前用户的启用模块"""
    try:
        user_id = user_manager.get_current_user_id()
        user = user_manager.get_user(user_id)
        modules = (user.get('enabled_modules') if user else None) or ALL_MODULES
        return api_success(data={
            'enabled_modules': modules,
            'all_modules': ALL_MODULES
        })
    except Exception as e:
        return api_error(str(e), status_code=500)

@bp_user.route('/api/user/modules', methods=['POST'])
@login_required
def update_user_modules():
    """更新当前用户的启用模块。

    注意：`enabled_modules` 存空数组的语义是「不限制 = 全部启用」，和「一个都不要」
    正好相反。所以这里必须挡住空数组——否则前端传 [] 想关掉所有模块，读回来却是
    全部模块都开着。校验规则和 `/api/user/branding` 保持一致。
    """
    try:
        data = request.json or {}
        modules = data.get('enabled_modules', [])
        if not isinstance(modules, list):
            return api_error("enabled_modules 必须是数组", status_code=400, error_type='validation_error')
        valid = [m for m in modules if m in ALL_MODULES]
        if not valid:
            return api_error("请至少保留一个功能模块", status_code=400, error_type='validation_error')
        user_manager.set_enabled_modules(user_manager.get_current_user_id(), valid)
        return api_success(data={'enabled_modules': valid}, message="模块设置已更新")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)

@bp_user.route('/api/user/branding', methods=['GET'])
@login_required
def get_user_branding():
    """返回当前用户的应用品牌配置 + 可选场景模板（首次引导与设置页共用）"""
    try:
        user = user_manager.get_user(user_manager.get_current_user_id()) or {}
        return api_success(data={
            'branding': branding.resolve_branding(user),
            'personas': branding.persona_options(),
            'all_modules': ALL_MODULES,
            'limits': {
                'name_max_len': branding.NAME_MAX_LEN,
                'tagline_max_len': branding.TAGLINE_MAX_LEN,
            },
        })
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_user.route('/api/user/branding', methods=['POST'])
@login_required
def update_user_branding():
    """保存应用名称 / 图标 / 标语 / 使用场景（可同时提交模块开关）"""
    try:
        payload, error = branding.build_branding_payload(request.json or {})
        if error:
            return api_error(error, status_code=400, error_type='validation_error')
        user_id = user_manager.get_current_user_id()
        user_manager.update_user_profile_partial(user_id, payload)
        # 保存后回读整条档案再解析：payload 里没有的字段（比如没提交 enabled_modules）
        # 若直接拿来 resolve，会被当成「空 = 未限制」，响应里的模块列表就是假的。
        saved = user_manager.get_user(user_id) or {}
        return api_success(
            data={'branding': branding.resolve_branding(saved)},
            message='应用名称已更新',
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_user.route('/select_avatar', methods=['POST'])
@login_required
def select_avatar():
    try:
        avatar = (request.json or {}).get('avatar')
        if not is_default_avatar(avatar):
            return api_error('请选择有效的默认头像', status_code=400, error_type='validation_error')

        user_id = user_manager.get_current_user_id()
        db = get_db()
        db.execute('UPDATE app_users SET avatar = ? WHERE id = ?', (avatar, user_id))
        db.commit()
        return api_success(
            data={'avatar': avatar, 'avatar_url': f'/static/avatars/{avatar}'},
            message='头像已更新',
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_user.route('/upload_avatar', methods=['POST'])
@login_required
def upload_avatar():
    try:
        file = request.files.get('avatar')
        if not file or file.filename == '' or not allowed_file(file.filename):
            return api_error("Invalid file", status_code=400)

        filename = f"avatar_{int(app_now().timestamp())}.png"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute("UPDATE app_users SET avatar = ? WHERE id = ?", (filename, user_id))
        db.commit()

        return api_success(
            data={"avatar_url": f"/static/avatars/{filename}", "avatar": filename},
            message="Avatar uploaded"
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_user.route('/change_username', methods=['POST'])
@login_required
def change_username():
    """修改用户名（已登录用户）"""
    data = request.json
    new_username = data.get('new_username', '').strip()

    if not new_username:
        return api_error('用户名不能为空', status_code=400, error_type='validation_error')

    existing = user_manager.get_user_by_username(new_username)
    user_id = user_manager.get_current_user_id()
    if existing and existing['id'] != user_id:
        return api_error('用户名已存在', status_code=400, error_type='validation_error')

    user_manager.change_username(user_id, new_username)
    return api_success(message='用户名修改成功')


@bp_user.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    """删除用户（软删除）"""
    try:
        current_id = user_manager.get_current_user_id()
        if user_id == current_id:
            return api_error("不能删除当前登录用户", status_code=400, error_type="validation_error")

        db = get_db()
        c = db.cursor()
        c.execute("SELECT id FROM app_users WHERE id = ? AND is_active = 1", (user_id,))
        if not c.fetchone():
            return api_error("用户不存在", status_code=404, error_type="not_found")

        c.execute("UPDATE app_users SET is_active = 0 WHERE id = ?", (user_id,))
        db.commit()
        return api_success(message="用户已删除")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500, error_type="user_error")


@bp_user.route('/get_user_providers', methods=['GET'])
@login_required
def get_user_providers():
    """获取当前用户已绑定的账号"""
    user_id = user_manager.get_current_user_id()
    providers = user_manager.get_user_providers(user_id)
    return api_success(data=providers)


@bp_user.route('/bind_phone', methods=['POST'])
@login_required
def bind_phone():
    """绑定手机号"""
    data = request.json
    phone = data.get('phone', '').strip()
    if not re.match(r'^1[3-9]\d{9}$', phone):
        return api_error('手机号格式不正确', status_code=400, error_type='validation_error')
    user_id = user_manager.get_current_user_id()
    result = user_manager.bind_provider(user_id, 'phone', phone)
    if not result['ok']:
        return api_error(result['message'], status_code=400, error_type='validation_error')
    return api_success(message='手机号绑定成功')


@bp_user.route('/bind_email', methods=['POST'])
@login_required
def bind_email():
    """绑定邮箱"""
    data = request.json
    email = data.get('email', '').strip()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return api_error('邮箱格式不正确', status_code=400, error_type='validation_error')
    user_id = user_manager.get_current_user_id()
    result = user_manager.bind_provider(user_id, 'email', email)
    if not result['ok']:
        return api_error(result['message'], status_code=400, error_type='validation_error')
    return api_success(message='邮箱绑定成功')


@bp_user.route('/unbind_provider', methods=['POST'])
@login_required
def unbind_provider():
    """解绑手机号/邮箱"""
    data = request.json
    provider = data.get('provider', '').strip()
    if provider not in ('phone', 'email'):
        return api_error('不支持的解绑类型', status_code=400, error_type='validation_error')
    user_id = user_manager.get_current_user_id()
    user_manager.unbind_provider(user_id, provider)
    return api_success(message='解绑成功')


@bp_user.route('/sync_garmin', methods=['POST'])
@login_required
def sync_garmin():
    """手动触发 Garmin 同步 — 仅允许已绑定 Garmin 的用户调用，数据只进本人账号"""
    current_uid = user_manager.get_current_user_id()
    from services import garmin_service
    if not garmin_service.has_garmin_binding(current_uid):
        return api_error("当前用户未绑定 Garmin 账号", status_code=403)
    try:
        days = garmin_service.get_sync_days(current_uid)
        result = garmin_service.sync_activities(current_uid, days=days)
        return api_success(
            data=result,
            message=f"同步完成（近 {days} 天）：新增 {result['inserted']} 条，跳过 {result['skipped']} 条"
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(f"Garmin 同步失败：{e}", status_code=500)


@bp_user.route('/api/garmin/status', methods=['GET'])
@login_required
def garmin_status():
    """当前用户的 Garmin 绑定状态（只返回本人信息）。"""
    from services import garmin_service
    uid = user_manager.get_current_user_id()
    account = garmin_service._account_for(uid)
    return api_success(data={
        'bound': bool(account),
        'email': account['email'] if account else '',
        'is_cn': account['is_cn'] if account else False,
    })


@bp_user.route('/api/garmin/bind', methods=['POST'])
@login_required
def garmin_bind():
    """绑定当前用户的 Garmin 账号：服务端登录成功后只保存 token，不存密码。"""
    from services import garmin_service
    data = request.get_json(silent=True) or {}
    email = str(data.get('email') or '').strip()
    password = str(data.get('password') or '')
    is_cn = bool(data.get('is_cn'))
    if not email or not password:
        return api_error('请填写 Garmin 账号和密码', status_code=400, error_type='validation_error')
    if len(email) > 120 or len(password) > 120:
        return api_error('账号或密码过长', status_code=400, error_type='validation_error')
    uid = user_manager.get_current_user_id()
    try:
        result = garmin_service.bind_account(uid, email, password, is_cn)
        return api_success(data=result, message='Garmin 绑定成功')
    except RuntimeError as e:
        return api_error(str(e), status_code=400, error_type='garmin_auth')
    except Exception as e:
        traceback.print_exc()
        return api_error(f'Garmin 绑定失败：{e}', status_code=500)


@bp_user.route('/api/garmin/unbind', methods=['POST'])
@login_required
def garmin_unbind():
    """解绑当前用户的 Garmin 账号，删除本人 token，不影响其他用户。"""
    from services import garmin_service
    uid = user_manager.get_current_user_id()
    try:
        garmin_service.unbind_account(uid)
    except RuntimeError as e:
        return api_error(str(e), status_code=409, error_type='garmin_managed')
    return api_success(message='已解绑 Garmin 账号')
