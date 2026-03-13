from flask import Blueprint, request, jsonify
import datetime
import traceback

import settings
from user_manager import UserManager
from core.config import DB_NAME
from utils.auth import login_required
from utils.db import get_db
from services import build_timeline, get_dashboard_stats

user_manager = UserManager(DB_NAME)
bp_dashboard = Blueprint('dashboard', __name__)


@bp_dashboard.route('/api/timeline')
@login_required
def api_timeline():
    """按需加载 timeline 数据，返回与 timelineData 相同格式的 JSON"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        days = request.args.get('days', 90, type=int)
        sorted_dates, _ = build_timeline(c, current_user_id, days=days)
        return jsonify(sorted_dates)
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@bp_dashboard.route('/api/health_stats')
@login_required
def api_health_stats():
    """返回指定天数范围的健康数据统计"""
    try:
        days_param = request.args.get('days', '7')
        days = None if days_param in ('null', 'all', '') else int(days_param)
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        user_config = settings.load_config()
        if days:
            cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        else:
            cutoff = '2000-01-01 00:00:00'

        # 血糖统计
        c.execute("""
            SELECT
                AVG(CASE WHEN (type LIKE '%空腹%') THEN value END),
                AVG(CASE WHEN type LIKE '%餐后2小时%' THEN value END),
                MAX(value), MIN(value)
            FROM records
            WHERE user_id = ? AND value > 0 AND is_predicted = 0
            AND systolic_pressure IS NULL AND timestamp > ?
        """, (current_user_id, cutoff))
        gs = c.fetchone()

        # 最高/最低血糖的详细信息（时间+类型）
        max_glucose_detail = {'timestamp': '', 'type': ''}
        min_glucose_detail = {'timestamp': '', 'type': ''}
        if gs[2]:
            c.execute("""SELECT timestamp, type FROM records
                WHERE user_id = ? AND value = ? AND value > 0 AND is_predicted = 0
                AND systolic_pressure IS NULL AND timestamp > ?
                ORDER BY timestamp DESC LIMIT 1""",
                (current_user_id, gs[2], cutoff))
            row = c.fetchone()
            if row:
                max_glucose_detail = {'timestamp': row[0], 'type': row[1]}
        if gs[3]:
            c.execute("""SELECT timestamp, type FROM records
                WHERE user_id = ? AND value = ? AND value > 0 AND is_predicted = 0
                AND systolic_pressure IS NULL AND timestamp > ?
                ORDER BY timestamp ASC LIMIT 1""",
                (current_user_id, gs[3], cutoff))
            row = c.fetchone()
            if row:
                min_glucose_detail = {'timestamp': row[0], 'type': row[1]}

        # 达标率
        c.execute("""
            SELECT value, type FROM records
            WHERE user_id = ? AND value > 0 AND is_predicted = 0
            AND systolic_pressure IS NULL AND timestamp > ?
        """, (current_user_id, cutoff))
        ok_count = total_g = 0
        for r in c.fetchall():
            total_g += 1
            result = settings.check_glucose_compliance(r['value'], r['type'])
            if result['is_compliant']:
                ok_count += 1
        compliance = int((ok_count / total_g * 100)) if total_g > 0 else 0

        # 运动统计
        c.execute("""
            SELECT SUM(distance), SUM(CASE WHEN calories > 0 THEN calories END),
                   AVG(CASE WHEN heart_rate IS NOT NULL THEN heart_rate END),
                   COUNT(DISTINCT DATE(timestamp))
            FROM records
            WHERE user_id = ? AND timestamp > ?
            AND (distance IS NOT NULL OR type IN ('跑步', '运动'))
        """, (current_user_id, cutoff))
        es = c.fetchone()

        # 血压统计
        c.execute("""
            SELECT AVG(systolic_pressure), AVG(diastolic_pressure), COUNT(*),
                   MAX(systolic_pressure), MAX(diastolic_pressure),
                   MIN(systolic_pressure), MIN(diastolic_pressure)
            FROM records
            WHERE user_id = ? AND systolic_pressure IS NOT NULL
            AND systolic_pressure > 0 AND diastolic_pressure > 0
            AND timestamp > ?
        """, (current_user_id, cutoff))
        bs = c.fetchone()

        bp_max_date = bp_min_date = '-'
        bp_max_timestamp = ''
        bp_min_timestamp = ''
        if bs[3]:
            c.execute("SELECT timestamp FROM records WHERE user_id = ? AND systolic_pressure = ? AND timestamp > ? AND systolic_pressure IS NOT NULL LIMIT 1",
                      (current_user_id, bs[3], cutoff))
            row = c.fetchone()
            if row:
                bp_max_timestamp = row[0]
                bp_max_date = row[0][:10]
        if bs[5]:
            c.execute("SELECT timestamp FROM records WHERE user_id = ? AND systolic_pressure = ? AND timestamp > ? AND systolic_pressure IS NOT NULL LIMIT 1",
                      (current_user_id, bs[5], cutoff))
            row = c.fetchone()
            if row:
                bp_min_timestamp = row[0]
                bp_min_date = row[0][:10]

        # 体重统计
        c.execute("SELECT weight, bmi, timestamp FROM records WHERE user_id = ? AND weight > 0 ORDER BY timestamp DESC LIMIT 1", (current_user_id,))
        lw = c.fetchone()
        latest_weight_ts = lw[2] if lw else ''
        c.execute("SELECT AVG(weight) FROM records WHERE user_id = ? AND weight > 0 AND timestamp > ?", (current_user_id, cutoff))
        aw = c.fetchone()[0]

        weight_change = None
        if lw and lw[0]:
            if days:
                c.execute("SELECT weight FROM records WHERE user_id = ? AND weight > 0 AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
                          (current_user_id, cutoff))
                ow = c.fetchone()
                if ow: weight_change = round(lw[0] - ow[0], 1)
            else:
                c.execute("SELECT weight FROM records WHERE user_id = ? AND weight > 0 ORDER BY timestamp ASC LIMIT 1",
                          (current_user_id,))
                ow = c.fetchone()
                if ow: weight_change = round(lw[0] - ow[0], 1)

        bmi_cat = settings.get_bmi_category(lw[1]) if lw and lw[1] else {'label': '-', 'color': '#999'}

        # 最新 VO2max
        c.execute("""SELECT vo2max, timestamp FROM records
            WHERE user_id = ? AND vo2max IS NOT NULL AND vo2max > 0
            ORDER BY timestamp DESC LIMIT 1""", (current_user_id,))
        vo2row = c.fetchone()
        latest_vo2max = vo2row[0] if vo2row else None
        latest_vo2max_date = vo2row[1] if vo2row else None
        prev_vo2max = None
        if vo2row:
            c.execute("""SELECT vo2max FROM records
                WHERE user_id = ? AND vo2max IS NOT NULL AND vo2max > 0
                AND timestamp < ? ORDER BY timestamp DESC LIMIT 1""", (current_user_id, vo2row[1]))
            pv = c.fetchone()
            if pv: prev_vo2max = pv[0]

        return jsonify({
            'days': days,
            'glucose': {
                'avg_fasting': round(gs[0], 1) if gs[0] else 0,
                'avg_post2h': round(gs[1], 1) if gs[1] else 0,
                'max': round(gs[2], 1) if gs[2] else 0,
                'min': round(gs[3], 1) if gs[3] else 0,
                'max_detail': max_glucose_detail,
                'min_detail': min_glucose_detail,
                'compliance': compliance
            },
            'exercise': {
                'total_distance': round(es[0], 1) if es[0] else 0,
                'total_calories': int(es[1]) if es[1] else 0,
                'avg_heart_rate': round(es[2]) if es[2] else 0,
                'count': es[3] if es[3] else 0,
                'latest_vo2max': latest_vo2max,
                'latest_vo2max_date': latest_vo2max_date,
                'prev_vo2max': prev_vo2max
            },
            'bp': {
                'avg_sys': round(bs[0]) if bs[0] else 0,
                'avg_dia': round(bs[1]) if bs[1] else 0,
                'count': bs[2] if bs[2] else 0,
                'max_sys': bs[3] if bs[3] else 0, 'max_dia': bs[4] if bs[4] else 0,
                'max_date': bp_max_date, 'max_timestamp': bp_max_timestamp,
                'min_sys': bs[5] if bs[5] else 0, 'min_dia': bs[6] if bs[6] else 0,
                'min_date': bp_min_date, 'min_timestamp': bp_min_timestamp
            },
            'weight': {
                'latest': round(lw[0], 1) if lw and lw[0] else None,
                'bmi': round(lw[1], 1) if lw and lw[1] else None,
                'latest_timestamp': latest_weight_ts,
                'bmi_label': bmi_cat['label'], 'bmi_color': bmi_cat['color'],
                'avg': round(aw, 1) if aw else None,
                'change': weight_change,
                'target_weight': user_config.get('target_weight')
            }
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@bp_dashboard.route('/api/day_overview')
@login_required
def api_day_overview():
    """返回指定日期的概览数据（血糖时间轴 + 运动/血压/体重/用药）"""
    try:
        date_str = request.args.get('date', datetime.datetime.now().strftime('%Y-%m-%d'))
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        day_start = date_str + ' 00:00:00'
        day_end = date_str + ' 23:59:59'
        target_date = datetime.datetime.strptime(date_str, '%Y-%m-%d')
        weekday_name = target_date.strftime('%A')
        day_of_month = target_date.day

        # 血糖时间轴
        today_schedule = [
            {'key': 'fasting', 'name': '空腹', 'time': '07:15', 'icon': '🌅'},
            {'key': 'post_exercise', 'name': '运动后', 'time': '08:45', 'icon': '🏃'},
            {'key': 'post_breakfast', 'name': '早餐后2h', 'time': '11:00', 'icon': '☕'},
            {'key': 'post_lunch', 'name': '午餐后2h', 'time': '14:30', 'icon': '🌞'},
            {'key': 'pre_dinner', 'name': '晚饭前', 'time': '17:30', 'icon': '🍽️'},
            {'key': 'post_dinner', 'name': '晚餐后2h', 'time': '20:00', 'icon': '🌙'},
            {'key': 'bedtime', 'name': '睡前', 'time': '22:00', 'icon': '😴'}
        ]

        c.execute("""
            SELECT value, type, timestamp, is_predicted
            FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
            AND value > 0 AND systolic_pressure IS NULL
            ORDER BY timestamp ASC, is_predicted ASC
        """, (current_user_id, day_start, day_end))
        records = c.fetchall()

        overview = []
        measured_count = 0
        predicted_count = 0
        for slot in today_schedule:
            slot_data = {'key': slot['key'], 'name': slot['name'], 'time': slot['time'],
                         'icon': slot['icon'], 'value': None, 'status': 'pending', 'compliance': None}
            measured_match = predicted_match = None
            for r in records:
                rt = r['type'] or ''
                rh = -1
                rt_time = r['timestamp'].split(' ')[1][:5] if ' ' in r['timestamp'] else ''
                if rt_time and ':' in rt_time:
                    try: rh = int(rt_time.split(':')[0])
                    except: rh = -1
                matched = False
                is_generic_post = '餐后' in rt and not ('早餐后' in rt or '午餐后' in rt or '晚餐后' in rt)
                is_generic_pre = '餐前' in rt and not ('早餐前' in rt or '午餐前' in rt or '晚餐前' in rt or '晚饭前' in rt)
                if slot['key'] == 'fasting' and '空腹' in rt: matched = True
                elif slot['key'] == 'post_exercise' and '运动后' in rt: matched = True
                elif slot['key'] == 'post_breakfast' and ('早餐后' in rt or (is_generic_post and 10 <= rh < 13)): matched = True
                elif slot['key'] == 'post_lunch' and ('午餐后' in rt or (is_generic_post and 13 <= rh < 17)): matched = True
                elif slot['key'] == 'pre_dinner' and ('晚饭前' in rt or '晚餐前' in rt or (is_generic_pre and 16 <= rh < 19)): matched = True
                elif slot['key'] == 'post_dinner' and ('晚餐后' in rt or (is_generic_post and 19 <= rh < 23)): matched = True
                elif slot['key'] == 'bedtime' and '睡前' in rt: matched = True
                if matched:
                    if not r['is_predicted'] and not measured_match: measured_match = r
                    elif r['is_predicted'] and not predicted_match: predicted_match = r
            chosen = measured_match or predicted_match

            # CGM 优先：查找该时间点30分钟内最近的CGM读数
            cgm_match = None
            cgm_min_diff = float('inf')
            slot_target_minutes = int(slot['time'].split(':')[0]) * 60 + int(slot['time'].split(':')[1])
            for r in records:
                if (r['type'] or '') != 'CGM':
                    continue
                rt_time = r['timestamp'].split(' ')[1][:5] if ' ' in r['timestamp'] else ''
                if not rt_time or ':' not in rt_time:
                    continue
                try:
                    parts = rt_time.split(':')
                    r_minutes = int(parts[0]) * 60 + int(parts[1])
                except (ValueError, IndexError):
                    continue
                diff = abs(r_minutes - slot_target_minutes)
                if diff < cgm_min_diff and diff <= 30:
                    cgm_min_diff = diff
                    cgm_match = r

            if cgm_match:
                slot_data['value'] = cgm_match['value']
                slot_data['status'] = 'measured'
                slot_data['cgm'] = True
                result = settings.check_glucose_compliance(cgm_match['value'], '空腹' if slot['key'] == 'fasting' else '餐后2小时')
                slot_data['compliance'] = result['level']
                measured_count += 1
            elif chosen:
                slot_data['value'] = chosen['value']
                slot_data['status'] = 'predicted' if chosen['is_predicted'] else 'measured'
                result = settings.check_glucose_compliance(chosen['value'], chosen['type'])
                slot_data['compliance'] = result['level']
                if slot_data['status'] == 'measured': measured_count += 1
                else: predicted_count += 1
            overview.append(slot_data)

        # 运动
        c.execute("""
            SELECT type, distance, calories, duration, heart_rate, pace, cadence, vo2max, max_heart_rate, steps, timestamp
            FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
            AND (type IN ('运动','跑步','走路','骑行','游泳','健身') OR type LIKE '%跑%' OR type LIKE '%走%' OR type LIKE '%骑%')
            ORDER BY timestamp DESC, vo2max DESC LIMIT 1
        """, (current_user_id, day_start, day_end))
        ex_row = c.fetchone()
        exercise = None
        if ex_row:
            exercise = {k: ex_row[k] for k in ['type','distance','calories','duration','heart_rate','pace','cadence','vo2max','max_heart_rate','steps']}
            exercise['time'] = ex_row['timestamp'].split(' ')[1][:5] if ' ' in ex_row['timestamp'] else ''

        # 血压
        c.execute("""
            SELECT systolic_pressure, diastolic_pressure, pulse_rate, spo2, timestamp
            FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
            AND systolic_pressure IS NOT NULL ORDER BY timestamp DESC LIMIT 1
        """, (current_user_id, day_start, day_end))
        bp_row = c.fetchone()
        bp = None
        if bp_row:
            bp = {'systolic': bp_row['systolic_pressure'], 'diastolic': bp_row['diastolic_pressure'],
                  'heart_rate': bp_row['pulse_rate'], 'spo2': bp_row['spo2'],
                  'time': bp_row['timestamp'].split(' ')[1][:5] if ' ' in bp_row['timestamp'] else ''}

        # 体重
        c.execute("""
            SELECT weight, bmi, timestamp FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
            AND weight > 0 ORDER BY timestamp DESC LIMIT 1
        """, (current_user_id, day_start, day_end))
        w_row = c.fetchone()
        weight = None
        if w_row:
            bmi_cat = settings.get_bmi_category(w_row['bmi'])
            weight = {'weight': w_row['weight'], 'bmi': w_row['bmi'],
                      'bmi_category': bmi_cat,
                      'time': w_row['timestamp'].split(' ')[1][:5] if ' ' in w_row['timestamp'] else ''}

        # 用药
        med_plans = []
        c.execute("""
            SELECT id, medication_name, dosage, dose_quantity, dose_unit, times_per_day, timing_notes, frequency, frequency_detail, start_date, category, med_type
            FROM medication_plans WHERE user_id = ? AND is_active = 1
            AND (start_date IS NULL OR start_date <= ?)
            AND (end_date IS NULL OR end_date >= ?)
            ORDER BY
                CASE WHEN frequency = 'daily' THEN 0 ELSE 1 END,
                CASE WHEN timing_notes LIKE '%早%' OR timing_notes LIKE '%晨%' THEN 0
                     WHEN timing_notes LIKE '%午%' OR timing_notes LIKE '%中%' THEN 1
                     WHEN timing_notes LIKE '%晚%' OR timing_notes LIKE '%餐后%' THEN 2
                     WHEN timing_notes LIKE '%睡%' OR timing_notes LIKE '%夜%' THEN 3
                     ELSE 4 END,
                medication_name ASC
        """, (current_user_id, date_str, date_str))
        for row in c.fetchall():
            freq = row['frequency'] or 'daily'
            freq_detail = row['frequency_detail'] or ''
            include = False
            if freq == 'daily': include = True
            elif freq == 'every_n_days' and freq_detail:
                try:
                    n = int(freq_detail)
                    plan_start = datetime.datetime.strptime(
                        row['start_date'] or date_str, '%Y-%m-%d').date()
                    t_date = target_date.date() if hasattr(target_date, 'date') else target_date
                    include = ((t_date - plan_start).days % n == 0)
                except (ValueError, TypeError):
                    include = True
            elif freq == 'weekdays': include = weekday_name not in ('Saturday', 'Sunday')
            elif freq == 'weekly': include = weekday_name == freq_detail if freq_detail else weekday_name == 'Monday'
            elif freq == 'biweekly' and freq_detail:
                plan_start = datetime.datetime.strptime(
                    row['start_date'] or date_str, '%Y-%m-%d').date()
                weeks_diff = (target_date.date() - plan_start).days // 7 if hasattr(target_date, 'date') else (target_date - plan_start).days // 7
                include = (weekday_name == freq_detail) and (weeks_diff % 2 == 0)
            elif freq == 'monthly':
                try:
                    allowed_days = [int(d.strip()) for d in freq_detail.split(',')]
                    include = day_of_month in allowed_days
                except (ValueError, AttributeError):
                    include = day_of_month == 1
            if include:
                dq = row['dose_quantity'] or '1'
                du = row['dose_unit'] or '片'
                dosage_display = row['dosage']
                if dosage_display:
                    dosage_display = f"{dosage_display} ×{dq}{du}" if dq != '1' else dosage_display
                med_plans.append({'id': row['id'], 'name': row['medication_name'], 'dosage': dosage_display,
                                  'dose_quantity': dq, 'dose_unit': du,
                                  'timing': row['timing_notes'], 'times': row['times_per_day'] or 1,
                                  'frequency': freq, 'frequency_detail': freq_detail,
                                  'category': row['category'] or 'long_term',
                                  'med_type': row['med_type'] or ''})

        c.execute("SELECT plan_id, COUNT(*) as count FROM medication_logs WHERE user_id = ? AND log_date = ? GROUP BY plan_id",
                  (current_user_id, date_str))
        taken_logs = {row['plan_id']: row['count'] for row in c.fetchall()}

        # 查询当日临时用药
        c.execute("""
            SELECT medication_name, notes, timestamp
            FROM records
            WHERE user_id = ? AND DATE(timestamp) = ?
            AND medication_name IS NOT NULL AND medication_name != ''
            ORDER BY timestamp ASC
        """, (current_user_id, date_str))
        temp_meds = []
        for r in c.fetchall():
            temp_meds.append({
                'name': r['medication_name'],
                'notes': r['notes'],
                'time': r['timestamp'].split(' ')[1][:5] if ' ' in r['timestamp'] else ''
            })

        # 计算达标率
        ok_count = 0
        for s in overview:
            if s['value'] and s['status'] == 'measured' and s['compliance'] in ('optimal', 'acceptable'):
                ok_count += 1
        compliance = int((ok_count / measured_count * 100)) if measured_count > 0 else 0
        compliance_badge = settings.get_badge_for_rate(compliance)

        return jsonify({
            'date': date_str,
            'overview': overview,
            'completion': {'measured': measured_count, 'predicted': predicted_count, 'total': len(today_schedule)},
            'compliance': compliance,
            'compliance_badge': compliance_badge,
            'exercise': exercise,
            'bp': bp,
            'weight': weight,
            'med_status': {'plans': med_plans, 'taken_details': taken_logs, 'temp_medications': temp_meds}
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
