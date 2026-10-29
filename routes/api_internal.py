"""内部 API 路由，仅供 Cloud Scheduler 等基础设施调用。

端点通过 Bearer Token 鉴权，token 由 INTERNAL_API_TOKEN 环境变量指定。
"""
from __future__ import annotations

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

    try:
        from services import garmin_service
        garmin_service.ensure_legacy_binding()
        results = garmin_service.sync_all_bindings(days=30)
        if not results:
            return api_success({'synced': False}, message='没有已绑定的 Garmin 用户，跳过同步')
        # 全部失败时保留旧的 503 + retry_after 语义，便于调度方重试；
        # 部分成功则返回 200 和各用户明细，单用户失败不拖垮其他人
        errors = [r['error'] for r in results.values() if isinstance(r, dict) and r.get('error')]
        if errors and len(errors) == len(results):
            err_str = errors[0]
            if '未找到 Garmin token' in err_str or '未绑定' in err_str:
                return api_error(err_str, status_code=503, error_type='garmin_auth', details={'retry_after': 300})
            if '过于频繁' in err_str:
                return api_error(err_str, status_code=503, error_type='garmin_rate_limit', details={'retry_after': 3600})
            if '超时' in err_str or '连接' in err_str:
                return api_error(err_str, status_code=503, error_type='garmin_transient', details={'retry_after': 120})
            return api_error(err_str, status_code=503, error_type='garmin_sync', details={'retry_after': 300})
        return api_success({'synced': True, 'results': results}, message='Garmin 同步完成')
    except RuntimeError as e:
        err_str = str(e)
        if '过于频繁' in err_str:
            return api_error(err_str, status_code=503, error_type='garmin_rate_limit', details={'retry_after': 3600})
        if '超时' in err_str or '连接' in err_str:
            return api_error(err_str, status_code=503, error_type='garmin_transient', details={'retry_after': 120})
        return api_error(f'Garmin 同步失败: {e}', status_code=503, error_type='garmin_sync', details={'retry_after': 300})
    except Exception as e:  # pragma: no cover
        return api_error(f'Garmin 同步失败: {e}', status_code=500, error_type='garmin_sync')
