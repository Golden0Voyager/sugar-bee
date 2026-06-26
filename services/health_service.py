import re
import traceback

from ai_client import AI_AVAILABLE, call_ai
from utils.sql_dialect import interval_sql
from utils.timezone import now as app_now


def generate_health_analysis(db, user_id=1, is_auto=False, days=7):
    """
    生成综合健康分析
    基于指定天数的血糖、血压、运动、饮食、用药数据，
    利用大模型生成个性化健康分析和建议
    """
    if not AI_AVAILABLE:
        return {"success": False, "error": "未配置 AI API Key", "error_type": "ai_unavailable"}

    try:
        now = app_now()
        today_str = now.strftime('%Y-%m-%d')

        if is_auto:
            c = db.cursor()
            c.execute("""
                SELECT id FROM health_analyses
                WHERE user_id = ?
                AND analysis_date = ?
                AND is_auto_generated = 1
            """, (user_id, today_str,))
            if c.fetchone():
                print("Today's auto analysis already exists, skipping...")
                return {"skipped": True, "message": "今日已生成分析"}

        c = db.cursor()

        # 1. 血糖数据
        c.execute(f"""
            SELECT r.value, r.type, r.timestamp, p.value AS predicted_value, p.prediction_error
            FROM records r
            LEFT JOIN records p ON p.verified_by_real_id = r.id AND p.is_predicted = 1
            WHERE r.user_id = ? AND r.value > 0 AND r.is_predicted = 0
            AND r.timestamp > {interval_sql(days)}
            AND r.type NOT IN ('跑步', '运动')
            AND r.type NOT LIKE '%血压%'
            AND r.systolic_pressure IS NULL
            ORDER BY r.timestamp DESC
        """, (user_id,))
        glucose_records = c.fetchall()

        # 2. 血压数据
        c.execute(f"""
            SELECT systolic_pressure, diastolic_pressure, pulse_rate, timestamp, spo2
            FROM records
            WHERE user_id = ? AND systolic_pressure > 0
            AND timestamp > {interval_sql(days)}
            ORDER BY timestamp DESC
        """, (user_id,))
        bp_records = c.fetchall()

        # 3. 运动数据
        c.execute(f"""
            SELECT distance, duration, heart_rate, max_heart_rate, calories, pace, cadence, steps, vo2max, timestamp
            FROM records
            WHERE user_id = ? AND (type IN ('跑步', '运动') OR distance > 0)
            AND timestamp > {interval_sql(days)}
            ORDER BY timestamp DESC
        """, (user_id,))
        exercise_records = c.fetchall()

        # 4. 饮食数据
        c.execute(f"""
            SELECT calories, carbs_grams, gi_value, diet_analysis, notes, type, timestamp
            FROM records
            WHERE user_id = ? AND calories > 0 AND type NOT IN ('跑步', '运动', '走路', '骑行', '游泳', '健身')
            AND timestamp > {interval_sql(days)}
            ORDER BY timestamp DESC
        """, (user_id,))
        diet_records = c.fetchall()  # noqa: F841

        # 5. 用药方案
        c.execute("""
            SELECT medication_name, dosage, dose_quantity, dose_unit, times_per_day, timing_notes,
                   category, start_date, med_type
            FROM medication_plans
            WHERE user_id = ? AND is_active = 1
        """, (user_id,))
        medications = c.fetchall()

        # 5b. 临时用药
        c.execute(f"""
            SELECT medication_name, notes, timestamp
            FROM records
            WHERE user_id = ? AND medication_name IS NOT NULL AND medication_name != ''
            AND timestamp > {interval_sql(days)}
            ORDER BY timestamp DESC
        """, (user_id,))
        temp_med_records = c.fetchall()  # noqa: F841

        # 5c. 服药依从性
        c.execute(f"""
            SELECT ml.plan_id, mp.medication_name, COUNT(*) as taken_count
            FROM medication_logs ml
            JOIN medication_plans mp ON ml.plan_id = mp.id
            WHERE ml.user_id = ? AND ml.log_date > {interval_sql(days)} AND ml.taken = 1
            GROUP BY ml.plan_id, mp.medication_name
        """, (user_id,))
        adherence_records = c.fetchall()  # noqa: F841
        # 6. 体重数据
        c.execute(f"""
            SELECT weight, bmi, timestamp FROM records
            WHERE user_id = ? AND weight > 0 AND timestamp > {interval_sql(days)}
            ORDER BY timestamp DESC
        """, (user_id,))
        weight_records = c.fetchall()  # noqa: F841
        glucose_summary = ""
        if glucose_records:
            glucose_values = [r[0] for r in glucose_records if 1.0 <= r[0] <= 30.0]
            if glucose_values:
                glucose_summary = f"平均血糖: {sum(glucose_values)/len(glucose_values):.1f} mmol/L, 最高: {max(glucose_values):.1f}, 最低: {min(glucose_values):.1f}"

        bp_summary = ""
        if bp_records:
            sys_vals = [r[0] for r in bp_records if r[0]]
            dia_vals = [r[1] for r in bp_records if r[1]]
            spo2_vals = [r[4] for r in bp_records if r[4]]
            parts = []
            if sys_vals and dia_vals:
                parts.append(f"平均血压: {sum(sys_vals)/len(sys_vals):.0f}/{sum(dia_vals)/len(dia_vals):.0f} mmHg")
            if spo2_vals:
                parts.append(f"平均血氧: {sum(spo2_vals)/len(spo2_vals):.0f}%")
            bp_summary = ", ".join(parts)

        exercise_summary = f"运动记录: {len(exercise_records)}次"
        if exercise_records:
            total_dist = sum((r[0] or 0) for r in exercise_records)
            total_dur_min = 0
            for r in exercise_records:
                dur = r[1]
                if dur and isinstance(dur, str):
                    m = re.search(r'(\d+)', dur)
                    if m:
                        total_dur_min += int(m.group(1))
            hr_vals = [r[2] for r in exercise_records if r[2]]
            max_hr_vals = [r[3] for r in exercise_records if r[3]]
            total_cal = sum((r[4] or 0) for r in exercise_records)
            total_steps = sum((r[7] or 0) for r in exercise_records)
            vo2_vals = [r[8] for r in exercise_records if r[8]]
            parts = [f"共{len(exercise_records)}次"]
            if total_dur_min:
                parts.append(f"总时长{total_dur_min}分钟")
            if total_dist:
                parts.append(f"总距离{total_dist:.1f}km")
            if total_steps:
                parts.append(f"总步数{total_steps}")
            if hr_vals:
                parts.append(f"平均心率{sum(hr_vals)//len(hr_vals)}")
            if max_hr_vals:
                parts.append(f"最高心率{max(max_hr_vals)}")
            if total_cal:
                parts.append(f"总消耗{total_cal}kcal")
            if vo2_vals:
                parts.append(f"VO2max范围{min(vo2_vals):.1f}-{max(vo2_vals):.1f}")
            exercise_summary = "运动: " + ", ".join(parts)

        diet_summary = ""
        if diet_records:
            cal_vals = [r[0] for r in diet_records if r[0]]
            carb_vals = [r[1] for r in diet_records if r[1]]
            gi_vals = [r[2] for r in diet_records if r[2]]
            parts = []
            if cal_vals:
                parts.append(f"日均摄入{sum(cal_vals)/len(cal_vals):.0f}kcal")
            if carb_vals:
                parts.append(f"日均碳水{sum(carb_vals)/len(carb_vals):.1f}g")
            if gi_vals:
                parts.append(f"GI范围{min(gi_vals):.0f}-{max(gi_vals):.0f}")
            diet_summary = "饮食: " + ", ".join(parts)

        weight_summary = ""
        if weight_records:
            latest_w = weight_records[0][0]
            latest_bmi = weight_records[0][1]
            parts = []
            if latest_w:
                parts.append(f"最新体重{latest_w:.1f}kg")
            if latest_bmi:
                parts.append(f"BMI {latest_bmi:.1f}")
            weight_summary = "体重: " + ", ".join(parts)

        med_summary = f"当前用药: {len(medications)}项"
        if medications:
            med_lines = []
            for m in medications:
                name, dosage, dose_qty, dose_unit, times, timing, cat, start, mtype = m
                line = f"{name}"
                if dosage:
                    line += f" {dosage}"
                if dose_qty and dose_unit:
                    line += f" ({dose_qty}{dose_unit})"
                if times:
                    line += f" 每日{times}次"
                if timing:
                    line += f" {timing}"
                med_lines.append(line)
            med_summary = "用药: " + "; ".join(med_lines)

        adherence_summary = ""
        if adherence_records and medications:
            expected = sum((m[4] or 1) * days for m in medications)
            total_taken = sum(r[2] for r in adherence_records)
            if expected:
                rate = min(100, int(total_taken / expected * 100))
                adherence_summary = f"服药依从性: {rate}%"

        # AI 提示词构建 (这里使用简化的汇总以防 Token 过长，但保留结构)
        # TODO: 睡眠数据目前未在数据库中存储，未来可新增 sleep_records 表或 records 睡眠列后接入
        prompt = f"""
你是一位资深的糖尿病管理专家和全科医生。请根据以下用户近 {days} 天的健康数据摘要，提供一份综合健康分析报告。

## 1. 血糖数据
{glucose_summary}
## 2. 血压
{bp_summary}
## 3. 运动
{exercise_summary}
## 4. 饮食
{diet_summary}
## 5. 体重
{weight_summary}
## 6. 用药
{med_summary}
{adherence_summary}

任务：
1. **健康评估**：评价控制现状。
2. **风险预警**：指出潜在风险。
3. **改进建议**：给出具体生活方式指导。
4. **综合得分**：给出一个 0-100 的得分。

要求：返回 Markdown 格式，文末必须包含 '综合健康得分: [分数]'。
"""

        ai_response = call_ai(prompt, task_type='report')

        # 去除 AI 返回的 ```markdown ... ``` 包裹
        ai_response = re.sub(r'^```(?:markdown|md)?\s*\n?', '', ai_response, flags=re.IGNORECASE)
        ai_response = re.sub(r'\n?```\s*$', '', ai_response)

        # 解析得分
        score_match = re.search(r'综合健康得分[：:]\s*(\d+)', ai_response)
        score = int(score_match.group(1)) if score_match else 80

        # 保存记录
        c.execute("""
            INSERT INTO health_analyses
            (analysis_date, health_score, glucose_summary, blood_pressure_summary, exercise_summary,
             medication_summary, recommendations, full_analysis, is_auto_generated, user_id, days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (today_str, score, glucose_summary, bp_summary, exercise_summary, med_summary, "", ai_response, 1 if is_auto else 0, user_id, days))
        db.commit()

        return {
            "success": True,
            "analysis_id": c.lastrowid,
            "result": ai_response,
            "score": score
        }
    except Exception as e:
        traceback.print_exc()
        err_text = str(e).lower()
        error_type = 'analysis_failed'
        details = {}
        if '429' in err_text or 'quota' in err_text or 'rate limit' in err_text:
            error_type = 'quota_exceeded'
            # 尝试从错误信息中提取等待秒数
            wait_match = re.search(r'(\d+)\s*秒', str(e))
            if wait_match:
                details['retry_after'] = int(wait_match.group(1))
            else:
                details['retry_after'] = 60
        return {"success": False, "error": str(e), "error_type": error_type, "details": details}

def auto_trigger_health_analysis(db, user_id=1):
    """
    自动触发健康分析。
    """
    return generate_health_analysis(db, user_id, is_auto=True, days=7)
