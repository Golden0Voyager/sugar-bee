from flask import Blueprint, request, jsonify, send_file, after_this_request, session
import os
import datetime
import shutil
import sqlite3
import traceback

from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.auth import login_required
from utils.db import get_db

# 获取项目根目录 (app.py 所在目录)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

bp_admin = Blueprint('admin', __name__)

@bp_admin.route('/backup_database', methods=['GET'])
@login_required
def backup_database():
    """备份数据库 - 下载 .db 文件"""
    try:
        if not os.path.exists(DB_NAME):
            return api_error("数据库文件不存在", status_code=404)

        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'glucose_backup_{timestamp}.db'
        backup_path = os.path.join(BASE_DIR, backup_filename)

        shutil.copy2(DB_NAME, backup_path)

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
            cursor.execute("SELECT count(*) FROM records")
            cursor.close()
            conn.close()
        except Exception:
            if os.path.exists(temp_path): os.remove(temp_path)
            return api_error("无效的数据库文件", status_code=400)

        # 备份当前数据库作为安全网
        if os.path.exists(DB_NAME):
            shutil.copy2(DB_NAME, DB_NAME + '.before_restore')

        # 覆盖当前数据库
        shutil.move(temp_path, DB_NAME)
        return api_success(message='数据库恢复成功，请刷新页面')
    except Exception as e:
        traceback.print_exc()
        temp_path = os.path.join(BASE_DIR, 'temp_restore.db')
        if os.path.exists(temp_path): os.remove(temp_path)
        return api_error(str(e), status_code=500)


user_manager = UserManager(DB_NAME)


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
                strftime('%Y-%m-%d %H:%M', timestamp) as time_key,
                type,
                value,
                COUNT(*) as count,
                GROUP_CONCAT(id) as ids
            FROM records
            WHERE user_id = ?
            GROUP BY time_key, type, value
            HAVING count > 1
            ORDER BY timestamp DESC
        """, (current_user_id,))

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
                strftime('%Y-%m-%d %H:%M', timestamp) as time_key,
                type,
                value,
                GROUP_CONCAT(id ORDER BY id) as ids
            FROM records
            WHERE user_id = ?
            GROUP BY time_key, type, value
            HAVING COUNT(*) > 1
        """, (current_user_id,))

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
