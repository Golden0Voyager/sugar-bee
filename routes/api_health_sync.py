"""Apple Health iOS Shortcuts 数据同步接口。

通过 iOS 快捷指令将 Apple Health 数据写入 Sugar Bee。
"""
import datetime
import random
import secrets
import traceback
import uuid

from flask import Blueprint, request

from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.db import get_db
from utils.auth import login_required

user_manager = UserManager(DB_NAME)

bp_health_sync = Blueprint('health_sync', __name__, url_prefix='/api/v1/health-sync')


def _get_bind_code() -> str:
    """生成 6 位数字绑定码。"""
    return str(random.randint(100000, 999999))


def _generate_device_token() -> str:
    """生成 32 字节随机设备令牌（URL-safe base64，约 43 字符）。"""
    return secrets.token_urlsafe(32)


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
            'expires_in': 1800,
        })
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


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
        return api_error(str(e), status_code=500)