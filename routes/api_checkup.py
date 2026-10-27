"""体检历史 API（模块 checkup）。

流程：前端上传报告照片 → ``/api/checkup/parse`` 只解析不落库 → 用户确认/修改
→ ``/api/checkup/save`` 落库。两步分开是为了让用户能纠正 AI 抄错的数值。
"""
import base64
import traceback

from flask import Blueprint, request

import checkup_parser
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.responses import api_error, api_success
from utils.timezone import today as app_today

bp_checkup = Blueprint('checkup', __name__)
user_manager = UserManager()

MAX_ITEMS = 200


def _report_list(db, user_id):
    c = db.cursor()
    c.execute("""
        SELECT r.id, r.checkup_date, r.institution, r.summary, r.created_at,
               (SELECT COUNT(*) FROM checkup_items i WHERE i.report_id = r.id) AS item_count,
               (SELECT COUNT(*) FROM checkup_items i WHERE i.report_id = r.id AND i.status <> 'normal') AS abnormal_count
        FROM checkup_reports r
        WHERE r.user_id = ?
        ORDER BY r.checkup_date DESC, r.id DESC
    """, (user_id,))
    return [dict(row) for row in c.fetchall()]


def _report_detail(db, user_id, report_id):
    c = db.cursor()
    c.execute("""
        SELECT id, checkup_date, institution, summary, created_at
        FROM checkup_reports WHERE id = ? AND user_id = ?
    """, (report_id, user_id))
    report = c.fetchone()
    if not report:
        return None
    c.execute("""
        SELECT id, category, name, value, unit, ref_range, status
        FROM checkup_items WHERE report_id = ? ORDER BY id
    """, (report_id,))
    detail = dict(report)
    detail['items'] = [dict(row) for row in c.fetchall()]
    return detail


@bp_checkup.route('/api/checkup/parse', methods=['POST'])
@login_required
def parse_report():
    """解析体检报告照片，返回结构化指标（不落库）。"""
    try:
        data = request.json or {}
        images_b64 = data.get('images') or []
        mime_type = data.get('mime_type', 'image/jpeg')
        if not images_b64:
            return api_error('请上传体检报告照片', status_code=400, error_type='validation_error')

        images_data = []
        for img in images_b64[:3]:
            try:
                raw = base64.b64decode(str(img).split(',')[-1])
            except Exception:
                return api_error('图片数据无法识别，请重新上传', status_code=400, error_type='validation_error')
            if not raw:
                # b64decode 对 '@@@' 这类垃圾串不报错、只返回空字节，需显式拦掉
                return api_error('图片数据无法识别，请重新上传', status_code=400, error_type='validation_error')
            images_data.append(raw)

        result = checkup_parser.parse_checkup_report(images_data, mime_type)
        if not result['items']:
            return api_error(
                '没有从图片中识别出体检指标，可以换一张更清晰的照片，或稍后手工录入',
                status_code=422, error_type='parse_empty',
            )
        if not result['checkup_date']:
            result['checkup_date'] = app_today().strftime('%Y-%m-%d')
        return api_success(data=result, message=f'识别到 {len(result["items"])} 项指标')
    except Exception as e:
        traceback.print_exc()
        return api_error(f'识别失败：{e}', status_code=503, error_type='ai_unavailable')


@bp_checkup.route('/api/checkup/save', methods=['POST'])
@login_required
def save_report():
    """保存（用户确认后的）体检报告与指标明细。"""
    try:
        data = request.json or {}
        items = data.get('items')
        if not isinstance(items, list) or not items:
            return api_error('至少需要一项体检指标', status_code=400, error_type='validation_error')
        if len(items) > MAX_ITEMS:
            return api_error(f'单次最多保存 {MAX_ITEMS} 项指标', status_code=400, error_type='validation_error')

        checkup_date = str(data.get('checkup_date') or '').strip()[:10] or app_today().strftime('%Y-%m-%d')
        institution = ' '.join(str(data.get('institution') or '').split())[:50]

        clean_items = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            name = ' '.join(str(raw.get('name') or '').split())[:40]
            value = ' '.join(str(raw.get('value') or '').split())[:30]
            if not name or not value:
                continue
            category = str(raw.get('category') or '其他').strip()
            if category not in checkup_parser.CATEGORIES:
                category = '其他'
            status = str(raw.get('status') or 'normal').strip().lower()
            if status not in ('normal', 'high', 'low'):
                status = 'normal'
            clean_items.append({
                'category': category,
                'name': name,
                'value': value,
                'unit': ' '.join(str(raw.get('unit') or '').split())[:20],
                'ref_range': ' '.join(str(raw.get('ref_range') or '').split())[:40],
                'status': status,
            })
        if not clean_items:
            return api_error('指标缺少项目名或数值', status_code=400, error_type='validation_error')

        user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        abnormal = sum(1 for i in clean_items if i['status'] != 'normal')
        summary = f'共 {len(clean_items)} 项，异常 {abnormal} 项'
        c.execute("""
            INSERT INTO checkup_reports (user_id, checkup_date, institution, summary)
            VALUES (?, ?, ?, ?)
        """, (user_id, checkup_date, institution, summary))
        report_id = c.lastrowid

        for item in clean_items:
            c.execute("""
                INSERT INTO checkup_items
                    (report_id, user_id, category, name, value, unit, ref_range, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (report_id, user_id, item['category'], item['name'], item['value'],
                  item['unit'], item['ref_range'], item['status']))
        db.commit()

        return api_success(data=_report_detail(db, user_id, report_id), message='体检记录已保存')
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_checkup.route('/api/checkup/list', methods=['GET'])
@login_required
def list_reports():
    """体检报告列表（按检查日期倒序）。"""
    try:
        user_id = user_manager.get_current_user_id()
        return api_success(data={'reports': _report_list(get_db(), user_id)})
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_checkup.route('/api/checkup/<int:report_id>', methods=['GET'])
@login_required
def report_detail(report_id):
    """单次体检的指标明细。"""
    try:
        user_id = user_manager.get_current_user_id()
        detail = _report_detail(get_db(), user_id, report_id)
        if not detail:
            return api_error('体检记录不存在', status_code=404, error_type='not_found')
        return api_success(data=detail)
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)


@bp_checkup.route('/api/checkup/delete/<int:report_id>', methods=['POST'])
@login_required
def delete_report(report_id):
    """删除一次体检记录及其指标。"""
    try:
        user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        c.execute("SELECT id FROM checkup_reports WHERE id = ? AND user_id = ?", (report_id, user_id))
        if not c.fetchone():
            return api_error('体检记录不存在', status_code=404, error_type='not_found')
        c.execute("DELETE FROM checkup_items WHERE report_id = ? AND user_id = ?", (report_id, user_id))
        c.execute("DELETE FROM checkup_reports WHERE id = ? AND user_id = ?", (report_id, user_id))
        db.commit()
        return api_success(message='已删除')
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)
