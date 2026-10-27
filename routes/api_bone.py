"""骨密度记录 API（模块 bone_density）。

低频检查（半年到一年一次），接口只做增删查与报告识别，不做预测。
"""
import traceback

from flask import Blueprint, request

import bone_density
import bone_parser
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.report_files import ReportFileError, to_model_images
from utils.responses import api_error, api_success
from utils.timezone import today as app_today

bp_bone = Blueprint('bone', __name__)
user_manager = UserManager()


def _rows_for_user(db, user_id):
    c = db.cursor()
    c.execute("""
        SELECT id, measure_date, site, t_score, z_score, bmd, institution, notes, created_at
        FROM bone_density_records
        WHERE user_id = ?
        ORDER BY measure_date ASC, id ASC
    """, (user_id,))
    return [dict(r) for r in c.fetchall()]


@bp_bone.route('/api/bone', methods=['GET'])
@login_required
def list_bone_records():
    """返回当前用户的骨密度记录、最新一次、趋势序列与可选部位。"""
    try:
        user_id = user_manager.get_current_user_id()
        rows = _rows_for_user(get_db(), user_id)
        return api_success(data=bone_density.build_summary(rows))
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_bone.route('/api/bone/parse', methods=['POST'])
@login_required
def parse_bone_image():
    """识别骨密度报告（照片或 PDF，最多 3 页），返回多条测量值（不落库，等用户确认）。"""
    try:
        data = request.json or {}
        images_b64 = data.get('images') or []
        if not images_b64:
            return api_error('请上传骨密度报告照片或 PDF', status_code=400, error_type='validation_error')

        try:
            images_data = to_model_images(images_b64)
        except ReportFileError as exc:
            return api_error(str(exc), status_code=400, error_type='validation_error')

        result = bone_parser.parse_bone_report(images_data, 'image/jpeg')
        if not result['measurements']:
            return api_error(
                '没有从报告里识别出 T 值，可以换一张更清晰的照片，或手工录入',
                status_code=422, error_type='parse_empty',
            )
        if not result['measure_date']:
            result['measure_date'] = app_today().strftime('%Y-%m-%d')
        return api_success(data=result, message=f'识别到 {len(result["measurements"])} 个部位')
    except Exception as e:
        traceback.print_exc()
        return api_error(f'识别失败：{e}', status_code=503, error_type='ai_unavailable')


@bp_bone.route('/api/bone', methods=['POST'])
@login_required
def create_bone_record():
    """新增一次骨密度检查。"""
    try:
        payload, error = bone_density.validate_payload(request.json or {})
        if error:
            return api_error(error, status_code=400, error_type='validation_error')

        user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute("""
            INSERT INTO bone_density_records
                (user_id, measure_date, site, t_score, z_score, bmd, institution, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, payload['measure_date'], payload['site'], payload['t_score'],
            payload['z_score'], payload['bmd'], payload['institution'], payload['notes'],
        ))
        db.commit()

        rows = _rows_for_user(db, user_id)
        return api_success(data=bone_density.build_summary(rows), message='骨密度记录已保存')
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_bone.route('/api/bone/delete/<int:record_id>', methods=['POST'])
@login_required
def delete_bone_record(record_id):
    """删除一条骨密度记录（仅限本人数据）。"""
    try:
        user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute("SELECT id FROM bone_density_records WHERE id = ? AND user_id = ?", (record_id, user_id))
        if not c.fetchone():
            return api_error('记录不存在', status_code=404, error_type='not_found')
        c.execute("DELETE FROM bone_density_records WHERE id = ? AND user_id = ?", (record_id, user_id))
        db.commit()

        rows = _rows_for_user(db, user_id)
        return api_success(data=bone_density.build_summary(rows), message='已删除')
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
