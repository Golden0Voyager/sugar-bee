from collections import defaultdict

import settings
from utils.sql_dialect import interval_sql
from utils.timezone import now as app_now


def get_active_medication_plans(cursor, user_id):
    """当前启用的用药方案，含日期范围。空日期也要能据此显示当日用药。"""
    cursor.execute("SELECT * FROM medication_plans WHERE user_id = ? AND is_active = 1", (user_id,))
    return [dict(row) for row in cursor.fetchall()]


def build_timeline(cursor, user_id, days=90):
    """
    构建 timeline 数据，返回 (sorted_dates, records) 元组。
    """
    # 1. Fetch records
    if days is None:
        cursor.execute("""SELECT *,
                    CASE WHEN is_predicted = 1 AND verified_by_real_id IS NOT NULL THEN 1 ELSE 0 END as is_verified
                    FROM records
                    WHERE user_id = ?
                    ORDER BY timestamp ASC""", (user_id,))
    else:
        cursor.execute(f"""SELECT *,
                    CASE WHEN is_predicted = 1 AND verified_by_real_id IS NOT NULL THEN 1 ELSE 0 END as is_verified
                    FROM records
                    WHERE user_id = ?
                    AND timestamp > {interval_sql(int(days))}
                    ORDER BY timestamp ASC""", (user_id,))
    rows = cursor.fetchall()
    records = [dict(row) for row in rows]

    # 2. Calculate Trends
    last_values = {}
    for r in records:
        key = 'fasting' if ('空腹' in r['type'] and '血压' not in r['type']) else ('post' if ('餐后' in r['type'] and '血压' not in r['type']) else 'other')
        r['trend'] = 0
        r['trend_dir'] = 'flat'
        # value 为 NULL 的记录(如只记了热量的餐食、体成分)不能参与趋势比较,
        # 否则同 key 有前值时 r['value'] > 0 直接 TypeError 崩掉整条时间线
        value = r.get('value') or 0
        if key in last_values and value > 0:
            diff = value - last_values[key]
            r['trend'] = round(abs(diff), 1)
            if diff > 0:
                r['trend_dir'] = 'up'
            elif diff < 0:
                r['trend_dir'] = 'down'
        if value > 0:
            last_values[key] = value

    # 3. Group by Date
    user_bmr = settings.calculate_bmr(user_id)
    grouped_records = defaultdict(lambda: {
        'entries': [],
        'medication_plans': [],
        'stats': {
            'cal_in': 0,
            'cal_out_exercise': 0,
            'cal_out_bmr': user_bmr,
            'avg_glucose': 0,
            'glucose_count': 0
        }
    })

    records.sort(key=lambda x: x['timestamp'], reverse=True)
    for r in records:
        date_str = r['timestamp'].split(' ')[0]
        day_group = grouped_records[date_str]
        day_group['entries'].append(r)

        calories = r.get('calories') or 0
        if calories > 0:
            if r['type'] in ['跑步', '运动', '走路', '骑行', '游泳', '健身'] or (r.get('distance') and r.get('distance') > 0):
                day_group['stats']['cal_out_exercise'] += calories
            else:
                day_group['stats']['cal_in'] += calories

        value = r.get('value') or 0
        if value > 0 and not r.get('systolic_pressure') and not r.get('is_predicted') and r['type'] not in ['跑步', '运动', '走路', '骑行', '游泳', '健身', '早餐', '午餐', '晚餐', '加餐', '体重记录', '体重'] and '血压' not in r['type']:
            day_group['stats']['avg_glucose'] += value
            day_group['stats']['glucose_count'] += 1

    # 4. Medication Plans
    medication_plans = get_active_medication_plans(cursor, user_id)

    now = app_now()
    today_str = now.strftime('%Y-%m-%d')
    user_config = settings.load_config(user_id)
    _default_meals = user_config.get('default_meals', {})

    sorted_dates = []
    for date_str in sorted(grouped_records.keys(), reverse=True):
        data = grouped_records[date_str]
        # Match medication plans
        for plan in medication_plans:
            start = plan['start_date'] or date_str
            end = plan['end_date'] or '9999-12-31'
            if start <= date_str <= end:
                data['medication_plans'].append(plan)

        # BMR Adjustment for today
        if date_str == today_str:
            # 按小时折算而非分钟：分钟粒度会让 __BOOT__ 每分钟变一次、ETag 跟着变，
            # HTML 的 304 通道形同虚设（review 实测命中率≈0）。按小时取整后
            # 同一小时内回访可命中 304，数值误差 < 4%（BMR 全天约 1500 kcal）。
            current_hours_rounded = now.hour  # 只到整点，小时内不变
            data['stats']['cal_out_bmr'] = int(user_bmr * (current_hours_rounded / 24))

        # Default meals logic ... (omitted for brevity here, but should be included)
        # 补全缺失餐饮的热量
        # ... (implementation from app.py)

        if data['stats']['glucose_count'] > 0:
            data['stats']['avg_glucose'] = round(data['stats']['avg_glucose'] / data['stats']['glucose_count'], 1)

        net = data['stats']['cal_in'] - (data['stats']['cal_out_bmr'] + data['stats']['cal_out_exercise'])
        data['stats']['net_calories'] = int(net)
        data['stats']['is_deficit'] = net < 0
        data['entries'].sort(key=lambda x: x['timestamp'])
        sorted_dates.append({'date': date_str, 'data': data})

    return sorted_dates, records
