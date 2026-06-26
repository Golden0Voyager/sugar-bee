import re
import traceback

from flask import Blueprint, jsonify, request

import settings
from core.config import DB_NAME
from services import (
    backfill_post_exercise_predictions,
    predict_morning_fpg,
    predict_post_exercise_glucose,
    predict_remaining_glucose_slots,
)
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.responses import api_error, api_success
from utils.sql_dialect import interval_sql
from utils.timezone import today_str as app_today_str

user_manager = UserManager(DB_NAME)
bp_prediction = Blueprint('prediction', __name__)


@bp_prediction.route('/trigger_prediction', methods=['POST'])
@login_required
def trigger_prediction():
    """
    手动触发预测
    支持触发空腹血糖、运动后血糖、以及剩余时间点预测

    参数:
        type: 'all', 'fpg', '空腹', 'post_exercise', '运动后', 'remaining', '剩余时间点'
        date: 目标日期 (默认今天)
        force_update: 是否强制更新已有预测 (默认 false)
    """
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        data = request.json or {}
        prediction_type = data.get('type', 'all')
        target_date = data.get('date', app_today_str())
        force_update = data.get('force_update', False)

        results = []

        if prediction_type in ['all', 'fpg', '空腹']:
            predict_morning_fpg(db, current_user_id)
            results.append({'type': '空腹', 'status': 'triggered'})

        if prediction_type in ['all', 'post_exercise', '运动后']:
            result = predict_post_exercise_glucose(db, current_user_id, target_date)
            if result:
                results.append({'type': '运动后', 'status': 'success', 'value': result})
            else:
                results.append({'type': '运动后', 'status': 'skipped', 'reason': '条件不满足或已存在'})

        if prediction_type in ['all', 'remaining', '剩余时间点']:
            remaining_results = predict_remaining_glucose_slots(db, current_user_id, target_date, force_update=force_update)
            if isinstance(remaining_results, list) and remaining_results:
                for pred in remaining_results:
                    results.append({
                        'type': pred['type'],
                        'status': 'updated' if force_update else 'success',
                        'value': pred['value'],
                        'reasoning': pred.get('reasoning', '')
                    })
            elif remaining_results == 'no_measured':
                results.append({'type': '剩余时间点', 'status': 'skipped', 'reason': '无实测血糖基准，请先录入实测数据'})
            elif remaining_results == 'all_measured':
                results.append({'type': '剩余时间点', 'status': 'skipped', 'reason': '所有时间点已有实测数据'})
            else:
                results.append({'type': '剩余时间点', 'status': 'skipped', 'reason': '预测未返回有效结果'})

        return api_success(data={'results': results})

    except Exception as e:
        error_str = str(e)
        traceback.print_exc()

        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
            retry_match = re.search(r'retry in (\d+)', error_str.lower())
            retry_seconds = int(retry_match.group(1)) if retry_match else 60

            return api_error(
                f"AI 服务配额已用尽，请等待 {retry_seconds} 秒后重试",
                status_code=429,
                error_type="quota_exceeded",
                details={"retry_after": retry_seconds}
            )

        return api_error(str(e), status_code=500, error_type="prediction_error")


@bp_prediction.route('/prediction_comparison', methods=['GET'])
@login_required
def prediction_comparison():
    """获取预测与真实值对比数据（用于对比图）"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        days = request.args.get('days', 7, type=int)
        glucose_type = request.args.get('type', None)

        query = f"""
            SELECT
                p.type,
                DATE(p.timestamp) as date,
                p.value as predicted_value,
                r.value as real_value,
                p.prediction_error
            FROM records p
            JOIN records r ON p.verified_by_real_id = r.id
            WHERE p.user_id = ?
            AND p.is_predicted = 1
            AND p.verified_by_real_id IS NOT NULL
            AND p.timestamp > {interval_sql(days)}
        """
        params = [current_user_id]
        if glucose_type:
            query += " AND p.type = ?"
            params.append(glucose_type)

        query += " ORDER BY p.timestamp ASC"

        c.execute(query, params)

        comparisons = []
        for row in c.fetchall():
            comparisons.append({
                'type': row[0],
                'date': row[1],
                'predicted': round(row[2], 2),
                'real': round(row[3], 2),
                'error': round(row[4], 2) if row[4] else None
            })

        return jsonify({
            'days': days,
            'type_filter': glucose_type,
            'data': comparisons
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp_prediction.route('/prediction_accuracy', methods=['GET'])
@login_required
def prediction_accuracy():
    """获取预测准确性统计"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        days = request.args.get('days', 30, type=int)

        c.execute(f"""
            SELECT
                COUNT(*) as total,
                AVG(prediction_error) as avg_error,
                AVG(ABS(prediction_error)) as mae,
                MIN(prediction_error) as min_error,
                MAX(prediction_error) as max_error
            FROM records
            WHERE user_id = ?
            AND is_predicted = 1
            AND verified_by_real_id IS NOT NULL
            AND timestamp > {interval_sql(days)}
        """, (current_user_id,))

        row = c.fetchone()

        if not row or row[0] == 0:
            return jsonify({
                'total_predictions': 0,
                'average_error': None,
                'mae': None,
                'min_error': None,
                'max_error': None,
                'days': days,
                'message': '暂无已验证的预测数据'
            })

        return jsonify({
            'total_predictions': int(row[0]),
            'average_error': round(float(row[1]), 2) if row[1] is not None else None,
            'mae': round(float(row[2]), 2) if row[2] is not None else None,
            'min_error': round(float(row[3]), 2) if row[3] is not None else None,
            'max_error': round(float(row[4]), 2) if row[4] is not None else None,
            'days': days
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp_prediction.route('/prediction_status', methods=['GET'])
@login_required
def prediction_status():
    """
    获取今日所有时间槽状态
    返回所有时间点的状态：已测量(measured)、已预测(predicted)、待测(pending)
    """
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        today_str = app_today_str()

        all_slots = [
            {'key': 'fasting', 'name': '空腹', 'time': '07:15', 'type_patterns': ['空腹', '早空腹']},
            {'key': 'post_exercise', 'name': '运动后', 'time': '08:45', 'type_patterns': ['运动后']},
            {'key': 'post_breakfast', 'name': '早餐后2小时', 'time': '11:00', 'type_patterns': ['早餐后', '餐后2小时', '餐后1小时']},
            {'key': 'post_lunch', 'name': '午餐后2小时', 'time': '14:30', 'type_patterns': ['午餐后']},
            {'key': 'pre_dinner', 'name': '晚饭前', 'time': '17:30', 'type_patterns': ['晚饭前', '晚餐前', '餐前']},
            {'key': 'post_dinner', 'name': '晚餐后2小时', 'time': '20:00', 'type_patterns': ['晚餐后']},
            {'key': 'bedtime', 'name': '睡前', 'time': '22:00', 'type_patterns': ['睡前']}
        ]

        c.execute("""
            SELECT id, type, value, timestamp, is_predicted, notes, verified_by_real_id, prediction_error
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND value > 0
            AND systolic_pressure IS NULL
            ORDER BY timestamp ASC
        """, (current_user_id, today_str))
        today_records = c.fetchall()

        slots = []
        for slot in all_slots:
            slot_data = {
                'key': slot['key'],
                'name': slot['name'],
                'time': slot['time'],
                'timestamp': f"{today_str} {slot['time']}:00",
                'status': 'pending',
                'value': None,
                'predicted_value': None,
                'real_value': None,
                'notes': None,
                'error': None
            }

            measured_record = None
            predicted_record = None

            for record in today_records:
                record_type = record['type'] or ''
                record_time = record['timestamp'].split(' ')[1][:5] if ' ' in record['timestamp'] else ''
                is_predicted = record['is_predicted']

                matched = False
                for pattern in slot['type_patterns']:
                    if pattern in record_type:
                        matched = True
                        break

                if slot['key'] == 'post_breakfast' and matched:
                    if '午餐后' in record_type or '晚餐后' in record_type:
                        matched = False
                    if record_time:
                        hour = int(record_time.split(':')[0])
                        if hour < 10 or hour >= 13:
                            matched = False

                if slot['key'] == 'pre_dinner' and matched:
                    if '运动后' in record_type:
                        matched = False
                    if record_time and '餐前' in record_type:
                        hour = int(record_time.split(':')[0])
                        if hour < 16 or hour >= 19:
                            matched = False

                if slot['key'] == 'post_lunch' and not matched:  # noqa: SIM102
                    if '餐后2小时' in record_type or '餐后' in record_type:  # noqa: SIM102
                        if '午餐' not in record_type and '早餐' not in record_type and '晚餐' not in record_type:  # noqa: SIM102
                            if record_time:
                                hour = int(record_time.split(':')[0])
                                if 13 <= hour < 17:
                                    matched = True

                if slot['key'] == 'post_dinner' and not matched:  # noqa: SIM102
                    if '餐后2小时' in record_type or '餐后' in record_type:  # noqa: SIM102
                        if '午餐' not in record_type and '早餐' not in record_type and '晚餐' not in record_type:  # noqa: SIM102
                            if record_time:
                                hour = int(record_time.split(':')[0])
                                if 19 <= hour < 23:
                                    matched = True

                if matched:
                    if not is_predicted:
                        if measured_record is None:
                            measured_record = record
                    else:
                        if predicted_record is None:
                            predicted_record = record

            # CGM 匹配
            cgm_record = None
            if measured_record is None:
                slot_target_minutes = int(slot['time'].split(':')[0]) * 60 + int(slot['time'].split(':')[1])
                cgm_min_diff = float('inf')
                for record in today_records:
                    if (record['type'] or '') != 'CGM':
                        continue
                    record_time = record['timestamp'].split(' ')[1][:5] if ' ' in record['timestamp'] else ''
                    if not record_time or ':' not in record_time:
                        continue
                    try:
                        parts = record_time.split(':')
                        record_minutes = int(parts[0]) * 60 + int(parts[1])
                    except (ValueError, IndexError):
                        continue
                    diff = abs(record_minutes - slot_target_minutes)
                    if diff < cgm_min_diff and diff <= 30:
                        cgm_min_diff = diff
                        cgm_record = record

            if measured_record:
                slot_data['status'] = 'measured'
                slot_data['value'] = round(measured_record['value'], 2)
                slot_data['real_value'] = round(measured_record['value'], 2)
                slot_data['timestamp'] = measured_record['timestamp']
                if predicted_record and predicted_record['verified_by_real_id'] == measured_record['id']:
                    slot_data['status'] = 'verified'
                    slot_data['predicted_value'] = round(predicted_record['value'], 2)
                    slot_data['error'] = round(predicted_record['prediction_error'], 2) if predicted_record['prediction_error'] else None
                    slot_data['notes'] = predicted_record['notes']
            elif cgm_record:
                slot_data['status'] = 'measured'
                slot_data['value'] = round(cgm_record['value'], 2)
                slot_data['real_value'] = round(cgm_record['value'], 2)
                slot_data['timestamp'] = cgm_record['timestamp']
                slot_data['cgm'] = True
                if predicted_record:
                    if predicted_record['verified_by_real_id']:
                        slot_data['status'] = 'verified'
                        slot_data['predicted_value'] = round(predicted_record['value'], 2)
                        slot_data['error'] = round(predicted_record['prediction_error'], 2) if predicted_record['prediction_error'] else None
                        slot_data['notes'] = predicted_record['notes']
                    else:
                        slot_data['status'] = 'verified'
                        slot_data['predicted_value'] = round(predicted_record['value'], 2)
                        slot_data['error'] = round(cgm_record['value'] - predicted_record['value'], 2)
                        slot_data['notes'] = predicted_record['notes']
            elif predicted_record:
                slot_data['status'] = 'predicted'
                slot_data['value'] = round(predicted_record['value'], 2)
                slot_data['predicted_value'] = round(predicted_record['value'], 2)
                slot_data['timestamp'] = predicted_record['timestamp']
                slot_data['notes'] = predicted_record['notes']
                if predicted_record['verified_by_real_id']:
                    c.execute("SELECT value FROM records WHERE id = ?", (predicted_record['verified_by_real_id'],))
                    real_row = c.fetchone()
                    if real_row:
                        slot_data['status'] = 'verified'
                        slot_data['real_value'] = round(real_row['value'], 2)
                        slot_data['error'] = round(predicted_record['prediction_error'], 2) if predicted_record['prediction_error'] else None

            target = settings.get_glucose_target(slot['name'])
            slot_data['target'] = {
                'min': target['min'],
                'max': target['max'],
                'optimal_max': target['optimal_max']
            }

            slots.append(slot_data)

        # 近7天预测准确性统计
        c.execute(f"""
            SELECT
                type,
                COUNT(*) as total,
                AVG(ABS(prediction_error)) as mae,
                SUM(CASE WHEN ABS(prediction_error) < 0.5 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as accuracy
            FROM records
            WHERE user_id = ?
            AND is_predicted = 1
            AND verified_by_real_id IS NOT NULL
            AND timestamp > {interval_sql(7)}
            GROUP BY type
        """, (current_user_id,))

        accuracy_by_type = {}
        for row in c.fetchall():
            accuracy_by_type[row[0]] = {
                'total': row[1],
                'mae': round(row[2], 2) if row[2] else None,
                'accuracy': round(row[3], 1) if row[3] else None
            }

        return jsonify({
            'date': today_str,
            'slots': slots,
            'accuracy_by_type': accuracy_by_type
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp_prediction.route('/backfill_predictions', methods=['POST'])
@login_required
def backfill_predictions():
    """批量补全历史运动后血糖预测"""
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()

        days = request.json.get('days', 30) if request.json else 30

        result = backfill_post_exercise_predictions(db, current_user_id, days)

        return api_success(
            data=result,
            message=f"成功预测 {result['success']} 条记录，跳过 {result['skipped']} 条"
        )
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500, error_type="backfill_error")
