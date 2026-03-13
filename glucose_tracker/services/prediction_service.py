import sqlite3
import datetime
import re
import json
import traceback
from ai_client import call_ai, AI_AVAILABLE
import settings
from core.config import DB_NAME

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
    """
    if not AI_AVAILABLE: return None
    try:
        now = datetime.datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        c = db.cursor()
        c.execute("""
            SELECT id FROM records
            WHERE user_id = ? AND DATE(timestamp) = ?
            AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%'))
            AND is_predicted = 1 AND value > 0
        """, (user_id, today_str,))
        if c.fetchone(): return

        yesterday = now - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y-%m-%d')

        c.execute("""SELECT value, type, timestamp FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND value > 0 AND is_predicted = 0 ORDER BY timestamp ASC""", (user_id, yesterday_str,))
        yesterday_glucose = c.fetchall()

        c.execute("""SELECT type, calories, timestamp, carbs_grams, gi_value FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND calories > 0""", (user_id, yesterday_str,))
        yesterday_calories = c.fetchall()

        cal_in = sum(row[1] for row in yesterday_calories if row[0] not in ['跑步', '运动'])
        cal_out_exercise = sum(row[1] for row in yesterday_calories if row[0] in ['跑步', '运动'])

        user_config = settings.load_config()
        default_meals = user_config.get('default_meals', {})

        has_breakfast = has_lunch = has_dinner = False
        total_carbs: float = 0.0
        gi_values = []

        for row in yesterday_calories:
            record_type = row[0] or ''
            timestamp = row[2] or ''
            if row[3] is not None:
                try:
                    total_carbs = total_carbs + float(row[3])
                except (ValueError, TypeError):
                    pass
            if row[4] is not None:
                gi_values.append(row[4])
            hour = int(timestamp.split(' ')[1].split(':')[0]) if timestamp and ' ' in timestamp else 0
            if '早餐' in record_type or (6 <= hour < 10): has_breakfast = True
            elif '午餐' in record_type or (11 <= hour < 14): has_lunch = True
            elif '晚餐' in record_type or (17 <= hour < 21): has_dinner = True

        default_cal = 0
        for meal, config in default_meals.items():
            if not locals()[f'has_{meal}'] and config.get('enabled', True):
                default_cal += config.get('calories', 300 if meal=='breakfast' else 500)
                try:
                    total_carbs = total_carbs + float(config.get('carbs_grams', 45 if meal=='breakfast' else 75))
                except (ValueError, TypeError):
                    pass
                if config.get('gi_value'): gi_values.append(config.get('gi_value'))
        cal_in += default_cal

        user_bmr = settings.calculate_bmr()
        net_calories = cal_in - (user_bmr + cal_out_exercise)

        c.execute("""SELECT value, timestamp FROM records WHERE user_id = ? AND type LIKE '%空腹%' AND value > 0 AND is_predicted = 0 AND timestamp > datetime('now', '-7 days') ORDER BY timestamp DESC""", (user_id,))
        fpg_values = [row[0] for row in c.fetchall()]
        
        c.execute("""SELECT p.value, r.value, p.prediction_error, r.timestamp FROM records p JOIN records r ON p.verified_by_real_id = r.id WHERE p.user_id = ? AND p.is_predicted = 1 AND p.type LIKE '%空腹%' AND r.timestamp > datetime('now', '-14 days') ORDER BY r.timestamp DESC""", (user_id,))
        prediction_history = c.fetchall()

        c.execute("""SELECT medication_name, dosage, times_per_day, timing_notes FROM medication_plans WHERE user_id = ? AND is_active = 1""", (user_id,))
        medications = c.fetchall()

        prompt = f"预测空腹血糖. 今日: {today_str}. 昨卡: {cal_in}/{cal_out_exercise}. 昨糖: {yesterday_glucose}. 历糖: {fpg_values}. 用药: {medications}."
        raw_text = call_ai(prompt, system_prompt="你是一位糖尿病顾问。回复特定JSON。")
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if match:
            result = json.loads(match.group(0))
            pred_v = result.get('predicted_value')
            if settings.is_valid_prediction(pred_v, 'fasting'):
                c.execute("DELETE FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND type LIKE '%空腹%' AND is_predicted = 1", (user_id, today_str))
                c.execute("INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) VALUES (?, ?, 'mmol/L', '空腹', ?, ?, 1)", (user_id, pred_v, f"AI预测: {result.get('reasoning')}", f"{today_str} 07:15:00"))
                db.commit()
    except Exception as e:
        traceback.print_exc()

def predict_post_exercise_glucose(db, user_id=1, target_date=None, force_update=False):
    if not AI_AVAILABLE: return None
    if target_date is None: target_date = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        c = db.cursor()
        c.execute("SELECT id, is_predicted FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND type = '运动后' AND value > 0", (user_id, target_date))
        existing = c.fetchone()
        if existing and (not existing[1] or not force_update): return None

        c.execute("SELECT value FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND type LIKE '%空腹%' AND is_predicted = 0 AND value > 0 LIMIT 1", (user_id, target_date))
        fpg = c.fetchone()
        if not fpg: return None

        c.execute("SELECT distance, duration, heart_rate, calories, timestamp FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND (type IN ('跑步', '运动') OR distance > 0) ORDER BY timestamp DESC LIMIT 1", (user_id, target_date))
        ex = c.fetchone()
        if not ex: return None

        prompt = f"预测运动后血糖. 日期: {target_date}. 常规空腹: {fpg[0]}. 运动卡: {ex[3]}."
        raw_text = call_ai(prompt)
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if match:
            result = json.loads(match.group(0))
            pred_v = result.get('predicted_value')
            if settings.is_valid_prediction(pred_v, 'post_exercise'):
                if existing: c.execute("UPDATE records SET value = ?, notes = ? WHERE id = ?", (pred_v, f"AI预测: {result.get('reasoning')}", existing[0]))
                else: c.execute("INSERT INTO records (user_id, value, unit, type, notes, timestamp, is_predicted) VALUES (?, ?, 'mmol/L', '运动后', ?, ?, 1)", (user_id, pred_v, f"AI预测: {result.get('reasoning')}", f"{target_date} 08:45:00"))
                db.commit()
                return pred_v
    except Exception as e:
        traceback.print_exc()
        if '429' in str(e): raise e
    return None

def backfill_post_exercise_predictions(db, user_id=1, days=30):
    """
    批量回溯补全过去 N 天的运动后血糖预测
    """
    results = {'success': 0, 'skipped': 0, 'error': 0}
    try:
        now = datetime.datetime.now()
        for i in range(days):
            target_date = (now - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
            try:
                # 调用单日预测函数
                pred = predict_post_exercise_glucose(db, user_id, target_date=target_date)
                if pred:
                    results['success'] += 1
                else:
                    results['skipped'] += 1
            except Exception as e:
                print(f"Backfill error for {target_date}: {e}")
                results['error'] += 1
        return results
    except Exception as e:
        traceback.print_exc()
        return results

def predict_remaining_glucose_slots(db, user_id=1, target_date=None, force_update=False):
    if not AI_AVAILABLE: return []
    if target_date is None: target_date = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        c = db.cursor()
        # ... logic ...
        return [] # Placeholder
    except Exception as e:
        traceback.print_exc()
    return []

def check_daily_data_complete(db, user_id=1, target_date=None):
    if target_date is None: target_date = datetime.datetime.now().strftime('%Y-%m-%d')
    try:
        c = db.cursor()
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND value > 0 AND type NOT IN ('跑步', '运动', '血压')", (user_id, target_date))
        has_g = c.fetchone()[0] > 0
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND systolic_pressure > 0", (user_id, target_date))
        has_bp = c.fetchone()[0] > 0
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ? AND DATE(timestamp) = ? AND (type IN ('跑步', '运动') OR distance > 0)", (user_id, target_date))
        has_ex = c.fetchone()[0] > 0
        return {'complete': has_g and has_bp and has_ex, 'has_glucose': has_g, 'has_blood_pressure': has_bp, 'has_exercise': has_ex}
    except:
        return {'complete': False, 'has_glucose': False, 'has_blood_pressure': False, 'has_exercise': False}
