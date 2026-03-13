import sqlite3
import datetime
import json
import traceback
import settings
from core.config import DB_NAME


def get_dashboard_stats(db, user_id):
    """获取仪表盘所需的所有统计数据"""
    c = db.cursor()
    seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    today = datetime.datetime.now()
    today_str = today.strftime('%Y-%m-%d')
    today_start = today_str + ' 00:00:00'
    today_end = today_str + ' 23:59:59'
    weekday_name = today.strftime('%A')
    day_of_month = today.day

    # === 1. 基础统计 ===
    c.execute("SELECT COUNT(*) FROM records WHERE user_id = ?", (user_id,))
    total_records = c.fetchone()[0]

    # === 2. 血糖统计（7天） ===
    c.execute("""
        SELECT
            AVG(CASE WHEN (type LIKE '%空腹%') THEN value END) as avg_fasting,
            AVG(CASE WHEN type LIKE '%餐后2小时%' THEN value END) as avg_post2h,
            MAX(value) as max_glucose,
            MIN(value) as min_glucose
        FROM records
        WHERE user_id = ? AND value > 0 AND is_predicted = 0
        AND systolic_pressure IS NULL AND timestamp > ?
    """, (user_id, seven_days_ago))
    glucose_stats = c.fetchone()

    max_glucose_detail = {'timestamp': '', 'type': ''}
    min_glucose_detail = {'timestamp': '', 'type': ''}
    if glucose_stats[2]:
        c.execute("""SELECT timestamp, type FROM records
            WHERE user_id = ? AND value = ? AND value > 0 AND is_predicted = 0
            AND systolic_pressure IS NULL AND timestamp > ?
            ORDER BY timestamp DESC LIMIT 1""",
            (user_id, glucose_stats[2], seven_days_ago))
        row = c.fetchone()
        if row: max_glucose_detail = {'timestamp': row[0], 'type': row[1]}
    if glucose_stats[3]:
        c.execute("""SELECT timestamp, type FROM records
            WHERE user_id = ? AND value = ? AND value > 0 AND is_predicted = 0
            AND systolic_pressure IS NULL AND timestamp > ?
            ORDER BY timestamp ASC LIMIT 1""",
            (user_id, glucose_stats[3], seven_days_ago))
        row = c.fetchone()
        if row: min_glucose_detail = {'timestamp': row[0], 'type': row[1]}

    # === 3. 运动统计（7天） ===
    c.execute("""
        SELECT
            SUM(distance) as total_distance,
            SUM(CASE WHEN (type = '跑步' OR type = '运动' OR distance IS NOT NULL) AND calories > 0 THEN calories END) as total_cal,
            AVG(CASE WHEN heart_rate IS NOT NULL AND (type = '跑步' OR type = '运动') THEN heart_rate END) as avg_hr,
            COUNT(DISTINCT DATE(timestamp)) as exercise_count
        FROM records
        WHERE user_id = ? AND timestamp > ?
        AND (distance IS NOT NULL OR type IN ('跑步', '运动'))
    """, (user_id, seven_days_ago))
    exercise_stats = c.fetchone()

    # VO2max
    c.execute("""SELECT vo2max, timestamp FROM records
        WHERE user_id = ? AND vo2max IS NOT NULL AND vo2max > 0
        ORDER BY timestamp DESC LIMIT 1""", (user_id,))
    vo2max_row = c.fetchone()
    latest_vo2max = vo2max_row[0] if vo2max_row else None
    latest_vo2max_date = vo2max_row[1] if vo2max_row else None
    prev_vo2max = None
    if vo2max_row:
        c.execute("""SELECT vo2max FROM records
            WHERE user_id = ? AND vo2max IS NOT NULL AND vo2max > 0
            AND timestamp < ? ORDER BY timestamp DESC LIMIT 1""", (user_id, vo2max_row[1]))
        pv = c.fetchone()
        if pv: prev_vo2max = pv[0]

    # === 4. 血压统计（7天） ===
    c.execute("""
        SELECT
            AVG(systolic_pressure) as avg_sys,
            AVG(diastolic_pressure) as avg_dia,
            COUNT(*) as bp_count,
            MAX(systolic_pressure) as max_sys,
            MAX(diastolic_pressure) as max_dia,
            MIN(systolic_pressure) as min_sys,
            MIN(diastolic_pressure) as min_dia
        FROM records
        WHERE user_id = ? AND systolic_pressure IS NOT NULL
        AND systolic_pressure > 0 AND diastolic_pressure > 0
        AND timestamp > ?
    """, (user_id, seven_days_ago))
    bp_stats = c.fetchone()

    bp_max_date = bp_min_date = '-'
    bp_max_timestamp = ''
    bp_min_timestamp = ''
    if bp_stats[3]:
        c.execute("SELECT timestamp FROM records WHERE user_id = ? AND systolic_pressure = ? AND timestamp > ? AND systolic_pressure IS NOT NULL LIMIT 1",
                  (user_id, bp_stats[3], seven_days_ago))
        row = c.fetchone()
        if row:
            bp_max_timestamp = row[0]
            bp_max_date = row[0][:10]
    if bp_stats[5]:
        c.execute("SELECT timestamp FROM records WHERE user_id = ? AND systolic_pressure = ? AND timestamp > ? AND systolic_pressure IS NOT NULL LIMIT 1",
                  (user_id, bp_stats[5], seven_days_ago))
        row = c.fetchone()
        if row:
            bp_min_timestamp = row[0]
            bp_min_date = row[0][:10]

    # === 5. 体重/BMI ===
    c.execute("""
        SELECT weight, bmi, timestamp FROM records
        WHERE user_id = ? AND weight IS NOT NULL AND weight > 0
        ORDER BY timestamp DESC LIMIT 1
    """, (user_id,))
    latest_weight_row = c.fetchone()
    latest_weight = latest_weight_row[0] if latest_weight_row else None
    latest_bmi_raw = latest_weight_row[1] if latest_weight_row else None
    latest_weight_date = latest_weight_row[2] if latest_weight_row else None

    c.execute("""
        SELECT AVG(weight) FROM records
        WHERE user_id = ? AND weight IS NOT NULL AND weight > 0 AND timestamp > ?
    """, (user_id, seven_days_ago))
    avg_weight_7d = c.fetchone()[0]

    # 7天体重变化
    weight_change_cutoff = seven_days_ago
    weight_change_default = None
    if latest_weight:
        c.execute("""SELECT weight FROM records WHERE user_id = ? AND weight IS NOT NULL AND weight > 0
            AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1""", (user_id, weight_change_cutoff))
        old_weight_row = c.fetchone()
        if old_weight_row:
            weight_change_default = round(latest_weight - old_weight_row[0], 1)

    user_config = settings.load_config()
    latest_bmi = settings.calculate_bmi(latest_weight, user_config.get('height')) if latest_weight else (round(latest_bmi_raw, 1) if latest_bmi_raw else None)
    bmi_category = settings.get_bmi_category(latest_bmi)

    # 今日体重
    c.execute("""SELECT weight, bmi, timestamp FROM records
        WHERE user_id = ? AND timestamp BETWEEN ? AND ? AND weight IS NOT NULL AND weight > 0
        ORDER BY timestamp DESC LIMIT 1""", (user_id, today_start, today_end))
    today_weight_row = c.fetchone()
    today_weight = None
    if today_weight_row:
        today_weight = {
            'weight': today_weight_row[0],
            'bmi': today_weight_row[1],
            'bmi_category': settings.get_bmi_category(today_weight_row[1]),
            'time': today_weight_row[2].split(' ')[1][:5] if ' ' in today_weight_row[2] else ''
        }

    # === 6. 达标率 ===
    c.execute("""
        SELECT value, type FROM records
        WHERE user_id = ? AND timestamp > ? AND value > 0 AND is_predicted = 0 AND systolic_pressure IS NULL
    """, (user_id, seven_days_ago))
    recent_glucose = c.fetchall()
    total_glucose = len(recent_glucose)
    ok_count = sum(1 for row in recent_glucose if settings.check_glucose_compliance(row[0], row[1] or '')['is_compliant'])
    compliance = int((ok_count / total_glucose * 100)) if total_glucose > 0 else 0

    # === 7. 今日概览 ===
    today_schedule = [
        {'key': 'fasting', 'name': '空腹', 'time': '07:15', 'icon': 'sunrise'},
        {'key': 'post_exercise', 'name': '运动后', 'time': '08:45', 'icon': 'bicycle'},
        {'key': 'post_breakfast', 'name': '早餐后2h', 'time': '11:00', 'icon': 'cup-hot'},
        {'key': 'post_lunch', 'name': '午餐后2h', 'time': '14:30', 'icon': 'sun'},
        {'key': 'pre_dinner', 'name': '晚饭前', 'time': '17:30', 'icon': 'clock'},
        {'key': 'post_dinner', 'name': '晚餐后2h', 'time': '20:00', 'icon': 'moon-stars'},
        {'key': 'bedtime', 'name': '睡前', 'time': '22:00', 'icon': 'moon'}
    ]

    c.execute("""
        SELECT value, type, timestamp, is_predicted
        FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
        AND value > 0 AND systolic_pressure IS NULL
        ORDER BY timestamp ASC, is_predicted ASC
    """, (user_id, today_start, today_end))
    today_records = c.fetchall()

    today_overview = []
    for slot in today_schedule:
        slot_data = {
            'key': slot['key'], 'name': slot['name'], 'time': slot['time'], 'icon': slot['icon'],
            'value': None, 'is_predicted': False, 'status': 'pending', 'compliance': None
        }
        measured_match = predicted_match = None
        for record in today_records:
            rt = record['type'] or ''
            record_time = record['timestamp'].split(' ')[1][:5] if ' ' in record['timestamp'] else ''
            rh = -1
            if record_time and ':' in record_time:
                try: rh = int(record_time.split(':')[0])
                except: rh = -1
            is_pred = record['is_predicted']
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
                if not is_pred and measured_match is None: measured_match = record
                elif is_pred and predicted_match is None: predicted_match = record

        # CGM 匹配
        cgm_match = None
        cgm_min_diff = float('inf')
        slot_target_minutes = int(slot['time'].split(':')[0]) * 60 + int(slot['time'].split(':')[1])
        for record in today_records:
            if (record['type'] or '') != 'CGM': continue
            record_time = record['timestamp'].split(' ')[1][:5] if ' ' in record['timestamp'] else ''
            if not record_time or ':' not in record_time: continue
            try:
                parts = record_time.split(':')
                r_minutes = int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError): continue
            diff = abs(r_minutes - slot_target_minutes)
            if diff < cgm_min_diff and diff <= 30:
                cgm_min_diff = diff
                cgm_match = record

        if cgm_match:
            slot_data['value'] = cgm_match['value']
            slot_data['status'] = 'measured'
            slot_data['cgm'] = True
            result = settings.check_glucose_compliance(cgm_match['value'], '空腹' if slot['key'] == 'fasting' else '餐后2小时')
            slot_data['compliance'] = result['level']
        elif measured_match:
            slot_data['value'] = measured_match['value']
            slot_data['status'] = 'measured'
            result = settings.check_glucose_compliance(measured_match['value'], measured_match['type'])
            slot_data['compliance'] = result['level']
        elif predicted_match:
            slot_data['value'] = predicted_match['value']
            slot_data['is_predicted'] = True
            slot_data['status'] = 'predicted'
            result = settings.check_glucose_compliance(predicted_match['value'], predicted_match['type'])
            slot_data['compliance'] = result['level']

        today_overview.append(slot_data)

    measured_count = sum(1 for s in today_overview if s['status'] == 'measured')
    predicted_count = sum(1 for s in today_overview if s['status'] == 'predicted')
    today_completion = {
        'measured': measured_count,
        'predicted': predicted_count,
        'total': len(today_schedule),
        'percentage': int(measured_count / len(today_schedule) * 100)
    }

    # === 8. 今日运动 ===
    c.execute("""
        SELECT type, distance, calories, duration, heart_rate, pace, cadence, vo2max, max_heart_rate, steps, timestamp
        FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
        AND (type IN ('运动','跑步','走路','骑行','游泳','健身') OR type LIKE '%跑%' OR type LIKE '%走%' OR type LIKE '%骑%')
        ORDER BY timestamp DESC, vo2max DESC LIMIT 1
    """, (user_id, today_start, today_end))
    today_ex_row = c.fetchone()
    today_exercise = None
    if today_ex_row:
        today_exercise = {k: today_ex_row[k] for k in ['type','distance','calories','duration','heart_rate','pace','cadence','vo2max','max_heart_rate','steps']}
        today_exercise['time'] = today_ex_row['timestamp'].split(' ')[1][:5] if ' ' in today_ex_row['timestamp'] else ''

    # === 9. 今日血压 ===
    c.execute("""
        SELECT systolic_pressure, diastolic_pressure, pulse_rate, spo2, timestamp
        FROM records WHERE user_id = ? AND timestamp BETWEEN ? AND ?
        AND systolic_pressure IS NOT NULL ORDER BY timestamp DESC LIMIT 1
    """, (user_id, today_start, today_end))
    today_bp_row = c.fetchone()
    today_bp = None
    if today_bp_row:
        today_bp = {
            'systolic': today_bp_row['systolic_pressure'],
            'diastolic': today_bp_row['diastolic_pressure'],
            'heart_rate': today_bp_row['pulse_rate'],
            'spo2': today_bp_row['spo2'],
            'time': today_bp_row['timestamp'].split(' ')[1][:5] if ' ' in today_bp_row['timestamp'] else ''
        }

    # === 10. 用药情况 ===
    c.execute("""
        SELECT id, medication_name, dosage, dose_quantity, dose_unit, times_per_day, timing_notes,
               frequency, frequency_detail, start_date, category, med_type
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
    """, (user_id, today_str, today_str))
    all_meds = c.fetchall()

    active_medications = []
    today_med_plans = []
    for row in all_meds:
        freq = row['frequency'] or 'daily'
        freq_detail = row['frequency_detail'] or ''
        include = False
        if freq == 'daily': include = True
        elif freq == 'every_n_days' and freq_detail:
            try:
                n = int(freq_detail)
                plan_start = datetime.datetime.strptime(row['start_date'] or today_str, '%Y-%m-%d').date()
                include = ((today.date() - plan_start).days % n == 0)
            except (ValueError, TypeError): include = True
        elif freq == 'weekdays': include = weekday_name not in ('Saturday', 'Sunday')
        elif freq == 'weekly': include = weekday_name == freq_detail if freq_detail else weekday_name == 'Monday'
        elif freq == 'biweekly' and freq_detail:
            plan_start = datetime.datetime.strptime(row['start_date'] or today_str, '%Y-%m-%d').date()
            weeks_diff = (today.date() - plan_start).days // 7
            include = (weekday_name == freq_detail) and (weeks_diff % 2 == 0)
        elif freq == 'monthly':
            try:
                allowed_days = [int(d.strip()) for d in freq_detail.split(',')]
                include = day_of_month in allowed_days
            except (ValueError, AttributeError): include = day_of_month == 1
        else: include = True

        if include:
            dq = row['dose_quantity'] or '1'
            du = row['dose_unit'] or '片'
            dosage_display = row['dosage']
            if dosage_display:
                dosage_display = f"{dosage_display} ×{dq}{du}" if dq != '1' else dosage_display
            active_medications.append({
                'name': row['medication_name'], 'dosage': dosage_display,
                'dose_quantity': dq, 'dose_unit': du,
                'times': row['times_per_day'], 'timing': row['timing_notes'],
                'frequency': freq, 'frequency_detail': freq_detail,
                'category': row['category'] or 'long_term'
            })
            today_med_plans.append({
                'id': row['id'], 'name': row['medication_name'], 'dosage': dosage_display,
                'dose_quantity': dq, 'dose_unit': du,
                'times': row['times_per_day'], 'timing': row['timing_notes'],
                'frequency': freq, 'frequency_detail': freq_detail,
                'category': row['category'] or 'long_term',
                'med_type': row['med_type'] or ''
            })

    c.execute("SELECT plan_id, COUNT(*) as count FROM medication_logs WHERE user_id = ? AND log_date = ? GROUP BY plan_id",
              (user_id, today_str))
    taken_logs = {row['plan_id']: row['count'] for row in c.fetchall()}

    # 临时用药
    c.execute("""SELECT medication_name, notes, timestamp FROM records
        WHERE user_id = ? AND DATE(timestamp) = ?
        AND medication_name IS NOT NULL AND medication_name != '' ORDER BY timestamp ASC""", (user_id, today_str))
    temp_meds = []
    for r in c.fetchall():
        temp_meds.append({
            'name': r['medication_name'], 'notes': r['notes'],
            'time': r['timestamp'].split(' ')[1][:5] if ' ' in r['timestamp'] else ''
        })

    today_med_status = {
        'plans': today_med_plans,
        'taken_count': sum(taken_logs.values()) if taken_logs else 0,
        'total_required': sum(p['times'] for p in today_med_plans),
        'taken_details': taken_logs,
        'temp_medications': temp_meds
    }

    # === 11. 健康分析 ===
    c.execute("SELECT * FROM health_analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
    latest_analysis_row = c.fetchone()
    latest_analysis = None
    if latest_analysis_row:
        latest_analysis = dict(latest_analysis_row)
        if latest_analysis.get('recommendations'):
            try: latest_analysis['recommendations'] = json.loads(latest_analysis['recommendations'])
            except (json.JSONDecodeError, TypeError, ValueError): latest_analysis['recommendations'] = []
        score = latest_analysis.get('health_score', 0) or 0
        if score >= 90: latest_analysis['score_label'] = '优秀'
        elif score >= 80: latest_analysis['score_label'] = '良好'
        elif score >= 70: latest_analysis['score_label'] = '一般'
        elif score >= 60: latest_analysis['score_label'] = '需改善'
        else: latest_analysis['score_label'] = '需关注'
        days_val = latest_analysis.get('days') or 7
        latest_analysis['days_label'] = f'近{days_val}天'

    return {
        'total_records': total_records,
        'today_str': today_str,
        'user': user_config,
        'compliance': compliance,
        'compliance_badge': settings.get_badge_for_rate(compliance),
        'glucose_targets': settings.GLUCOSE_TARGETS,
        'badge_system': settings.BADGE_SYSTEM,

        # 今日概览
        'today_overview': today_overview,
        'today_completion': today_completion,
        'today_exercise': today_exercise,
        'today_bp': today_bp,
        'today_weight': today_weight,
        'today_med_status': today_med_status,

        # 血糖统计（7天）
        'avg_fasting_7d': round(glucose_stats[0], 1) if glucose_stats[0] else 0,
        'avg_post2h_7d': round(glucose_stats[1], 1) if glucose_stats[1] else 0,
        'max_glucose_7d': round(glucose_stats[2], 1) if glucose_stats[2] else 0,
        'min_glucose_7d': round(glucose_stats[3], 1) if glucose_stats[3] else 0,
        'max_glucose_detail': max_glucose_detail,
        'min_glucose_detail': min_glucose_detail,

        # 运动统计（7天）
        'total_distance_7d': round(exercise_stats[0], 1) if exercise_stats[0] else 0,
        'total_exercise_cal_7d': int(exercise_stats[1]) if exercise_stats[1] else 0,
        'avg_heart_rate_7d': round(exercise_stats[2]) if exercise_stats[2] else 0,
        'exercise_count_7d': exercise_stats[3] or 0,
        'latest_vo2max': latest_vo2max,
        'latest_vo2max_date': latest_vo2max_date,
        'prev_vo2max': prev_vo2max,

        # 血压统计（7天）
        'avg_systolic_7d': round(bp_stats[0]) if bp_stats[0] else 0,
        'avg_diastolic_7d': round(bp_stats[1]) if bp_stats[1] else 0,
        'bp_count_7d': bp_stats[2] if bp_stats[2] else 0,
        'bp_max_sys': bp_stats[3] if bp_stats[3] else 0,
        'bp_max_dia': bp_stats[4] if bp_stats[4] else 0,
        'bp_max_date': bp_max_date,
        'bp_max_timestamp': bp_max_timestamp,
        'bp_min_sys': bp_stats[5] if bp_stats[5] else 0,
        'bp_min_dia': bp_stats[6] if bp_stats[6] else 0,
        'bp_min_date': bp_min_date,
        'bp_min_timestamp': bp_min_timestamp,

        # 用药
        'active_medications': active_medications,

        # 体重/BMI
        'latest_weight': round(latest_weight, 1) if latest_weight else None,
        'latest_bmi': round(latest_bmi, 1) if latest_bmi else None,
        'latest_weight_date': latest_weight_date,
        'avg_weight_7d': round(avg_weight_7d, 1) if avg_weight_7d else None,
        'weight_change_default': weight_change_default,
        'bmi_category': bmi_category,
        'target_weight': user_config.get('target_weight'),

        # 健康分析
        'latest_analysis': latest_analysis,
    }
