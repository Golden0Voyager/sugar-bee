"""Apple Health iOS Shortcuts 数据同步接口。

通过 iOS 快捷指令将 Apple Health 数据写入 Sugar Bee。
"""
import datetime
import hmac
import io
import os
import plistlib
import secrets
import subprocess
import tempfile
import traceback
import uuid

from flask import Blueprint, request, send_file

from core import config as core_config
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.responses import api_error, api_success

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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
        traceback.print_exc()
        return api_error("解除绑定失败", status_code=500)


@bp_health_sync.route('/download_shortcut', methods=['GET'])
@login_required
def download_shortcut():
    """下载 iOS 捷径文件（动态生成，包含正确的服务器地址）。

    在 macOS 本地运行时会自动签名，生产环境返回未签名版本。
    """
    try:
        # 获取服务器地址（host_url 保留 scheme；生产环境经 ProxyFix 还原 https）
        base_url = request.host_url.rstrip('/')
        url = f'{base_url}/api/v1/health-sync/bind_from_shortcut'

        # 构建捷径 actions
        actions = [
            # 1. 获取剪贴板
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.getclipboard',
                'WFWorkflowActionParameters': {},
            },
            # 2. 设置变量 "BindCode"
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.setvariable',
                'WFWorkflowActionParameters': {
                    'WFVariableName': 'BindCode',
                },
            },
            # 3. 获取变量 "BindCode"（用于后续引用）
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.getvariable',
                'WFWorkflowActionParameters': {
                    'WFVariable': {
                        'Value': {'Type': 'Variable', 'VariableName': 'BindCode'},
                        'WFSerializationType': 'WFTextTokenAttachment',
                    },
                },
            },
            # 4. 文本 - JSON 模板（使用变量）
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.gettext',
                'WFWorkflowActionParameters': {
                    'WFTextActionText': {
                        'Value': {
                            'attachmentsByRange': {
                                '{8, 1}': {
                                    'Type': 'Variable',
                                    'VariableName': 'BindCode',
                                },
                            },
                            'string': '{"code": "\ufffc"}',
                        },
                        'WFSerializationType': 'WFTextTokenString',
                    },
                },
            },
            # 5. URL
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.url',
                'WFWorkflowActionParameters': {
                    'WFURLActionURL': url,
                },
            },
            # 6. 获取 URL 内容 (POST)
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.geturlcontent',
                'WFWorkflowActionParameters': {
                    'WFHTTPMethod': 'POST',
                    'WFHTTPBodyType': 'JSON',
                    'WFGetDictionaryValueType': 'Dictionary',
                    'WFHTTPHeaders': [],
                    'WFFormValues': {
                        'Value': {
                            'attachmentsByRange': {
                                '{0, 1}': {
                                    'Type': 'Variable',
                                    'VariableName': 'BindCode',
                                },
                            },
                            'string': '\ufffc',
                        },
                        'WFSerializationType': 'WFTextTokenString',
                    },
                },
            },
            # 7. 显示结果
            {
                'WFWorkflowActionIdentifier': 'is.workflow.actions.showresult',
                'WFWorkflowActionParameters': {},
            },
        ]

        shortcut_dict = {
            'WFWorkflowMinimumClientVersion': 900,
            'WFWorkflowMinimumClientVersionString': '900',
            'WFWorkflowClientVersion': '2612.0.4',
            'WFWorkflowHasShortcutInputVariables': False,
            'WFWorkflowIcon': {
                'WFWorkflowIconStartColor': 463140863,
                'WFWorkflowIconGlyphNumber': 59771,
            },
            'WFWorkflowImportQuestions': [],
            'WFWorkflowInputContentItemClasses': [],
            'WFWorkflowTypes': ['NCWidget', 'WatchKit', 'ActionExtension'],
            'WFWorkflowActions': actions,
            'WFWorkflowHasOutputFallback': False,
            'WFWorkflowName': 'Sugar Bee 绑定',
        }

        # 生成未签名的 plist 到临时文件
        with tempfile.NamedTemporaryFile(suffix='.shortcut', delete=False) as tmp_unsigned:
            plistlib.dump(shortcut_dict, tmp_unsigned, fmt=plistlib.FMT_BINARY)
            tmp_unsigned_path = tmp_unsigned.name

        try:
            # 尝试签名（仅 macOS 本地有效）
            tmp_signed_path = tmp_unsigned_path + '.signed'
            sign_result = subprocess.run(
                ['shortcuts', 'sign', '--mode', 'anyone',
                 '--input', tmp_unsigned_path, '--output', tmp_signed_path],
                capture_output=True, text=True, timeout=30,
            )

            if sign_result.returncode == 0 and os.path.exists(tmp_signed_path):
                # 签名成功，使用签名版本
                with open(tmp_signed_path, 'rb') as f:
                    signed_data = f.read()
                os.unlink(tmp_signed_path)
                return send_file(
                    io.BytesIO(signed_data),
                    mimetype='application/octet-stream',
                    as_attachment=True,
                    download_name='SugarBeeBind.shortcut',
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        finally:
            os.unlink(tmp_unsigned_path)

        # 签名失败或不可用，返回未签名版本
        buffer = io.BytesIO()
        plistlib.dump(shortcut_dict, buffer, fmt=plistlib.FMT_BINARY)
        buffer.seek(0)

        return send_file(
            buffer,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='SugarBeeBind.shortcut',
        )
    except Exception:
        traceback.print_exc()
        return api_error("生成捷径文件失败", status_code=500)
