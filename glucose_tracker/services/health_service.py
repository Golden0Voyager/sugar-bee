import datetime
import traceback
import re
import json
from ai_client import call_ai, AI_AVAILABLE
import settings

def generate_health_analysis(db, user_id=1, is_auto=False, days=7):
    """
    生成综合健康分析
    基于指定天数的血糖、血压、运动、饮食、用药数据，
    利用大模型生成个性化健康分析和建议
    """
    if not AI_AVAILABLE:
        return {"error": "未配置 AI API Key"}

    try:
        now = datetime.datetime.now()
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
        c.execute("""
            SELECT r.value, r.type, r.timestamp, p.value AS predicted_value, p.prediction_error
            FROM records r
            LEFT JOIN records p ON p.verified_by_real_id = r.id AND p.is_predicted = 1
            WHERE r.user_id = ? AND r.value > 0 AND r.is_predicted = 0
            AND r.timestamp > datetime('now', ? || ' days')
            AND r.type NOT IN ('跑步', '运动', '血压')
            ORDER BY r.timestamp DESC
        """, (user_id, f'-{days}'))
        glucose_records = c.fetchall()

        # 2. 血压数据
        c.execute("""
            SELECT systolic_pressure, diastolic_pressure, pulse_rate, timestamp, spo2
            FROM records
            WHERE user_id = ? AND systolic_pressure > 0
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}'))
        bp_records = c.fetchall()

        # 3. 运动数据
        c.execute("""
            SELECT distance, duration, heart_rate, max_heart_rate, calories, pace, cadence, steps, vo2max, timestamp
            FROM records
            WHERE user_id = ? AND (type IN ('跑步', '运动') OR distance IS NOT NULL)
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}'))
        exercise_records = c.fetchall()

        # 4. 饮食数据
        c.execute("""
            SELECT calories, carbs_grams, gi_value, diet_analysis, notes, type, timestamp
            FROM records
            WHERE user_id = ? AND calories > 0 AND type NOT IN ('跑步', '运动')
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}'))
        diet_records = c.fetchall()

        # 5. 用药方案
        c.execute("""
            SELECT medication_name, dosage, dose_quantity, dose_unit, times_per_day, timing_notes,
                   category, start_date, med_type
            FROM medication_plans
            WHERE user_id = ? AND is_active = 1
        """, (user_id,))
        medications = c.fetchall()

        # 5b. 临时用药
        c.execute("""
            SELECT medication_name, notes, timestamp
            FROM records
            WHERE user_id = ? AND medication_name IS NOT NULL AND medication_name != ''
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}'))
        temp_med_records = c.fetchall()

        # 5c. 服药依从性
        c.execute("""
            SELECT ml.plan_id, mp.medication_name, COUNT(*) as taken_count
            FROM medication_logs ml
            JOIN medication_plans mp ON ml.plan_id = mp.id
            WHERE ml.user_id = ? AND ml.log_date > date('now', ? || ' days') AND ml.taken = 1
            GROUP BY ml.plan_id
        """, (user_id, f'-{days}'))
        adherence_records = c.fetchall()

        # 6. 体重数据
        c.execute("""
            SELECT weight, bmi, timestamp FROM records
            WHERE user_id = ? AND weight > 0 AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}'))
        weight_records = c.fetchall()

        # --- 数据汇总 ---
        glucose_summary = ""
        if glucose_records:
            glucose_values = [r[0] for r in glucose_records]
            glucose_summary = f"平均血糖: {sum(glucose_values)/len(glucose_values):.1f} mmol/L, 最高: {max(glucose_values):.1f}, 最低: {min(glucose_values):.1f}"
        
        bp_summary = ""
        if bp_records:
            sys = [r[0] for r in bp_records]
            dia = [r[1] for r in bp_records]
            bp_summary = f"平均血压: {sum(sys)/len(sys):.0f}/{sum(dia)/len(dia):.0f} mmHg"

        exercise_summary = f"运动记录: {len(exercise_records)}次"
        med_summary = f"当前用药: {len(medications)}项"

        # AI 提示词构建 (这里使用简化的汇总以防 Token 过长，但保留结构)
        prompt = f"""
你是一位资深的糖尿病管理专家和全科医生。请根据以下用户近 {days} 天的健康数据摘要，提供一份综合健康分析报告。

## 1. 血糖数据
{glucose_summary}
## 2. 血压
{bp_summary}
## 3. 运动
{exercise_summary}
## 4. 用药
{med_summary}

任务：
1. **健康评估**：评价控制现状。
2. **风险预警**：指出潜在风险。
3. **改进建议**：给出具体生活方式指导。
4. **综合得分**：给出一个 0-100 的得分。

要求：返回 Markdown 格式，文末必须包含 '综合健康得分: [分数]'。
"""

        ai_response = call_ai(prompt, system_prompt="你是一位专业的健康管理专家。")
        
        # 解析得分
        score_match = re.search(r'综合健康得分:\s*(\d+)', ai_response)
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
        return {"success": False, "error": str(e)}

def auto_trigger_health_analysis(db, user_id=1):
    """
    自动触发健康分析。
    """
    return generate_health_analysis(db, user_id, is_auto=True, days=7)
