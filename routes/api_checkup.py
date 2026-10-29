"""体检历史 API（模块 checkup）。

流程：前端上传报告照片/PDF → ``/api/checkup/parse`` 只解析不落库 → 用户确认/修改
→ ``/api/checkup/save`` 落库。两步分开是为了让用户能纠正 AI 抄错的数值。
"""
import traceback
from contextlib import suppress

from flask import Blueprint, request

import checkup_parser
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.report_files import ReportFileError, to_report_pages
from utils.report_processing import process_report_pages
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
    """解析整份体检报告；原生文字优先，视觉兜底，单次最多 50 页。"""
    try:
        data = request.json or {}
        images_b64 = data.get('images') or []
        if not images_b64:
            return api_error('请上传体检报告照片或 PDF', status_code=400, error_type='validation_error')

        try:
            pages = to_report_pages(images_b64)
        except ReportFileError as exc:
            return api_error(str(exc), status_code=400, error_type='validation_error')

        result = process_report_pages(
            pages,
            text_parser=checkup_parser.parse_checkup_text,
            vision_parser=checkup_parser.parse_checkup_report,
            item_key='items',
            date_key='checkup_date',
        )
        if not result['items']:
            return api_error(
                '没有从报告里识别出体检指标，可以换一张更清晰的照片，或稍后手工录入',
                status_code=422, error_type='parse_empty',
            )
        if not result['checkup_date']:
            result['checkup_date'] = app_today().strftime('%Y-%m-%d')
        message = f'识别到 {len(result["items"])} 项指标'
        if result['failed_pages']:
            message += f'，{len(result["failed_pages"])} 页识别失败'
        return api_success(data=result, message=message)
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
            # 与 checkup_parser.VALUE_LIMIT 保持一致：影像描述等长文本不能截断
            value = ' '.join(str(raw.get('value') or '').split())[:checkup_parser.VALUE_LIMIT]
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
            RETURNING id
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


@bp_checkup.route('/api/checkup/merge', methods=['POST'])
@login_required
def merge_reports():
    """合并同一检查日期分批上传的多份报告为一条记录。

    完全相同的指标行（项目+数值+单位都一样）只保留一条；
    同名但数值不同的行全部保留，由用户在详情里自行取舍。
    """
    try:
        data = request.json or {}
        raw_ids = data.get('report_ids') or []
        if not isinstance(raw_ids, list) or len(raw_ids) < 2:
            return api_error('请勾选至少 2 份要合并的报告', status_code=400, error_type='validation_error')
        try:
            report_ids = sorted({int(r) for r in raw_ids})
        except (TypeError, ValueError):
            return api_error('报告 ID 无效', status_code=400, error_type='validation_error')
        if len(report_ids) < 2:
            return api_error('请勾选至少 2 份不同的报告', status_code=400, error_type='validation_error')

        user_id = user_manager.get_current_user_id()
        db = get_db()
        c = db.cursor()
        placeholders = ','.join('?' * len(report_ids))
        c.execute(f"""
            SELECT id, checkup_date, institution FROM checkup_reports
            WHERE user_id = ? AND id IN ({placeholders})
        """, (user_id, *report_ids))
        rows = c.fetchall()
        if len(rows) != len(report_ids):
            return api_error('有报告不存在或不属于当前用户', status_code=404, error_type='not_found')

        dates = {r['checkup_date'] for r in rows}
        if len(dates) != 1:
            return api_error('只能合并检查日期相同的报告（同一次体检）', status_code=400,
                             error_type='validation_error')

        institution = next((r['institution'] for r in rows if r['institution']), '')
        c.execute("""
            INSERT INTO checkup_reports (user_id, checkup_date, institution, summary)
            VALUES (?, ?, ?, '')
            RETURNING id
        """, (user_id, rows[0]['checkup_date'], institution))
        merged_id = c.lastrowid

        c.execute(f"UPDATE checkup_items SET report_id = ? WHERE report_id IN ({placeholders})",
                  (merged_id, *report_ids))

        # 项目+数值+单位+参考范围完全相同的行只保留一条（参考范围不同不算重复）
        c.execute("""
            DELETE FROM checkup_items
            WHERE report_id = ? AND id NOT IN (
                SELECT MIN(id) FROM checkup_items
                WHERE report_id = ? GROUP BY category, name, value, unit, ref_range
            )
        """, (merged_id, merged_id))

        # 异常数用 COUNT 分开查：PostgreSQL 不允许对布尔表达式 SUM
        c.execute("SELECT COUNT(*) FROM checkup_items WHERE report_id = ?", (merged_id,))
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM checkup_items WHERE report_id = ? AND status <> 'normal'",
                  (merged_id,))
        abnormal = c.fetchone()[0]
        c.execute("UPDATE checkup_reports SET summary = ? WHERE id = ?",
                  (f'共 {total} 项，异常 {abnormal} 项', merged_id))
        c.execute(f"DELETE FROM checkup_reports WHERE id IN ({placeholders})", (*report_ids,))
        db.commit()

        return api_success(data=_report_detail(db, user_id, merged_id), message='已合并为一份报告')
    except Exception as e:
        # 多步写操作中途失败必须回滚，避免留下半合并状态（孤儿报告/悬空指标）
        with suppress(Exception):
            get_db().rollback()
        traceback.print_exc()
        return api_error(str(e), status_code=500)
