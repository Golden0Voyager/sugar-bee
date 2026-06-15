import datetime
import re
import json
import traceback
from ai_client import call_ai, AI_AVAILABLE
import settings


def link_prediction_to_real_record(db, real_record_id, user_id, record_date, record_type, real_value, record_timestamp=None):
    """
    关联预测记录到真实记录，计算预测误差
    """
    if not settings.is_valid_glucose(real_value):
        print(f"WARNING: Invalid glucose value {real_value}, skipping prediction link")
        return None

    try:
        c = db.cursor()
        record_hour = None
        if record_timestamp:
            try:
                if ' ' in record_timestamp:
                    time_part = record_timestamp.split(' ')[1]
                    record_hour = int(time_part.split(':')[0])
            except (ValueError, IndexError):
                pass

        if '空腹' in record_type and '血压' not in record_type:
            type_condition = "type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%')"
        elif '餐后1小时' in record_type:
            type_condition = "type LIKE '%餐后1小时%'"
        elif '早餐后' in record_type:
            type_condition = "type LIKE '%早餐后%'"
        elif '午餐后' in record_type:
            type_condition = "type LIKE '%午餐后%'"
        elif '晚餐后' in record_type:
            type_condition = "type LIKE '%晚餐后%'"
        elif '餐后2小时' in record_type or '餐后' in record_type:
            if record_hour is not None:
                if 10 <= record_hour < 13:
                    type_condition = "(type LIKE '%早餐后%' OR (type LIKE '%餐后%' AND type NOT LIKE '%午餐%' AND type NOT LIKE '%晚餐%' AND type NOT LIKE '%1小时%'))"
                elif 13 <= record_hour < 17:
                    type_condition = "(type LIKE '%午餐后%' OR type = '餐后2小时')"
                elif 17 <= record_hour < 23:
                    type_condition = "(type LIKE '%晚餐后%')"
                else:
                    type_condition = "type LIKE '%餐后%' AND type NOT LIKE '%1小时%'"
            else:
                type_condition = "type LIKE '%餐后%' AND type NOT LIKE '%1小时%'"
        elif '餐前' in record_type:
            type_condition = "type LIKE '%餐前%'"
        elif '睡前' in record_type:
            type_condition = "type LIKE '%睡前%'"
        else:
            type_condition = f"type LIKE '%{record_type}%' AND type NOT LIKE '%血压%'"

        if record_timestamp:
            c.execute(f"""
                SELECT id, value, timestamp FROM records
                WHERE user_id = ? AND DATE(timestamp) = ? AND ({type_condition})
                AND is_predicted = 1 AND verified_by_real_id IS NULL AND value > 0 AND systolic_pressure IS NULL
                ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?))
                LIMIT 1
            """, (user_id, record_date, record_timestamp))
        else:
            c.execute(f"""
                SELECT id, value FROM records
                WHERE user_id = ? AND DATE(timestamp) = ? AND ({type_condition})
                AND is_predicted = 1 AND verified_by_real_id IS NULL AND value > 0 AND systolic_pressure IS NULL
                ORDER BY timestamp ASC
                LIMIT 1
            """, (user_id, record_date))

        prediction = c.fetchone()
        if prediction:
            pred_id = prediction[0]
            pred_value = prediction[1]
            error = real_value - pred_value
            c.execute("""
                UPDATE records SET verified_by_real_id = ?, prediction_error = ?
                WHERE id = ?
            """, (real_record_id, error, pred_id))
            return {'predicted_value': pred_value, 'error': error}
        return None
    except Exception as e:
        print(f"ERROR in link_prediction_to_real_record: {e}")
        return None


def predict_morning_fpg(db, user_id=1):
    """
    自动预测当天早晨空腹血糖 (FPG)
    基于前一天综合数据：血糖波动、饮食热量、能量平衡、近期趋势、用药情况
    """
    if not AI_AVAILABLE:
        return None
    try:
        now = datetime.datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        c = db.cursor()

        # 1. 检查今天是否已有预测
        c.execute("""
            SELECT id FROM records
            WHERE user_id = ? AND DATE(timestamp) = ?
            AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%'))
            AND is_predicted = 1 AND value > 0
        """, (user_id, today_str,))
        if c.fetchone():
            return

        yesterday = now - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y-%m-%d')

        # 2. 昨日血糖数据
        c.execute("""SELECT value, type, timestamp FROM records
            WHERE user_id = ? AND DATE(timestamp) = ? AND value > 0
            AND is_predicted = 0 AND systolic_pressure IS NULL
            ORDER BY timestamp ASC""", (user_id, yesterday_str,))
        yesterday_glucose = c.fetchall()

        # 3. 昨日饮食热量
        c.execute("""SELECT type, calories, timestamp, carbs_grams, gi_value FROM records
            WHERE user_id = ? AND DATE(timestamp) = ? AND calories > 0""", (user_id, yesterday_str,))
        yesterday_calories = c.fetchall()

        cal_in = sum(row[1] for row in yesterday_calories if row[0] not in ['跑步', '运动', '走路', '骑行', '游泳', '健身'])
        cal_out_exercise = sum(row[1] for row in yesterday_calories if row[0] in ['跑步', '运动', '走路', '骑行', '游泳', '健身'])

        user_config = settings.load_config(user_id)
        default_meals = user_config.get('default_meals', {})

        has_breakfast = has_lunch = has_dinner = False
        total_carbs = 0.0
        gi_values = []

        for row in yesterday_calories:
            record_type = row[0] or ''
            timestamp = row[2] or ''
            if row[3] is not None:
                try:
                    total_carbs += float(row[3])
                except (ValueError, TypeError):
                    pass
            if row[4] is not None:
                gi_values.append(row[4])
            hour = int(timestamp.split(' ')[1].split(':')[0]) if timestamp and ' ' in timestamp else 0
            if '早餐' in record_type or (6 <= hour < 10):
                has_breakfast = True
            elif '午餐' in record_type or (11 <= hour < 14):
                has_lunch = True
            elif '晚餐' in record_type or (17 <= hour < 21):
                has_dinner = True

        default_cal = 0
        for meal, config in default_meals.items():
            if not locals()[f'has_{meal}'] and config.get('enabled', True):
                default_cal += config.get('calories', 300 if meal == 'breakfast' else 500)
                try:
                    total_carbs += float(config.get('carbs_grams', 45 if meal == 'breakfast' else 75))
                except (ValueError, TypeError):
                    pass
                if config.get('gi_value'):
                    gi_values.append(config.get('gi_value'))
        cal_in += default_cal

        avg_gi = sum(gi_values) / len(gi_values) if gi_values else None
        user_bmr = settings.calculate_bmr(user_id)
        net_calories = cal_in - (user_bmr + cal_out_exercise)

        # 4. 近7天空腹血糖趋势（排除血压）
        seven_days_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("""SELECT value, timestamp FROM records
            WHERE user_id = ? AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%'))
            AND value > 0 AND is_predicted = 0 AND systolic_pressure IS NULL
            AND timestamp > ?
            ORDER BY timestamp DESC""", (user_id, seven_days_ago))
        recent_fpg = c.fetchall()

        # 5. 历史预测反馈
        fourteen_days_ago = (datetime.datetime.now() - datetime.timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("""
            SELECT p.value AS predicted, r.value AS actual, p.prediction_error, r.timestamp AS actual_time
            FROM records p JOIN records r ON p.verified_by_real_id = r.id
            WHERE p.user_id = ? AND p.is_predicted = 1 AND p.prediction_error IS NOT NULL
            AND p.type LIKE '%空腹%' AND p.type NOT LIKE '%血压%'
            AND r.timestamp > ?
            ORDER BY r.timestamp DESC""", (user_id, fourteen_days_ago))
        prediction_history = c.fetchall()

        # 6. 当前用药
        c.execute("""SELECT medication_name, dosage, dose_quantity, dose_unit, times_per_day, timing_notes
            FROM medication_plans WHERE user_id = ? AND is_active = 1""", (user_id,))
        medications = c.fetchall()

        # === 构建详细 prompt ===
        # 昨日血糖
        if yesterday_glucose:
            glucose_values = [row[0] for row in yesterday_glucose]
            evening_glucose = glucose_values[-1]
            glucose_summary = f"""前一天血糖数据（{yesterday_str}）：
- 测量次数: {len(yesterday_glucose)}
- 最高值: {max(glucose_values)} mmol/L，最低值: {min(glucose_values)} mmol/L
- 波动幅度: {max(glucose_values) - min(glucose_values):.1f} mmol/L
- 晚间最后一次: {evening_glucose} mmol/L ({yesterday_glucose[-1][1]})
- 详细: {', '.join([f'{r[0]} mmol/L ({r[1]})' for r in yesterday_glucose])}"""
        else:
            glucose_summary = "前一天无血糖记录"

        # 近期 FPG 趋势
        if recent_fpg:
            fpg_values = [row[0] for row in recent_fpg]
            avg_fpg = sum(fpg_values) / len(fpg_values)
            recent_trend = "上升" if fpg_values[0] > fpg_values[-1] else "下降" if fpg_values[0] < fpg_values[-1] else "稳定"
            fpg_trend_summary = f"""近7天空腹血糖趋势：
- 平均值: {avg_fpg:.1f} mmol/L，最近一次: {fpg_values[0]:.1f} mmol/L
- 趋势: {recent_trend}
- 历史: {', '.join([f'{v:.1f}' for v in fpg_values])}"""
        else:
            fpg_trend_summary = "近期无空腹血糖记录"

        # 能量平衡
        energy_summary = f"""前一天能量平衡（{yesterday_str}）：
- 饮食摄入: {cal_in} kcal{f' (含默认补足 {default_cal} kcal)' if default_cal > 0 else ''}
- 运动消耗: {cal_out_exercise} kcal，基础代谢: {user_bmr} kcal
- 净能量: {net_calories:+d} kcal ({'能量盈余' if net_calories > 0 else '能量缺口'})"""

        # 营养
        if total_carbs > 0 or gi_values:
            avg_gi_str = f'{avg_gi:.0f}' if avg_gi else '未知'
            nutrition_summary = f"前一天营养：总碳水 {total_carbs:.0f}g，平均GI {avg_gi_str}"
        else:
            nutrition_summary = ""

        # 预测反馈
        prediction_feedback = ""
        if prediction_history:
            errors = [r[2] for r in prediction_history]
            avg_error = sum(errors) / len(errors)
            avg_abs_error = sum(abs(e) for e in errors) / len(errors)
            bias_direction = "偏高" if avg_error > 0.2 else ("偏低" if avg_error < -0.2 else "无明显偏差")
            pairs = '\n'.join([f"  - {r[3].split(' ')[0]}: 预测 {r[0]:.1f} → 实际 {r[1]:.1f}（误差 {r[2]:+.1f}）" for r in prediction_history[:7]])
            prediction_feedback = f"""历史预测反馈（近14天，{len(prediction_history)}次）：
- 平均绝对误差: {avg_abs_error:.2f} mmol/L，系统性偏差: {bias_direction}（{avg_error:+.2f}）
{pairs}
⚠️ 请根据上述偏差校准预测。"""

        # 用药
        if medications:
            med_lines = []
            for med in medications:
                line = f"- {med[0]}"
                if med[1]:
                    line += f" {med[1]}"
                line += f"，{med[4]}次/天"
                if med[5]:
                    line += f"，{med[5]}"
                med_lines.append(line)
            med_summary = "当前用药：\n" + '\n'.join(med_lines)
        else:
            med_summary = "当前无用药"

        user_profile = settings.get_ai_system_prompt(user_id)

        prompt = f"""你是一个专业的糖尿病健康管理顾问。基于用户前一天的综合数据，预测今天早晨的空腹血糖值。

{user_profile}

{glucose_summary}

{fpg_trend_summary}

{energy_summary}

{nutrition_summary}

{prediction_feedback}

{med_summary}

预测任务：预测今天（{today_str}）早晨 07:15 的空腹血糖值（合理范围 3.5-10.0 mmol/L）。

请返回JSON（不要包含 markdown 格式）：
{{"predicted_value": float, "reasoning": "string (1-2句话)"}}"""

        raw_text = call_ai(prompt)
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if not match:
            return

        result = json.loads(match.group(0))
        pred_v = result.get('predicted_value')
        if not settings.is_valid_prediction(pred_v, 'fasting'):
            return

        c.execute("DELETE FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%')) AND is_predicted = 1", (user_id, today_str))
        c.execute("INSERT INTO records (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted) VALUES (?, ?, 'mmol/L', '空腹', ?, ?, 0, '', 1)",
                  (user_id, pred_v, f"AI预测: {result.get('reasoning')}", f"{today_str} 07:15:00"))
        db.commit()
        print(f"✓ FPG 预测: {pred_v} mmol/L ({today_str})")

    except Exception as e:
        print(f"ERROR in predict_morning_fpg: {e}")
        traceback.print_exc()


def predict_post_exercise_glucose(db, user_id=1, target_date=None, force_update=False):
    if not AI_AVAILABLE:
        return None
    if target_date is None:
        target_date = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        c = db.cursor()
        c.execute("SELECT id, is_predicted FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND type = '运动后' AND value > 0", (user_id, target_date))
        existing = c.fetchone()
        if existing and (not existing[1] or not force_update):
            return None

        c.execute("SELECT value FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%')) AND is_predicted = 0 AND value > 0 AND systolic_pressure IS NULL LIMIT 1", (user_id, target_date))
        fpg = c.fetchone()
        if not fpg:
            return None

        c.execute("SELECT distance, duration, heart_rate, calories, timestamp FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND (type IN ('跑步', '运动') OR distance > 0) ORDER BY timestamp DESC LIMIT 1", (user_id, target_date))
        ex = c.fetchone()
        if not ex:
            return None

        prompt = f"""预测运动后血糖。日期: {target_date}。空腹血糖: {fpg[0]} mmol/L。
运动数据: 距离 {ex[0]}km, 时长 {ex[1]}, 心率 {ex[2]}bpm, 消耗 {ex[3]}kcal。
返回JSON：{{"predicted_value": float, "reasoning": "string"}}"""

        raw_text = call_ai(prompt)
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if match:
            result = json.loads(match.group(0))
            pred_v = result.get('predicted_value')
            if settings.is_valid_prediction(pred_v, 'post_exercise'):
                if existing:
                    c.execute("UPDATE records SET value = ?, notes = ? WHERE id = ?", (pred_v, f"AI预测: {result.get('reasoning')}", existing[0]))
                else:
                    c.execute("INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) VALUES (?, ?, 'mmol/L', '运动后', ?, ?, 1)", (user_id, pred_v, f"AI预测: {result.get('reasoning')}", f"{target_date} 08:45:00"))
                db.commit()
                return pred_v
    except Exception as e:
        traceback.print_exc()
        if '429' in str(e):
            raise e
    return None


def backfill_post_exercise_predictions(db, user_id=1, days=30):
    """批量回溯补全过去 N 天的运动后血糖预测"""
    results = {'success': 0, 'skipped': 0, 'error': 0}
    try:
        now = datetime.datetime.now()
        for i in range(days):
            target_date = (now - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
            try:
                pred = predict_post_exercise_glucose(db, user_id, target_date=target_date)
                if pred:
                    results['success'] += 1
                else:
                    results['skipped'] += 1
            except Exception as e:
                print(f"Backfill error for {target_date}: {e}")
                results['error'] += 1
        return results
    except Exception:
        traceback.print_exc()
        return results


def predict_remaining_glucose_slots(db, user_id=1, target_date=None, force_update=False):
    if not AI_AVAILABLE:
        return []
    if target_date is None:
        target_date = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        c = db.cursor()
        # 查找今日已有实测数据
        c.execute("""SELECT value, type, timestamp FROM records
            WHERE user_id = ? AND DATE(timestamp) = ? AND value > 0
            AND is_predicted = 0 AND systolic_pressure IS NULL
            ORDER BY timestamp ASC""", (user_id, target_date))
        measured = c.fetchall()
        if not measured:
            return 'no_measured'

        # 定义所有时间槽
        all_slots = [
            {'type': '早餐后2小时', 'time': '11:00:00'},
            {'type': '午餐后2小时', 'time': '14:30:00'},
            {'type': '晚饭前', 'time': '17:30:00'},
            {'type': '晚餐后2小时', 'time': '20:00:00'},
            {'type': '睡前', 'time': '22:00:00'}
        ]

        # 找出缺失的槽位（只看实测记录，已有预测的可以覆盖）
        c.execute("""SELECT type FROM records
            WHERE user_id = ? AND DATE(timestamp) = ? AND value > 0
            AND is_predicted = 0 AND systolic_pressure IS NULL""",
            (user_id, target_date))
        measured_types = set(row[0] for row in c.fetchall())

        missing_slots = []
        for slot in all_slots:
            if not force_update and slot['type'] in measured_types:
                continue
            missing_slots.append(slot)

        if not missing_slots:
            return 'all_measured'

        measured_str = ', '.join([f"{r[0]} mmol/L ({r[1]})" for r in measured])
        missing_str = ', '.join([s['type'] for s in missing_slots])

        prompt = f"""基于今日已测数据预测剩余时间点的血糖值。
日期: {target_date}
已测数据: {measured_str}
需预测: {missing_str}

返回JSON数组（不要 markdown）：
[{{"type": "时间点类型", "value": float, "reasoning": "string"}}]"""

        raw_text = call_ai(prompt)
        match = re.search(r'\[[\s\S]*\]', raw_text)
        if not match:
            return []

        predictions = json.loads(match.group(0))
        results = []
        for pred in predictions:
            pred_type = pred.get('type', '')
            pred_value = pred.get('value')
            if not pred_value or pred_value < 2.0 or pred_value > 30.0:
                continue

            # 找对应时间
            slot_time = None
            for slot in missing_slots:
                if slot['type'] == pred_type:
                    slot_time = slot['time']
                    break
            if not slot_time:
                continue

            if not force_update:
                c.execute("SELECT id FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND type = ? AND is_predicted = 1",
                          (user_id, target_date, pred_type))
                if c.fetchone():
                    continue

            c.execute("DELETE FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND type = ? AND is_predicted = 1",
                      (user_id, target_date, pred_type))
            c.execute("INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) VALUES (?, ?, 'mmol/L', ?, ?, ?, 1)",
                      (user_id, pred_value, pred_type, f"AI预测: {pred.get('reasoning', '')}", f"{target_date} {slot_time}"))
            results.append(pred)

        if results:
            db.commit()
        return results
    except Exception:
        traceback.print_exc()
    return []


def check_daily_data_complete(db, user_id=1, target_date=None):
    if target_date is None:
        target_date = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND value > 0 AND type NOT IN ('跑步', '运动') AND type NOT LIKE '%血压%' AND systolic_pressure IS NULL", (user_id, target_date))
        has_g = c.fetchone()[0] > 0
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND systolic_pressure > 0", (user_id, target_date))
        has_bp = c.fetchone()[0] > 0
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND (type IN ('跑步', '运动') OR distance > 0)", (user_id, target_date))
        has_ex = c.fetchone()[0] > 0
        return {'complete': has_g and has_bp and has_ex, 'has_glucose': has_g, 'has_blood_pressure': has_bp, 'has_exercise': has_ex}
    except Exception:
        return {'complete': False, 'has_glucose': False, 'has_blood_pressure': False, 'has_exercise': False}
