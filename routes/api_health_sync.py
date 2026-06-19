"""Apple Health iOS Shortcuts 数据同步接口。

通过 iOS 快捷指令将 Apple Health 数据写入 Sugar Bee。
"""
import datetime
import hmac
import secrets
import traceback
import uuid

from flask import Blueprint, request

from user_manager import UserManager
from core import config as core_config
from utils.responses import api_success, api_error
from utils.db import get_db
from utils.auth import login_required

user_manager = UserManager(core_config.DB_NAME)

bp_health_sync = Blueprint('health_sync', __name__, url_prefix='/api/v1/health-sync')

# 绑定码过期时间：30 分钟
BIND_CODE_EXPIRY_SECONDS = 1800

# Apple Health 数据源标识（用于去重）
SOURCE_APPLE_HEALTH = 'apple_health'


def _get_bind_code() -> str:
    """生成 6 位数字绑定码（使用密码学安全随机数）。"""
    return f"{secrets.randbelow(900000) + 100000}"


def _generate_device_token() -> str:
    """生成 32 字节随机设备令牌（URL-safe base64，约 43 字符）。"""
    return secrets.token_urlsafe(32)


def _verify_device_auth(device_id: str, device_token: str) -> int | None:
    """验证 device_id + device_token，返回绑定的 user_id 或 None。

    使用 HMAC 常量时间比较防计时攻击。
    """
    try:
        db = get_db()
        c = db.cursor()
        c.execute(
            "SELECT user_id, device_token FROM device_bindings "
            "WHERE device_id = ? AND device_token IS NOT NULL",
            (device_id,),
        )
        row = c.fetchone()
        if row and hmac.compare_digest(row['device_token'], device_token):
            return row['user_id']
    except Exception:
        pass
    return None


@bp_health_sync.route('/bind', methods=['POST'])
@login_required
def bind_device():
    """Step 1: 用户从 Sugar Bee 设置页发起绑定 → 生成绑定码。

    请求: POST /api/v1/health-sync/bind
    请求体: {}
    响应: {"status": "success", "data": {"bind_code": "123456", "expires_in": 1800}}
    """
    try:
        current_user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()

        # 清除该用户的过期绑定码
        c.execute(
            "DELETE FROM device_bindings WHERE user_id = ? AND "
            "bind_code IS NOT NULL AND code_expires_at < datetime('now')",
            (current_user_id,),
        )

        # 生成新绑定码（30 分钟过期）
        bind_code = _get_bind_code()
        expires_at = (datetime.datetime.now() + datetime.timedelta(minutes=30)).isoformat()

        c.execute(
            "INSERT INTO device_bindings (user_id, bind_code, code_expires_at) VALUES (?, ?, ?)",
            (current_user_id, bind_code, expires_at),
        )
        db.commit()

        return api_success(data={
            'bind_code': bind_code,
            'expires_in': BIND_CODE_EXPIRY_SECONDS,
        })
    except Exception as e:
        traceback.print_exc()
        return api_error("绑定码生成失败", status_code=500)


@bp_health_sync.route('/bind_from_shortcut', methods=['POST'])
def bind_from_shortcut():
    """iOS 捷径携带绑定码调用此端点完成绑定。

    请求: POST /api/v1/health-sync/bind_from_shortcut
    请求体: {"code": "123456", "device_name": "iPhone 15"}
    响应: {"status": "success", "data": {"device_id": "uuid", "device_token": "token"}}
    """
    try:
        data = request.get_json(force=True)
        if not data:
            return api_error("请求体为空")
        code = data.get('code', '').strip()
        device_name = (data.get('device_name') or 'Unknown Device').strip()

        if not code or not code.isdigit() or len(code) != 6:
            return api_error("无效的绑定码格式", status_code=400)

        db = get_db()
        c = db.cursor()

        # 查找有效绑定码（30 分钟内未过期、未使用）
        c.execute(
            "SELECT id, user_id FROM device_bindings "
            "WHERE bind_code = ? AND device_id IS NULL "
            "AND code_expires_at >= datetime('now') "
            "ORDER BY created_at DESC LIMIT 1",
            (code,),
        )
        row = c.fetchone()
        if not row:
            return api_error("绑定码无效或已过期", status_code=404)

        binding_id = row['id']
        user_id = row['user_id']

        # 生成 device_id + device_token
        device_id = str(uuid.uuid4())
        device_token = _generate_device_token()
        now = datetime.datetime.now().isoformat()

        c.execute(
            "UPDATE device_bindings SET device_id = ?, device_token = ?, "
            "device_name = ?, bound_at = ?, bind_code = NULL, code_expires_at = NULL "
            "WHERE id = ? AND device_id IS NULL",
            (device_id, device_token, device_name, now, binding_id),
        )
        if c.rowcount == 0:
            db.commit()
            return api_error("绑定冲突，请重新生成绑定码", status_code=409)
        db.commit()

        return api_success(data={
            'device_id': device_id,
            'device_token': device_token,
        })
    except Exception as e:
        traceback.print_exc()
        return api_error("设备绑定失败", status_code=500)


@bp_health_sync.route('/confirm_binding', methods=['GET'])
@login_required
def confirm_binding():
    """查询当前用户的绑定状态。"""
    try:
        current_user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute(
            "SELECT device_id, device_name, bound_at FROM device_bindings "
            "WHERE user_id = ? AND device_id IS NOT NULL AND device_token IS NOT NULL "
            "ORDER BY bound_at DESC LIMIT 1",
            (current_user_id,),
        )
        row = c.fetchone()
        if row:
            return api_success(data={
                'device_id': row['device_id'],
                'device_name': row['device_name'],
                'bound_at': row['bound_at'],
            })
        return api_success(data={'device_id': None})
    except Exception as e:
        traceback.print_exc()
        return api_error("查询绑定状态失败", status_code=500)


@bp_health_sync.route('/sync', methods=['POST'])
def sync_health_data():
    """iOS 捷径同步 Apple Health 数据。

    请求头: X-Device-Id, X-Device-Token
    请求体: {"records": [...]}
    记录字段: type, value, unit, timestamp
    响应: {"status": "success", "data": {"inserted": N, "skipped": M}}

    去重: 每条记录设 external_id = "apple_health:<uuid>" + source = "apple_health"，
          写入前检查是否已存在。
    """
    try:
        # 1. 设备鉴权
        device_id = request.headers.get('X-Device-Id', '')
        device_token = request.headers.get('X-Device-Token', '')
        if not device_id or not device_token:
            return api_error("缺少设备鉴权信息", status_code=401)

        user_id = _verify_device_auth(device_id, device_token)
        if user_id is None:
            return api_error("设备鉴权失败", status_code=401)

        # 2. 解析请求体
        data = request.get_json(force=True)
        if not data:
            return api_error("请求体为空")
        records = data.get('records', [])
        if not records:
            return api_error("records 列表为空")

        db = get_db()
        c = db.cursor()
        inserted = 0
        skipped = 0

        # 3. 逐条写入（去重基于 external_id + source）
        for r in records:
            r_type = r.get('type', '')
            r_value = r.get('value')
            r_timestamp = r.get('timestamp')

            if not r_type or r_value is None or not r_timestamp:
                skipped += 1
                continue

            # 生成去重 ID（如果 iOS 捷径未提供则自动生成）
            r_external_id = r.get('external_id', '') or f"apple_health:{uuid.uuid4()}"

            # 检查重复
            c.execute(
                "SELECT id FROM records WHERE external_id = ? AND source = ?",
                (r_external_id, SOURCE_APPLE_HEALTH),
            )
            if c.fetchone():
                skipped += 1
                continue

            # 血压特殊处理：拆分为收缩压和舒张压
            if r_type == '血压收缩压':
                c.execute(
                    "INSERT INTO records (user_id, type, value, unit, timestamp, "
                    "systolic_pressure, external_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, '血压', r_value, r.get('unit', 'mmHg'), r_timestamp,
                     r_value, r_external_id, SOURCE_APPLE_HEALTH),
                )
            elif r_type == '血压舒张压':
                c.execute(
                    "INSERT INTO records (user_id, type, value, unit, timestamp, "
                    "diastolic_pressure, external_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, '血压', r_value, r.get('unit', 'mmHg'), r_timestamp,
                     r_value, r_external_id, SOURCE_APPLE_HEALTH),
                )
            else:
                c.execute(
                    "INSERT INTO records (user_id, type, value, unit, timestamp, "
                    "external_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, r_type, r_value, r.get('unit', ''), r_timestamp,
                     r_external_id, SOURCE_APPLE_HEALTH),
                )
            inserted += 1

        db.commit()
        return api_success(data={'inserted': inserted, 'skipped': skipped})
    except Exception as e:
        traceback.print_exc()
        return api_error("数据同步失败", status_code=500)


@bp_health_sync.route('/unbind', methods=['POST'])
@login_required
def unbind_device():
    """解除当前用户的所有设备绑定。"""
    try:
        current_user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute(
            "DELETE FROM device_bindings WHERE user_id = ? AND device_id IS NOT NULL",
            (current_user_id,),
        )
        db.commit()
        return api_success(message="绑定已解除")
    except Exception as e:
        traceback.print_exc()
        return api_error("解除绑定失败", status_code=500)