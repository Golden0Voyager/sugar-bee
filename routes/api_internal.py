"""内部 API 路由，仅供 Cloud Scheduler 等基础设施调用。

端点通过 Bearer Token 鉴权，token 由 INTERNAL_API_TOKEN 环境变量指定。
"""
from __future__ import annotations

import os

from flask import Blueprint, request

from core.config import DB_TYPE, INTERNAL_API_TOKEN
from services.gcs_sync import backup_db_to_gcs
from utils.responses import api_error, api_success

bp_internal = Blueprint('internal', __name__)


def _check_internal_auth() -> tuple[bool, tuple | None]:
    """校验内部调用鉴权。"""
    if not INTERNAL_API_TOKEN:
        return False, api_error(
            'INTERNAL_API_TOKEN not configured',
            status_code=503,
            error_type='internal_auth',
        )
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != INTERNAL_API_TOKEN:
        return False, api_error('Unauthorized', status_code=401, error_type='internal_auth')
    return True, None


@bp_internal.route('/internal/backup', methods=['POST'])
def internal_backup():
    """触发数据库备份到 GCS。"""
    ok, error_response = _check_internal_auth()
    if not ok:
        return error_response

    if DB_TYPE != 'sqlite':
        return api_success(
            {'backed_up': False},
            message='PostgreSQL 模式使用 Cloud SQL 自动备份，无需 GCS 数据库备份',
        )

    try:
        backup_db_to_gcs()
        return api_success({'backed_up': True}, message='备份已触发')
    except Exception as e:  # pragma: no cover
        return api_error(f'备份失败: {e}', status_code=500, error_type='backup')


@bp_internal.route('/internal/garmin-sync', methods=['POST'])
def internal_garmin_sync():
    """触发 Garmin 数据同步。"""
    ok, error_response = _check_internal_auth()
    if not ok:
        return error_response

    user_id = int(os.environ.get('GARMIN_USER_ID', 0) or 0)
    if not user_id or not os.environ.get('GARMIN_EMAIL'):
        return api_success({'synced': False}, message='未配置 Garmin，跳过同步')

    token_dir = os.environ.get('GARMIN_TOKEN_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.garmin_tokens'))
    token_file = os.path.join(token_dir, 'garmin_tokens.json')
    if not os.path.isfile(token_file):
        return api_error(
            'Garmin token 不存在，请先手动登录',
            status_code=503,
            error_type='garmin_auth',
        )

    try:
        from services.garmin_service import sync_activities
        result = sync_activities(user_id, days=30)
        return api_success({'synced': True, 'result': result}, message='Garmin 同步完成')
    except Exception as e:  # pragma: no cover
        return api_error(f'Garmin 同步失败: {e}', status_code=500, error_type='garmin_sync')
