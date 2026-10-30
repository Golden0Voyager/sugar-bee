import contextlib
import importlib.util
import os
import shutil
import sqlite3
import traceback

from flask import Blueprint, after_this_request, request, send_file

from core.config import DB_TYPE, get_db_name
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.responses import api_error, api_success
from utils.sql_dialect import date_format_sql, group_concat_sql
from utils.timezone import now as app_now

# 获取项目根目录 (app.py 所在目录)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# 唯一可信备份实现的位置（镜像内 /app/scripts/ops/，宿主机 /opt/sugar_bee/）
BACKUP_MODULE_PATH = os.path.join(BASE_DIR, 'scripts', 'ops', 'backup_db.py')

bp_admin = Blueprint('admin', __name__)


def _load_backup_module():
    """按文件路径加载 scripts/ops/backup_db.py（宿主机 cron 用的同一份实现）。"""
    if not os.path.isfile(BACKUP_MODULE_PATH):
        return None
    spec = importlib.util.spec_from_file_location('sb_backup_db', BACKUP_MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 极旧 python
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_safe_backup(dest_path):
    """用 Online Backup API 生成快照（禁止 shutil.copy2 直拷活库）。

    与 cron 快照共用一份实现，避免"下载的备份"和"每日快照"可信度不一致。
    """
    module = _load_backup_module()
    if module is None:
        raise RuntimeError(f'备份模块缺失: {BACKUP_MODULE_PATH}')
    return module.make_backup(dest_path=dest_path, src_path=get_db_name())


def _remove_wal_sidecars(db_path):
    """删掉 -wal / -shm 残留。

    WAL 模式下只换主文件、留着旧 -wal，启动时会把旧事务回放回来
    （= 恢复成一半新一半旧）。恢复规范序：先删残留，再换主文件。
    """
    for suffix in ('-wal', '-shm'):
        sidecar = db_path + suffix
        if os.path.exists(sidecar):
            # 文件被占用时删不掉，只能留给人工 —— 但不该因此让恢复失败
            with contextlib.suppress(OSError):
                os.remove(sidecar)


@bp_admin.route('/backup_database', methods=['GET'])
@login_required
def backup_database():
    """备份数据库 - 下载 .db 文件"""
    try:
        if DB_TYPE == 'postgres':
            return api_error("PostgreSQL 模式下请使用 Cloud SQL 托管备份", status_code=400)
        db_path = get_db_name()
        if not os.path.exists(db_path):
            return api_error("数据库文件不存在", status_code=404)

        timestamp = app_now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'glucose_backup_{timestamp}.db'
        backup_path = os.path.join(BASE_DIR, backup_filename)

        # Online Backup API 快照（旧实现是 shutil.copy2 直拷活库，可能撕裂）
        make_safe_backup(backup_path)

        @after_this_request
        def cleanup(response):
            try:
                if os.path.exists(backup_path):
                    os.remove(backup_path)
            except Exception:
                pass
            return response

        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_filename,
            mimetype='application/x-sqlite3'
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)

@bp_admin.route('/restore_database', methods=['POST'])
@login_required
def restore_database():
    """恢复数据库 - 上传 .db 文件覆盖当前数据库"""
    try:
        if DB_TYPE == 'postgres':
            return api_error("PostgreSQL 模式下请使用 Cloud SQL 托管备份", status_code=400)
        if 'file' not in request.files:
            return api_error("未选择文件", status_code=400)

        file = request.files['file']
        if file.filename == '' or not file.filename.endswith('.db'):
            return api_error("无效的文件格式", status_code=400)

        # 保存到临时文件进行验证
        temp_path = os.path.join(BASE_DIR, 'temp_restore.db')
        file.save(temp_path)

        if os.path.getsize(temp_path) == 0:
            os.remove(temp_path)
            return api_error("上传的文件为空", status_code=400)

        # 验证是否为有效的数据库
        try:
            conn = sqlite3.connect(temp_path)
            cursor = conn.cursor()
            check = [r[0] for r in cursor.execute('PRAGMA quick_check').fetchall()]
            if check != ['ok']:
                raise ValueError(f'quick_check failed: {check[:3]}')
            cursor.execute("SELECT count(*) FROM records")
            cursor.close()
            conn.close()
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return api_error("无效的数据库文件", status_code=400)

        # 备份当前数据库作为安全网（同样走 Online Backup API，不留撕裂副本）
        db_path = get_db_name()
        if os.path.exists(db_path):
            make_safe_backup(db_path + '.before_restore')

        # 恢复规范序：先删 -wal/-shm 残留，再换主文件
        _remove_wal_sidecars(db_path)
        shutil.move(temp_path, db_path)
        return api_success(message='数据库恢复成功，请刷新页面')
    except Exception as e:
        traceback.print_exc()
        temp_path = os.path.join(BASE_DIR, 'temp_restore.db')
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return api_error(str(e), status_code=500)


# 不传 db_path：路径在每次访问时动态解析，测试改环境变量即可隔离
user_manager = UserManager()


@bp_admin.route('/find_duplicates', methods=['GET'])
@login_required
def find_duplicates():
    """查找重复的记录"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        c.execute("""
            SELECT
                {} as time_key,
                type,
                value,
                COUNT(*) as count,
                {} as ids
            FROM records
            WHERE user_id = ?
            GROUP BY time_key, type, value
            HAVING count > 1
            ORDER BY timestamp DESC
        """.format(date_format_sql('timestamp', '%Y-%m-%d %H:%M'), group_concat_sql('id')), (current_user_id,))

        duplicates = []
        for row in c.fetchall():
            time_key, record_type, value, count, ids = row
            id_list = [int(id) for id in ids.split(',')]

            duplicates.append({
                'time': time_key,
                'type': record_type,
                'value': value,
                'count': count,
                'ids': id_list
            })

        return api_success(data={
            'duplicates': duplicates,
            'total_groups': len(duplicates)
        })
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500, error_type="query_error")


@bp_admin.route('/delete_duplicates', methods=['POST'])
@login_required
def delete_duplicates():
    """删除重复的记录，保留每组中的第一条"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        c.execute("""
            SELECT
                {} as time_key,
                type,
                value,
                {} as ids
            FROM records
            WHERE user_id = ?
            GROUP BY time_key, type, value
            HAVING COUNT(*) > 1
        """.format(date_format_sql('timestamp', '%Y-%m-%d %H:%M'), group_concat_sql('id')), (current_user_id,))

        deleted_count = 0
        for row in c.fetchall():
            time_key, record_type, value, ids = row
            id_list = [int(id) for id in ids.split(',')]

            ids_to_delete = id_list[1:]
            for id_to_delete in ids_to_delete:
                c.execute("DELETE FROM records WHERE id = ? AND user_id = ?", (id_to_delete, current_user_id))
                deleted_count += 1

        db.commit()

        return api_success(
            data={'deleted_count': deleted_count},
            message=f'已删除 {deleted_count} 条重复记录'
        )
    except Exception as e:
        traceback.print_exc()
        db.rollback()
        return api_error(str(e), status_code=500, error_type="delete_error")
