from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, g, session
import sqlite3
import pandas as pd
import datetime
import os
import io
import traceback
from werkzeug.utils import secure_filename
from parser import parse_glucose_input
import settings
from google import genai
import json
import re
from user_manager import UserManager

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

app.config['UPLOAD_FOLDER'] = AVATAR_FOLDER
# Secret key for session
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-for-glucose-tracker-2026')

# Ensure avatar folder exists
os.makedirs(AVATAR_FOLDER, exist_ok=True)

# Initialize user manager
user_manager = UserManager(DB_NAME)

# Configure Gemini API for FPG prediction
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_NAME)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS records
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      value REAL, 
                      unit TEXT, 
                      type TEXT, 
                      notes TEXT, 
                      timestamp DATETIME,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
        # Migration: Check and add new columns if they don't exist
        try:
            c.execute("ALTER TABLE records ADD COLUMN calories INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Column already exists

        try:
            c.execute("ALTER TABLE records ADD COLUMN diet_analysis TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass # Column already exists

        try:
            c.execute("ALTER TABLE records ADD COLUMN is_predicted BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass # Column already exists

        # Migration: Add exercise columns
        for col in ['distance REAL', 'duration TEXT', 'heart_rate INTEGER', 'pace TEXT', 'cadence INTEGER']:
            try:
                c.execute(f"ALTER TABLE records ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass

        # Migration: Add blood pressure columns
        for col in ['systolic_pressure INTEGER', 'diastolic_pressure INTEGER', 'pulse_rate INTEGER']:
            try:
                c.execute(f"ALTER TABLE records ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass

        # Migration: Add prediction verification fields
        try:
            c.execute("ALTER TABLE records ADD COLUMN verified_by_real_id INTEGER")
        except sqlite3.OperationalError:
            pass  # Column already exists

        try:
            c.execute("ALTER TABLE records ADD COLUMN prediction_error REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Create medication_plans table (药物方案)
        c.execute('''CREATE TABLE IF NOT EXISTS medication_plans
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      medication_name TEXT NOT NULL,
                      dosage TEXT,
                      times_per_day INTEGER DEFAULT 1,
                      timing_notes TEXT,
                      start_date DATE NOT NULL,
                      end_date DATE,
                      is_active BOOLEAN DEFAULT 1,
                      notes TEXT,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # Migration: Add frequency fields to medication_plans
        try:
            c.execute("ALTER TABLE medication_plans ADD COLUMN frequency TEXT DEFAULT 'daily'")
        except sqlite3.OperationalError:
            pass  # Column already exists

        try:
            c.execute("ALTER TABLE medication_plans ADD COLUMN frequency_detail TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: Add user_id to all user-specific tables
        for table in ['records', 'medication_plans', 'medication_logs', 'health_analyses']:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Create medication_logs table (服药记录/打卡)
        c.execute('''CREATE TABLE IF NOT EXISTS medication_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      plan_id INTEGER NOT NULL,
                      log_date DATE NOT NULL,
                      timestamp DATETIME NOT NULL,
                      taken BOOLEAN DEFAULT 1,
                      notes TEXT,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (plan_id) REFERENCES medication_plans(id))''')

        # Create health_analyses table (综合健康分析记录)
        c.execute('''CREATE TABLE IF NOT EXISTS health_analyses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      analysis_date DATE NOT NULL,
                      health_score INTEGER,
                      glucose_summary TEXT,
                      blood_pressure_summary TEXT,
                      exercise_summary TEXT,
                      medication_summary TEXT,
                      recommendations TEXT,
                      full_analysis TEXT,
                      is_auto_generated BOOLEAN DEFAULT 0,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        conn.commit()
    except Exception as e:
        print(f"DB Init Error: {e}")
    finally:
        if conn:
            conn.close()

init_db()


def link_prediction_to_real_record(db, real_record_id, user_id, record_date, record_type, real_value):
    """
    关联预测记录到真实记录，计算预测误差

    Args:
        db: 数据库连接
        real_record_id: 刚插入的真实记录 ID
        user_id: 用户 ID
        record_date: 记录日期 (YYYY-MM-DD)
        record_type: 记录类型 (如 '空腹', '餐后2小时' 等)
        real_value: 真实血糖值

    Returns:
        dict: 关联的预测信息 {'predicted_value': float, 'error': float} 或 None
    """
    # 血糖值合理范围验证 (mmol/L)
    VALID_GLUCOSE_RANGE = (2.0, 25.0)
    if not (VALID_GLUCOSE_RANGE[0] <= real_value <= VALID_GLUCOSE_RANGE[1]):
        print(f"WARNING: Invalid glucose value {real_value}, skipping prediction link")
        return None

    try:
        c = db.cursor()

        # 改进的类型匹配逻辑，避免误匹配
        # 精确匹配血糖类型，排除血压等其他类型
        if '空腹' in record_type and '血压' not in record_type:
            # 空腹血糖：匹配 '空腹', '早空腹' 等
            type_condition = "type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%')"
        elif '餐后1小时' in record_type:
            type_condition = "type LIKE '%餐后1小时%'"
        elif '餐后2小时' in record_type or '餐后' in record_type:
            type_condition = "type LIKE '%餐后%' AND type NOT LIKE '%1小时%'"
        elif '餐前' in record_type:
            type_condition = "type LIKE '%餐前%'"
        elif '睡前' in record_type:
            type_condition = "type LIKE '%睡前%'"
        else:
            # 通用匹配，但排除血压记录
            type_condition = f"type LIKE '%{record_type}%' AND type NOT LIKE '%血压%'"

        # 查找同一天同一类型的未验证预测记录
        # 同时排除血压记录（value > 0 且 systolic_pressure IS NULL）
        c.execute(f"""
            SELECT id, value FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND ({type_condition})
            AND is_predicted = 1
            AND verified_by_real_id IS NULL
            AND value > 0
            AND systolic_pressure IS NULL
            ORDER BY timestamp ASC
            LIMIT 1
        """, (user_id, record_date))

        prediction = c.fetchone()
        if prediction:
            pred_id, pred_value = prediction
            error = real_value - pred_value

            # 更新预测记录
            c.execute("""
                UPDATE records
                SET verified_by_real_id = ?, prediction_error = ?
                WHERE id = ?
            """, (real_record_id, error, pred_id))

            print(f"✓ Linked prediction {pred_id} to real record {real_record_id}: predicted={pred_value}, actual={real_value}, error={error:+.2f}")
            return {'predicted_value': pred_value, 'error': error}

        return None
    except Exception as e:
        print(f"ERROR in link_prediction_to_real_record: {e}")
        return None


def predict_morning_fpg(db, user_id=1):
    """
    自动预测当天早晨空腹血糖 (FPG)
    基于前一天综合数据：血糖波动、饮食热量、能量平衡、近期趋势、用药情况

    触发条件：
    - 当天第一次打开应用（今天还没有生成 FPG 预测记录）
    """
    if not api_key:
        return  # No API key, skip prediction

    try:
        now = datetime.datetime.now()
        today_str = now.strftime('%Y-%m-%d')

        # 1. 检查今天是否已经有 FPG 预测记录（防止重复预测）
        # 精确匹配空腹血糖，排除空腹血压
        c = db.cursor()
        c.execute("""
            SELECT id FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%'))
            AND is_predicted = 1
            AND value > 0
        """, (user_id, today_str,))
        existing = c.fetchone()
        if existing:
            return  # 预测已存在，跳过

        # 3. 获取前一天的日期
        yesterday = now - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y-%m-%d')

        # 4. 查询前一天的综合数据
        # 4.1 前一天血糖记录（真实数据，不含预测值）
        c.execute("""
            SELECT value, type, timestamp FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND value > 0
            AND is_predicted = 0
            ORDER BY timestamp ASC
        """, (user_id, yesterday_str,))
        yesterday_glucose = c.fetchall()

        # 4.2 前一天的饮食与运动卡路里
        c.execute("""
            SELECT type, calories FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND calories > 0
        """, (user_id, yesterday_str,))
        yesterday_calories = c.fetchall()

        cal_in = sum(row[1] for row in yesterday_calories if row[0] not in ['跑步', '运动'])
        cal_out_exercise = sum(row[1] for row in yesterday_calories if row[0] in ['跑步', '运动'])
        user_bmr = settings.calculate_bmr()
        net_calories = cal_in - (user_bmr + cal_out_exercise)

        # 4.3 近 7 天空腹血糖趋势（真实数据）
        c.execute("""
            SELECT value, timestamp FROM records
            WHERE user_id = ?
            AND type LIKE '%空腹%'
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', '-7 days')
            ORDER BY timestamp DESC
        """, (user_id,))
        recent_fpg = c.fetchall()

        # 4.4 当前用药情况
        c.execute("""
            SELECT medication_name, dosage, times_per_day, timing_notes
            FROM medication_plans
            WHERE user_id = ?
            AND is_active = 1
        """, (user_id,))
        medications = c.fetchall()

        # 5. 构建 AI 预测提示词
        # 前一天血糖波动情况
        if yesterday_glucose:
            glucose_values = [row[0] for row in yesterday_glucose]
            max_glucose = max(glucose_values)
            min_glucose = min(glucose_values)
            glucose_range = max_glucose - min_glucose
            evening_glucose = glucose_values[-1] if glucose_values else None

            glucose_summary = f"""
前一天血糖数据（{yesterday_str}）：
- 测量次数: {len(yesterday_glucose)}
- 最高值: {max_glucose} mmol/L
- 最低值: {min_glucose} mmol/L
- 波动幅度: {glucose_range:.1f} mmol/L
- 晚间最后一次: {evening_glucose} mmol/L ({yesterday_glucose[-1][1]})
- 详细记录: {', '.join([f"{r[0]} mmol/L ({r[1]})" for r in yesterday_glucose])}
            """
        else:
            glucose_summary = "前一天无血糖记录"

        # 近期 FPG 趋势
        if recent_fpg:
            fpg_values = [row[0] for row in recent_fpg]
            avg_fpg = sum(fpg_values) / len(fpg_values)
            recent_trend = "上升" if fpg_values[0] > fpg_values[-1] else "下降" if fpg_values[0] < fpg_values[-1] else "稳定"

            fpg_trend_summary = f"""
近 7 天空腹血糖趋势：
- 平均值: {avg_fpg:.1f} mmol/L
- 最近一次: {fpg_values[0]:.1f} mmol/L
- 趋势: {recent_trend}
- 历史记录: {', '.join([f"{v:.1f}" for v in fpg_values])}
            """
        else:
            fpg_trend_summary = "近期无空腹血糖记录"

        # 能量平衡
        energy_summary = f"""
前一天能量平衡（{yesterday_str}）：
- 饮食摄入: {cal_in} kcal
- 运动消耗: {cal_out_exercise} kcal
- 基础代谢: {user_bmr} kcal
- 净能量: {net_calories:+d} kcal ({'能量盈余，脂糖累积' if net_calories > 0 else '能量缺口，减糖减脂'})
        """

        # 用药情况
        if medications:
            med_summary = "当前用药方案：\n"
            for med in medications:
                med_summary += f"- {med[0]}"
                if med[1]:  # dosage
                    med_summary += f" {med[1]}"
                med_summary += f"，{med[2]}次/天"
                if med[3]:  # timing_notes
                    med_summary += f"，{med[3]}"
                med_summary += "\n"
        else:
            med_summary = "当前无用药"

        # 用户健康档案
        user_profile = settings.get_ai_system_prompt()

        # AI Prompt
        prompt = f"""
你是一个专业的糖尿病健康管理顾问。你的任务是基于用户前一天的综合数据，预测今天早晨的空腹血糖值。

{user_profile}

{glucose_summary}

{fpg_trend_summary}

{energy_summary}

{med_summary}

**预测任务**：
- 根据上述信息，预测今天（{today_str}）早晨 07:15 的空腹血糖值
- 预测值应基于：
  1. 前一天血糖波动情况（特别是晚间血糖）
  2. 前一天能量平衡（能量盈余通常导致晨起血糖偏高）
  3. 近期空腹血糖趋势
  4. 当前用药控制效果
- 预测值应在合理范围内（3.5-10.0 mmol/L）
- 给出简短的预测依据说明（1-2句话）

请返回以下JSON格式（不要包含 markdown 格式）：
{{
    "predicted_value": float,
    "reasoning": "string (预测依据，1-2句话)"
}}
        """

        # 6. 调用 Gemini API
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt
        )
        raw_text = response.text

        print(f"DEBUG FPG Prediction: Raw AI response: {raw_text}")

        # 7. 解析 AI 响应
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if not match:
            print("ERROR: AI response is not valid JSON")
            return

        result = json.loads(match.group(0))
        predicted_value = result.get('predicted_value')
        reasoning = result.get('reasoning', 'AI预测')

        if not predicted_value or not (3.5 <= predicted_value <= 10.0):
            print(f"ERROR: Invalid predicted value: {predicted_value}")
            return

        # 8. 存储预测记录
        prediction_timestamp = f"{today_str} 07:15:00"
        c.execute("""
            INSERT INTO records
            (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, predicted_value, 'mmol/L', '空腹', f'AI预测: {reasoning}',
              prediction_timestamp, 0, '', 1))
        db.commit()

        print(f"✓ FPG Prediction generated: {predicted_value} mmol/L for {today_str} 07:15")

    except Exception as e:
        print(f"ERROR in predict_morning_fpg: {e}")
        traceback.print_exc()


def predict_post_exercise_glucose(db, user_id=1, target_date=None):
    """
    预测运动后餐前血糖值

    基于当天数据：
    - 早空腹血糖（真实值）
    - 运动数据（距离、时长、心率、消耗卡路里）
    - 历史运动后餐前血糖与空腹血糖的差值

    触发条件：
    - 指定日期有运动记录
    - 指定日期有空腹血糖记录（真实值）
    - 指定日期还没有运动后餐前血糖记录（无论真实还是预测）

    Args:
        db: 数据库连接
        user_id: 用户ID
        target_date: 目标日期（YYYY-MM-DD格式），默认为今天
    """
    if not api_key:
        return None  # No API key, skip prediction

    try:
        if target_date is None:
            target_date = datetime.datetime.now().strftime('%Y-%m-%d')

        c = db.cursor()

        # 1. 检查指定日期是否已有运动后餐前血糖记录（真实或预测）
        c.execute("""
            SELECT id FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND type = '运动后餐前'
            AND value > 0
        """, (user_id, target_date,))
        existing = c.fetchone()
        if existing:
            return None  # 已有记录，跳过

        # 2. 获取指定日期的空腹血糖（真实值）
        c.execute("""
            SELECT value, timestamp FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND (type IN ('空腹', '早空腹') OR (type LIKE '%空腹%' AND type NOT LIKE '%血压%'))
            AND is_predicted = 0
            AND value > 0
            ORDER BY timestamp ASC
            LIMIT 1
        """, (user_id, target_date,))
        fpg_record = c.fetchone()
        if not fpg_record:
            return None  # 没有空腹血糖，无法预测

        fpg_value = fpg_record[0]

        # 3. 获取指定日期的运动记录
        c.execute("""
            SELECT distance, duration, heart_rate, calories, timestamp
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND (type IN ('跑步', '运动') OR distance IS NOT NULL)
            ORDER BY timestamp DESC
            LIMIT 1
        """, (user_id, target_date,))
        exercise_record = c.fetchone()
        if not exercise_record:
            return None  # 没有运动记录，无法预测

        distance = exercise_record[0] or 0
        duration = exercise_record[1] or ''
        heart_rate = exercise_record[2] or 0
        exercise_calories = exercise_record[3] or 0
        exercise_time = exercise_record[4]

        # 4. 获取历史运动后餐前血糖与空腹血糖的差值（目标日期之前30天）
        c.execute("""
            SELECT
                r1.value as post_exercise_value,
                r2.value as fpg_value,
                r1.timestamp
            FROM records r1
            JOIN records r2 ON DATE(r1.timestamp) = DATE(r2.timestamp)
                AND r2.type IN ('空腹', '早空腹')
                AND r2.is_predicted = 0
                AND r2.value > 0
            WHERE r1.user_id = ?
            AND r1.type = '运动后餐前'
            AND r1.is_predicted = 0
            AND r1.value > 0
            AND DATE(r1.timestamp) < ?
            AND r1.timestamp > datetime(?, '-30 days')
            ORDER BY r1.timestamp DESC
            LIMIT 10
        """, (user_id, target_date, target_date,))
        historical_data = c.fetchall()

        # 计算历史平均差值
        if historical_data:
            avg_diff = sum(row[0] - row[1] for row in historical_data) / len(historical_data)
        else:
            avg_diff = -0.5  # 默认运动后血糖比空腹低 0.5 mmol/L

        # 5. 构建 AI 预测提示词
        user_profile = settings.get_ai_system_prompt()

        prompt = f"""
你是一个专业的糖尿病健康管理顾问。请基于用户的运动数据和空腹血糖，预测运动后餐前血糖值。

{user_profile}

## {target_date} 数据

### 空腹血糖
- 数值: {fpg_value} mmol/L

### 运动数据
- 距离: {distance} km
- 时长: {duration}
- 平均心率: {heart_rate} bpm
- 消耗卡路里: {exercise_calories} kcal
- 运动时间: {exercise_time}

### 历史参考
- 历史运动后餐前与空腹血糖平均差值: {avg_diff:.2f} mmol/L
- 历史数据点数: {len(historical_data)}

## 预测任务

请预测 {target_date} 运动后餐前血糖值（约 08:45）。

**预测依据**：
1. 运动强度和时长对血糖的消耗影响
2. 空腹血糖基线
3. 历史差值规律
4. 运动后血糖通常比空腹低，但不会低于正常范围

**预测范围**：3.5-8.0 mmol/L（运动后餐前的合理范围）

请返回以下JSON格式（不要包含 markdown 格式）：
{{
    "predicted_value": float,
    "reasoning": "string (预测依据，1-2句话)"
}}
        """

        # 6. 调用 Gemini API
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt
        )
        raw_text = response.text

        print(f"DEBUG Post-Exercise Prediction ({target_date}): Raw AI response: {raw_text}")

        # 7. 解析 AI 响应
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if not match:
            print(f"ERROR: AI response is not valid JSON for {target_date}")
            return None

        result = json.loads(match.group(0))
        predicted_value = result.get('predicted_value')
        reasoning = result.get('reasoning', 'AI预测')

        if not predicted_value or not (3.5 <= predicted_value <= 8.0):
            print(f"ERROR: Invalid predicted value: {predicted_value} for {target_date}")
            return None

        # 8. 存储预测记录（运动结束后约30分钟）
        prediction_timestamp = f"{target_date} 08:45:00"
        c.execute("""
            INSERT INTO records
            (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, predicted_value, 'mmol/L', '运动后餐前', f'AI预测: {reasoning}',
              prediction_timestamp, 0, '', 1))
        db.commit()

        print(f"✓ Post-Exercise Glucose Prediction generated: {predicted_value} mmol/L for {target_date} 08:45")
        return predicted_value

    except Exception as e:
        error_str = str(e)
        print(f"ERROR in predict_post_exercise_glucose for {target_date}: {error_str}")
        traceback.print_exc()

        # 特殊处理 429 配额错误，抛出异常让调用者处理
        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
            raise Exception(error_str)

        return None


def backfill_post_exercise_predictions(db, user_id=1, days=30):
    """
    批量补全历史运动后餐前血糖预测

    查找指定天数内有空腹血糖+运动记录但没有运动后餐前血糖的日期，
    并生成预测值。

    Args:
        db: 数据库连接
        user_id: 用户ID
        days: 回溯天数，默认30天

    Returns:
        dict: {'success': int, 'skipped': int, 'dates': list}
    """
    if not api_key:
        return {'success': 0, 'skipped': 0, 'dates': [], 'error': 'No API key configured'}

    try:
        c = db.cursor()

        # 找出有空腹血糖+运动记录但没有运动后餐前血糖的日期
        c.execute("""
            SELECT DISTINCT DATE(r1.timestamp) as date
            FROM records r1
            WHERE r1.user_id = ?
            AND (r1.type IN ('空腹', '早空腹') OR (r1.type LIKE '%空腹%' AND r1.type NOT LIKE '%血压%'))
            AND r1.is_predicted = 0
            AND r1.value > 0
            AND r1.timestamp > datetime('now', ? || ' days')
            AND EXISTS (
                SELECT 1 FROM records r2
                WHERE r2.user_id = r1.user_id
                AND DATE(r2.timestamp) = DATE(r1.timestamp)
                AND (r2.type IN ('跑步', '运动') OR r2.distance IS NOT NULL)
            )
            AND NOT EXISTS (
                SELECT 1 FROM records r3
                WHERE r3.user_id = r1.user_id
                AND DATE(r3.timestamp) = DATE(r1.timestamp)
                AND r3.type = '运动后餐前'
                AND r3.value > 0
            )
            ORDER BY date DESC
        """, (user_id, f'-{days}',))

        dates_to_predict = [row[0] for row in c.fetchall()]

        print(f"Found {len(dates_to_predict)} dates needing post-exercise glucose prediction")

        success_count = 0
        skipped_count = 0
        predicted_dates = []

        for target_date in dates_to_predict:
            result = predict_post_exercise_glucose(db, user_id, target_date)
            if result:
                success_count += 1
                predicted_dates.append({'date': target_date, 'value': result})
            else:
                skipped_count += 1

            # 添加短暂延迟避免 API 限流
            import time
            time.sleep(0.5)

        return {
            'success': success_count,
            'skipped': skipped_count,
            'dates': predicted_dates
        }

    except Exception as e:
        print(f"ERROR in backfill_post_exercise_predictions: {e}")
        traceback.print_exc()
        return {'success': 0, 'skipped': 0, 'dates': [], 'error': str(e)}


def predict_remaining_glucose_slots(db, user_id=1, target_date=None):
    """
    基于当日已有数据，向后预测剩余时间点的血糖值

    预测逻辑：
    1. 获取今日最后一条实测血糖记录作为基准
    2. 根据时间推移、饮食、运动等因素预测后续时间点
    3. 预测值会被后续实测值覆盖

    Args:
        db: 数据库连接
        user_id: 用户ID
        target_date: 目标日期（默认今天）

    Returns:
        list: 生成的预测记录列表
    """
    if not api_key:
        return []

    try:
        if target_date is None:
            target_date = datetime.datetime.now().strftime('%Y-%m-%d')

        c = db.cursor()
        now = datetime.datetime.now()
        current_time = now.strftime('%H:%M')

        # 定义可预测的时间点
        predictable_slots = [
            {'key': 'post_breakfast', 'name': '早餐后2h', 'time': '11:00', 'type': '早餐后2小时'},
            {'key': 'post_lunch', 'name': '午餐后2h', 'time': '14:30', 'type': '午餐后2小时'},
            {'key': 'post_dinner', 'name': '晚餐后2h', 'time': '20:00', 'type': '晚餐后2小时'},
            {'key': 'bedtime', 'name': '睡前', 'time': '22:00', 'type': '睡前'}
        ]

        # 获取今日所有实测血糖记录（非预测）
        c.execute("""
            SELECT value, type, timestamp
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND value > 0
            AND is_predicted = 0
            AND systolic_pressure IS NULL
            ORDER BY timestamp DESC
            LIMIT 1
        """, (user_id, target_date))
        last_measured = c.fetchone()

        if not last_measured:
            return []  # 没有实测数据，无法预测

        base_glucose = last_measured['value']
        base_time = last_measured['timestamp'].split(' ')[1][:5] if ' ' in last_measured['timestamp'] else '07:00'
        base_type = last_measured['type'] or ''

        # 获取今日运动数据
        c.execute("""
            SELECT distance, calories, duration
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND (type IN ('运动', '跑步', '走路', '骑行') OR type LIKE '%跑%')
        """, (user_id, target_date))
        exercise_row = c.fetchone()
        exercise_calories = exercise_row['calories'] if exercise_row else 0

        # 获取今日饮食记录（如有）
        c.execute("""
            SELECT notes, timestamp
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND notes LIKE '%早餐%' OR notes LIKE '%午餐%' OR notes LIKE '%晚餐%'
        """, (user_id, target_date))
        meals = c.fetchall()

        # 获取历史同类型血糖均值作为参考
        c.execute("""
            SELECT type, AVG(value) as avg_value
            FROM records
            WHERE user_id = ?
            AND timestamp > datetime('now', '-30 days')
            AND value > 0
            AND is_predicted = 0
            AND systolic_pressure IS NULL
            GROUP BY type
        """, (user_id,))
        historical_avg = {row['type']: row['avg_value'] for row in c.fetchall()}

        # BMR 计算（用于静息消耗估算）
        config = settings.load_config()
        current_year = datetime.datetime.now().year
        age = current_year - config.get('birth_year', 1964)
        weight = config.get('weight', 75)
        height = config.get('height', 170)
        gender = config.get('gender', 'male')
        s = 5 if gender == 'male' else -161
        bmr = 10 * weight + 6.25 * height - 5 * age + s  # 每日基础代谢
        hourly_bmr = bmr / 24  # 每小时静息消耗

        predictions = []

        for slot in predictable_slots:
            slot_time = slot['time']

            # 跳过已过去的时间点
            if slot_time <= current_time:
                # 检查该时间点是否已有记录
                c.execute("""
                    SELECT id FROM records
                    WHERE user_id = ?
                    AND DATE(timestamp) = ?
                    AND (type LIKE ? OR timestamp LIKE ?)
                """, (user_id, target_date, f"%{slot['name'][:2]}%", f"%{slot_time}%"))
                if c.fetchone():
                    continue  # 已有记录，跳过

            # 跳过当前时间之前的槽位（除非没有记录）
            if slot_time <= base_time:
                continue

            # 检查是否已有该时间点的预测
            c.execute("""
                SELECT id FROM records
                WHERE user_id = ?
                AND DATE(timestamp) = ?
                AND type = ?
                AND is_predicted = 1
            """, (user_id, target_date, slot['type']))
            if c.fetchone():
                continue  # 预测已存在

            # 计算时间差（小时）
            base_h, base_m = map(int, base_time.split(':'))
            slot_h, slot_m = map(int, slot_time.split(':'))
            hours_diff = (slot_h - base_h) + (slot_m - base_m) / 60

            if hours_diff <= 0:
                continue

            # 简单预测模型
            # 基础：历史均值或基于当前值的估算
            hist_avg = historical_avg.get(slot['type'], base_glucose)

            # 血糖变化因素
            # 1. 时间衰减（餐后血糖随时间下降）
            time_decay = 0.1 * hours_diff if '餐后' in base_type else 0.05 * hours_diff

            # 2. 运动影响（降低血糖）
            exercise_effect = min(0.5, exercise_calories / 500) if exercise_calories > 0 else 0

            # 3. 静息消耗
            bmr_effect = hours_diff * hourly_bmr / 2000 * 0.3  # 转换为血糖影响

            # 4. 餐后升高（如果预测的是餐后时间点）
            meal_effect = 0
            if '餐后' in slot['type']:
                meal_effect = 1.5  # 餐后血糖通常升高

            # 综合预测
            predicted_value = base_glucose - time_decay - exercise_effect - bmr_effect + meal_effect

            # 参考历史均值调整
            predicted_value = predicted_value * 0.6 + hist_avg * 0.4

            # 限制在合理范围
            predicted_value = max(3.5, min(15.0, round(predicted_value, 1)))

            # 生成预测记录
            timestamp = f"{target_date} {slot_time}:00"
            notes = f"AI预测 | 基于{base_type}({base_glucose}) | 运动消耗{exercise_calories}kcal"

            c.execute("""
                INSERT INTO records (user_id, value, type, timestamp, is_predicted, notes, created_at)
                VALUES (?, ?, ?, ?, 1, ?, datetime('now'))
            """, (user_id, predicted_value, slot['type'], timestamp, notes))

            predictions.append({
                'type': slot['type'],
                'value': predicted_value,
                'time': slot_time,
                'base_glucose': base_glucose,
                'base_type': base_type
            })

        db.commit()
        return predictions

    except Exception as e:
        print(f"ERROR in predict_remaining_glucose_slots: {e}")
        traceback.print_exc()
        return []


def check_daily_data_complete(db, user_id=1, target_date=None):
    """
    检查指定日期是否具备血糖、血压、运动三类数据

    Args:
        db: 数据库连接
        user_id: 用户ID
        target_date: 目标日期（默认为今天）

    Returns:
        dict: {
            'complete': bool,
            'has_glucose': bool,
            'has_blood_pressure': bool,
            'has_exercise': bool
        }
    """
    if target_date is None:
        target_date = datetime.datetime.now().strftime('%Y-%m-%d')

    try:
        c = db.cursor()

        # 检查是否有血糖数据
        c.execute("""
            SELECT COUNT(*) FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND value > 0
            AND type NOT IN ('跑步', '运动', '血压')
        """, (user_id, target_date,))
        has_glucose = c.fetchone()[0] > 0

        # 检查是否有血压数据
        c.execute("""
            SELECT COUNT(*) FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND systolic_pressure IS NOT NULL
            AND systolic_pressure > 0
        """, (user_id, target_date,))
        has_blood_pressure = c.fetchone()[0] > 0

        # 检查是否有运动数据
        c.execute("""
            SELECT COUNT(*) FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND (type IN ('跑步', '运动') OR distance IS NOT NULL)
        """, (user_id, target_date,))
        has_exercise = c.fetchone()[0] > 0

        complete = has_glucose and has_blood_pressure and has_exercise

        return {
            'complete': complete,
            'has_glucose': has_glucose,
            'has_blood_pressure': has_blood_pressure,
            'has_exercise': has_exercise
        }
    except Exception as e:
        print(f"ERROR in check_daily_data_complete: {e}")
        return {
            'complete': False,
            'has_glucose': False,
            'has_blood_pressure': False,
            'has_exercise': False
        }


def generate_health_analysis(db, user_id=1, is_auto=False, days=7):
    """
    生成综合健康分析

    基于指定天数的血糖、血压、运动、饮食、用药数据，
    利用大模型生成个性化健康分析和建议

    Args:
        db: 数据库连接
        user_id: 用户ID
        is_auto: 是否为自动生成（True=自动，False=手动）
        days: 分析的天数范围（默认7天，可选7/14/30）

    Returns:
        dict: 分析结果或错误信息
    """
    if not api_key:
        return {"error": "未配置 GEMINI_API_KEY"}

    try:
        now = datetime.datetime.now()
        today_str = now.strftime('%Y-%m-%d')

        # 如果是自动生成，检查今天是否已经生成过
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

        # 1. 收集指定天数内的血糖数据
        c = db.cursor()
        c.execute("""
            SELECT value, type, timestamp FROM records
            WHERE user_id = ?
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', ? || ' days')
            AND type NOT IN ('跑步', '运动', '血压')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}',))
        glucose_records = c.fetchall()

        # 2. 收集指定天数内的血压数据
        c.execute("""
            SELECT systolic_pressure, diastolic_pressure, pulse_rate, timestamp
            FROM records
            WHERE user_id = ?
            AND systolic_pressure IS NOT NULL
            AND systolic_pressure > 0
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}',))
        bp_records = c.fetchall()

        # 3. 收集指定天数内的运动数据
        c.execute("""
            SELECT distance, duration, heart_rate, calories, timestamp
            FROM records
            WHERE user_id = ?
            AND (type IN ('跑步', '运动') OR distance IS NOT NULL)
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}',))
        exercise_records = c.fetchall()

        # 4. 收集指定天数内的饮食数据
        c.execute("""
            SELECT calories, diet_analysis, timestamp
            FROM records
            WHERE user_id = ?
            AND calories > 0
            AND type NOT IN ('跑步', '运动')
            AND timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (user_id, f'-{days}',))
        diet_records = c.fetchall()

        # 5. 收集当前用药信息
        c.execute("""
            SELECT medication_name, dosage, times_per_day, timing_notes
            FROM medication_plans
            WHERE user_id = ?
            AND is_active = 1
        """, (user_id,))
        medications = c.fetchall()

        # 6. 构建分析数据摘要
        # 血糖摘要
        if glucose_records:
            glucose_values = [r[0] for r in glucose_records]
            fasting_values = [r[0] for r in glucose_records if '空腹' in r[1]]
            postmeal_values = [r[0] for r in glucose_records if '餐后' in r[1]]

            # 计算空腹平均值（如果有数据）
            fasting_avg_str = f"{sum(fasting_values)/len(fasting_values):.1f} mmol/L（{len(fasting_values)}次）" if fasting_values else "无数据"

            # 计算餐后平均值（如果有数据）
            postmeal_avg_str = f"{sum(postmeal_values)/len(postmeal_values):.1f} mmol/L（{len(postmeal_values)}次）" if postmeal_values else "无数据"

            glucose_summary = f"""
近{days}天血糖数据（共{len(glucose_records)}次测量）：
- 平均血糖: {sum(glucose_values)/len(glucose_values):.1f} mmol/L
- 最高值: {max(glucose_values):.1f} mmol/L
- 最低值: {min(glucose_values):.1f} mmol/L
- 空腹平均: {fasting_avg_str}
- 餐后平均: {postmeal_avg_str}
- 详细记录: {', '.join([f"{r[0]:.1f} mmol/L ({r[1]}, {r[2]})" for r in glucose_records[:10]])}
            """
        else:
            glucose_summary = f"近{days}天无血糖记录"

        # 血压摘要
        if bp_records:
            sys_values = [r[0] for r in bp_records]
            dia_values = [r[1] for r in bp_records]
            bp_summary = f"""
近{days}天血压数据（共{len(bp_records)}次测量）：
- 平均收缩压: {sum(sys_values)/len(sys_values):.0f} mmHg
- 平均舒张压: {sum(dia_values)/len(dia_values):.0f} mmHg
- 最高: {max(sys_values):.0f}/{max(dia_values):.0f} mmHg
- 最低: {min(sys_values):.0f}/{min(dia_values):.0f} mmHg
- 详细记录: {', '.join([f"{r[0]}/{r[1]} mmHg ({r[3]})" for r in bp_records[:5]])}
            """
        else:
            bp_summary = f"近{days}天无血压记录"

        # 运动摘要
        if exercise_records:
            total_distance = sum([r[0] for r in exercise_records if r[0]])
            total_calories = sum([r[3] for r in exercise_records if r[3]])

            # 分离今天和历史数据
            today_str = now.strftime('%Y-%m-%d')
            today_exercises = [r for r in exercise_records if r[4] and r[4].startswith(today_str)]
            today_distance = sum([r[0] for r in today_exercises if r[0]]) if today_exercises else 0
            today_calories = sum([r[3] for r in today_exercises if r[3]]) if today_exercises else 0

            exercise_summary = f"""
近{days}天运动数据（共{len(exercise_records)}次）：
- 总里程: {total_distance:.1f} km，总消耗: {total_calories} kcal
- 平均每次: {total_distance/len(exercise_records):.1f} km, {total_calories/len(exercise_records):.0f} kcal
- 今日运动: {today_distance:.1f} km，消耗 {today_calories} kcal
- 详细记录: {', '.join([f"{r[0]:.1f}km, {r[1]}, {r[2]}bpm ({r[4]})" for r in exercise_records[:5] if r[0]])}
            """
        else:
            exercise_summary = f"近{days}天无运动记录"

        # 饮食摘要（考虑默认餐食）
        # 加载用户配置
        user_config = settings.load_config()
        default_meals = user_config.get('default_meals', {})

        # 计算实际记录的摄入
        recorded_intake = sum([r[0] for r in diet_records]) if diet_records else 0

        # 统计每天的餐食记录情况，补齐默认值
        # 生成分析范围内的所有日期
        from datetime import timedelta
        start_date = now - timedelta(days=days)
        all_dates = [(start_date + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]

        # 按日期分组统计
        daily_meals = {date: {'breakfast': False, 'lunch': False, 'dinner': False, 'recorded_cal': 0} for date in all_dates}

        for record in diet_records:
            date_str = record[2].split(' ')[0]  # timestamp -> date
            if date_str not in daily_meals:
                continue  # 不在分析范围内

            # 判断是哪一餐（简单判断，基于时间或类型）
            time_str = record[2].split(' ')[1] if ' ' in record[2] else '00:00:00'
            hour = int(time_str.split(':')[0])

            if 6 <= hour < 10:
                daily_meals[date_str]['breakfast'] = True
            elif 11 <= hour < 14:
                daily_meals[date_str]['lunch'] = True
            elif 17 <= hour < 21:
                daily_meals[date_str]['dinner'] = True

            daily_meals[date_str]['recorded_cal'] += record[0]

        # 计算补齐的默认卡路里
        estimated_default_cal = 0
        for date_str in daily_meals:
            if not daily_meals[date_str]['breakfast'] and default_meals.get('breakfast', {}).get('enabled', True):
                estimated_default_cal += default_meals.get('breakfast', {}).get('calories', 300)
            if not daily_meals[date_str]['lunch'] and default_meals.get('lunch', {}).get('enabled', True):
                estimated_default_cal += default_meals.get('lunch', {}).get('calories', 500)
            if not daily_meals[date_str]['dinner'] and default_meals.get('dinner', {}).get('enabled', True):
                estimated_default_cal += default_meals.get('dinner', {}).get('calories', 500)

        # 总摄入 = 实际记录 + 估计默认值
        total_estimated_intake = recorded_intake + estimated_default_cal

        if diet_records or estimated_default_cal > 0:
            diet_summary = f"""
近{days}天饮食数据：
- 实际记录摄入: {recorded_intake} kcal（{len(diet_records)}次记录）
- 估算默认摄入: {estimated_default_cal} kcal（未记录的餐食按默认值计）
- 估算总摄入: {total_estimated_intake} kcal
- 平均每天: {total_estimated_intake/days:.0f} kcal
注：未记录的餐食按配置的默认卡路里估算（早餐{default_meals.get('breakfast', {}).get('calories', 300)}，午餐{default_meals.get('lunch', {}).get('calories', 500)}，晚餐{default_meals.get('dinner', {}).get('calories', 500)}大卡）
            """
        else:
            diet_summary = f"近{days}天无饮食记录"

        # 用药摘要
        if medications:
            med_summary = "当前用药方案：\n"
            for med in medications:
                med_summary += f"- {med[0]}"
                if med[1]:
                    med_summary += f" {med[1]}"
                med_summary += f"，{med[2]}次/天"
                if med[3]:
                    med_summary += f"，{med[3]}"
                med_summary += "\n"
        else:
            med_summary = "当前无用药"

        # 用户健康档案
        user_profile = settings.get_ai_system_prompt()

        # 7. 构建 AI Prompt
        prompt = f"""
你是一个专业的糖尿病健康管理顾问。请基于用户近{days}天的综合健康数据，生成一份全面的健康分析报告。

{user_profile}

## 近{days}天健康数据

### 血糖数据
{glucose_summary}

### 血压数据
{bp_summary}

### 运动数据
{exercise_summary}

### 饮食数据
{diet_summary}

### 用药情况
{med_summary}

## 分析任务

请从以下维度进行综合分析：

1. **健康评分**（0-100分）：基于血糖控制、血压水平、运动频率、能量平衡的综合评分
2. **血糖控制评估**：达标率、波动性、趋势分析
3. **血压健康状况**：是否在正常范围、异常预警
4. **运动与能量平衡**：运动量是否充足、能量平衡情况
5. **用药依从性**：用药效果评估
6. **个性化建议**：
   - 饮食建议（具体到食物种类和份量）
   - 运动建议（强度、频率、时间）
   - 用药建议（如有需要）
   - 生活方式建议

请以专业、温和、鼓励的语气，返回以下JSON格式（不要包含markdown格式）：

{{
    "health_score": int (0-100),
    "glucose_summary": "string (血糖控制评估，2-3句话)",
    "blood_pressure_summary": "string (血压健康状况，2-3句话)",
    "exercise_summary": "string (运动与能量评估，2-3句话)",
    "medication_summary": "string (用药情况评估，1-2句话)",
    "recommendations": [
        "建议1：具体可执行的建议",
        "建议2：具体可执行的建议",
        "建议3：具体可执行的建议"
    ],
    "full_analysis": "string (完整的分析报告，markdown格式，包含所有维度的详细分析)"
}}
        """

        # 8. 调用 Gemini API
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt
        )
        raw_text = response.text

        print(f"DEBUG Health Analysis: Raw AI response: {raw_text[:500]}...")

        # 9. 解析 AI 响应
        match = re.search(r'\{[\s\S]*\}', raw_text)
        if not match:
            print("ERROR: AI response is not valid JSON")
            return {"error": "AI 响应解析失败"}

        result = json.loads(match.group(0))

        # 10. 保存到数据库
        c.execute("""
            INSERT INTO health_analyses
            (user_id, analysis_date, health_score, glucose_summary, blood_pressure_summary,
             exercise_summary, medication_summary, recommendations, full_analysis, is_auto_generated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            today_str,
            result.get('health_score'),
            result.get('glucose_summary'),
            result.get('blood_pressure_summary'),
            result.get('exercise_summary'),
            result.get('medication_summary'),
            json.dumps(result.get('recommendations', []), ensure_ascii=False),
            result.get('full_analysis'),
            1 if is_auto else 0
        ))
        db.commit()

        print(f"✓ Health analysis generated: Score {result.get('health_score')}/100")

        return {
            "success": True,
            "analysis_id": c.lastrowid,
            "result": result
        }

    except Exception as e:
        error_str = str(e)
        print(f"ERROR in generate_health_analysis: {error_str}")
        traceback.print_exc()

        # 特殊处理 429 配额错误
        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
            # 尝试提取重试时间
            import re
            retry_match = re.search(r'retry in (\d+)', error_str.lower())
            retry_seconds = int(retry_match.group(1)) if retry_match else 60

            return {
                "error": f"AI 服务配额已用尽，请等待 {retry_seconds} 秒后重试",
                "error_type": "quota_exceeded",
                "retry_after": retry_seconds
            }

        return {"error": str(e)}


def auto_trigger_health_analysis(db, user_id=1):
    """
    自动触发健康分析（当三类数据齐全时）

    检查今天是否已有血糖、血压、运动数据，
    如果三者齐全且今天还没有自动生成过分析，则自动生成
    """
    try:
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')

        # 检查数据完整性
        status = check_daily_data_complete(db, user_id, today_str)

        if status['complete']:
            print(f"✓ Daily data complete for {today_str}, triggering auto analysis...")
            result = generate_health_analysis(db, user_id, is_auto=True)
            if result.get('success'):
                print(f"✓ Auto health analysis generated successfully")
            elif result.get('skipped'):
                print(f"⊙ Auto analysis skipped: {result.get('message')}")
            else:
                print(f"✗ Auto analysis failed: {result.get('error')}")
        else:
            missing = []
            if not status['has_glucose']:
                missing.append('血糖')
            if not status['has_blood_pressure']:
                missing.append('血压')
            if not status['has_exercise']:
                missing.append('运动')
            print(f"⊙ Daily data incomplete (missing: {', '.join(missing)}), skipping auto analysis")

    except Exception as e:
        print(f"ERROR in auto_trigger_health_analysis: {e}")
        traceback.print_exc()


@app.route('/')
def index():
    try:
        db = get_db()
        c = db.cursor()

        # 获取当前用户
        current_user_id = user_manager.get_current_user_id()
        current_user = user_manager.get_user(current_user_id)

        # 自动生成早晨空腹血糖预测（如果符合条件）
        predict_morning_fpg(db, current_user_id)

        # 自动生成运动后餐前血糖预测（如果符合条件）
        predict_post_exercise_glucose(db, current_user_id)

        # 获取分页参数，默认显示最近 14 天
        days = request.args.get('days', 14, type=int)
        page = request.args.get('page', 1, type=int)
        show_all = request.args.get('all', False, type=bool)

        # 1. Fetch records with pagination and user filter
        # 返回所有记录（包括预测），前端根据 is_verified 决定如何显示
        if show_all:
            c.execute("""SELECT *,
                        CASE WHEN is_predicted = 1 AND verified_by_real_id IS NOT NULL THEN 1 ELSE 0 END as is_verified
                        FROM records
                        WHERE user_id = ?
                        ORDER BY timestamp ASC""", (current_user_id,))
        else:
            c.execute("""SELECT *,
                        CASE WHEN is_predicted = 1 AND verified_by_real_id IS NOT NULL THEN 1 ELSE 0 END as is_verified
                        FROM records
                        WHERE user_id = ?
                        AND timestamp > datetime('now', ?)
                        ORDER BY timestamp ASC""", (current_user_id, f'-{days} days'))
        rows = c.fetchall()

        # Convert to list of dicts to allow modification
        records = [dict(row) for row in rows]

        # 2. Calculate Trends
        last_values = {} # stores last value for each type key

        for r in records:
            # Simplify type for comparison
            if '空腹' in r['type']:
                key = 'fasting'
            elif '餐后' in r['type']:
                key = 'post'
            else:
                key = 'other'

            r['trend'] = 0
            r['trend_dir'] = 'flat'

            if key in last_values and r['value'] > 0: # Only compare glucose values
                diff = r['value'] - last_values[key]
                r['trend'] = round(abs(diff), 1)
                if diff > 0: r['trend_dir'] = 'up'
                elif diff < 0: r['trend_dir'] = 'down'

            if r['value'] > 0:
                last_values[key] = r['value']

        # 3. Group by Date for Timeline View
        from collections import defaultdict

        # 计算基础代谢 (使用 settings 中的配置)
        USER_BMR = settings.calculate_bmr()

        grouped_records = defaultdict(lambda: {
            'entries': [],
            'medication_plans': [],  # 当天应服用的药物方案
            'stats': {
                'cal_in': 0,
                'cal_out_exercise': 0,  # 运动消耗
                'cal_out_bmr': USER_BMR,  # 基础代谢
                'avg_glucose': 0,
                'glucose_count': 0
            }
        })

        # Sort by timestamp DESC initially
        records.sort(key=lambda x: x['timestamp'], reverse=True)

        for r in records:
            date_str = r['timestamp'].split(' ')[0] # YYYY-MM-DD
            day_group = grouped_records[date_str]
            day_group['entries'].append(r)

            # Stats calculation
            calories = r.get('calories') or 0
            if calories > 0:
                # 判断是摄入还是消耗：运动类型是消耗，其他是摄入
                if r['type'] in ['跑步', '运动'] or r.get('distance'):
                    day_group['stats']['cal_out_exercise'] += calories
                else:
                    day_group['stats']['cal_in'] += calories

            value = r.get('value') or 0
            if value > 0:
                day_group['stats']['avg_glucose'] += value
                day_group['stats']['glucose_count'] += 1

        # 4. Fetch active medication plans (user-specific)
        c.execute("""SELECT * FROM medication_plans
                    WHERE user_id = ?
                    AND is_active = 1
                    ORDER BY medication_name ASC""", (current_user_id,))
        med_plan_rows = c.fetchall()
        medication_plans = [dict(row) for row in med_plan_rows]

        # 为每个日期添加当天应服用的药物方案
        for date_str in grouped_records.keys():
            for plan in medication_plans:
                # 检查日期是否在方案有效期内
                plan_start = plan['start_date']
                plan_end = plan['end_date'] if plan['end_date'] else '9999-12-31'

                if plan_start <= date_str <= plan_end:
                    # 根据频率判断是否在当天显示
                    frequency = plan.get('frequency', 'daily')
                    frequency_detail = plan.get('frequency_detail')

                    should_show = False

                    if frequency == 'daily':
                        # 每天服用
                        should_show = True
                    elif frequency == 'weekly' and frequency_detail:
                        # 每周特定日期服用
                        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d')
                        weekday_name = date_obj.strftime('%A')  # Monday, Tuesday, etc.
                        should_show = (weekday_name == frequency_detail)
                    elif frequency == 'monthly' and frequency_detail:
                        # 每月特定日期服用（如 "1,15" 表示1号和15号）
                        day_of_month = int(date_str.split('-')[2])
                        allowed_days = [int(d) for d in frequency_detail.split(',')]
                        should_show = (day_of_month in allowed_days)
                    else:
                        # 默认每天显示
                        should_show = True

                    if should_show:
                        grouped_records[date_str]['medication_plans'].append(plan)

        # Post-process stats: BMR correction for today & Net Calories
        now = datetime.datetime.now()
        today_str = now.strftime('%Y-%m-%d')

        # 加载默认餐食配置
        user_config = settings.load_config()
        default_meals = user_config.get('default_meals', {})

        sorted_dates = []
        for date_str, data in grouped_records.items():
            stats = data['stats']

            # 1. BMR Correction for Today
            # 如果是今天，BMR 应该按当前时间比例计算
            if date_str == today_str:
                current_minutes = now.hour * 60 + now.minute
                total_minutes = 24 * 60
                stats['cal_out_bmr'] = int(USER_BMR * (current_minutes / total_minutes))

            # 2. 检查并补充缺失的餐食摄入（默认基础摄入）
            # 检查该天是否有早餐、午餐、晚餐的记录
            has_breakfast = False
            has_lunch = False
            has_dinner = False

            for entry in data['entries']:
                entry_type = entry['type'].lower() if entry['type'] else ''
                entry_time = entry['timestamp'].split(' ')[1] if ' ' in entry['timestamp'] else ''

                # 检查是否是餐食记录（calories > 0 且不是运动）
                calories = entry.get('calories') or 0
                if calories > 0 and entry['type'] not in ['跑步', '运动']:
                    # 根据类型或时间判断是哪一餐
                    if '早餐' in entry_type or '晨跑前' in entry_type:
                        has_breakfast = True
                    elif '午餐' in entry_type:
                        has_lunch = True
                    elif '晚餐' in entry_type:
                        has_dinner = True
                    else:
                        # 根据时间推断餐食类型
                        hour = int(entry_time.split(':')[0]) if ':' in entry_time else 0
                        if 7 <= hour < 10:
                            has_breakfast = True
                        elif 11 <= hour < 14:
                            has_lunch = True
                        elif 17 <= hour < 21:
                            has_dinner = True

            # 补充缺失的餐食默认摄入
            added_default_calories = 0
            if default_meals.get('breakfast', {}).get('enabled', True) and not has_breakfast:
                cal = default_meals.get('breakfast', {}).get('calories') or 0
                added_default_calories += cal
            if default_meals.get('lunch', {}).get('enabled', True) and not has_lunch:
                cal = default_meals.get('lunch', {}).get('calories') or 0
                added_default_calories += cal
            if default_meals.get('dinner', {}).get('enabled', True) and not has_dinner:
                cal = default_meals.get('dinner', {}).get('calories') or 0
                added_default_calories += cal

            # 添加默认摄入到统计中
            if added_default_calories > 0:
                stats['cal_in'] += added_default_calories
                stats['default_calories_added'] = added_default_calories
            else:
                stats['default_calories_added'] = 0

            # 3. Glucose Average
            if stats['glucose_count'] > 0:
                stats['avg_glucose'] = round(stats['avg_glucose'] / stats['glucose_count'], 1)

            # 4. Net Calories Calculation
            # 摄入 - (基础代谢 + 运动消耗)
            total_out = stats['cal_out_bmr'] + stats['cal_out_exercise']
            net = stats['cal_in'] - total_out
            stats['net_calories'] = int(net)
            # is_deficit: True if Intake < Output (Green), False if Intake > Output (Red)
            stats['is_deficit'] = net < 0

            # Sort entries within the day ASCENDING (Morning -> Night) for a natural timeline flow
            data['entries'].sort(key=lambda x: x['timestamp'])

            sorted_dates.append({'date': date_str, 'data': data})

        # 4. Calculate 7-Day Stats for Dashboard

        # === 血糖统计 ===
        # 近7天平均早空腹血糖（排除预测数据）
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE user_id = ?
            AND (type LIKE '%空腹%' OR type LIKE '%早空腹%')
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        avg_fasting_7d = c.fetchone()[0]

        # 近7天平均餐后2小时血糖（排除预测数据）
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE user_id = ?
            AND type LIKE '%餐后2小时%'
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        avg_post2h_7d = c.fetchone()[0]

        # 近7日最高血糖（排除预测数据）
        c.execute("""
            SELECT MAX(value) FROM records
            WHERE user_id = ?
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        max_glucose_7d = c.fetchone()[0]

        # 近7日最低血糖（排除预测数据）
        c.execute("""
            SELECT MIN(value) FROM records
            WHERE user_id = ?
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        min_glucose_7d = c.fetchone()[0]

        # === 运动统计 ===
        # 最近7天跑步总里程数
        c.execute("""
            SELECT SUM(distance) FROM records
            WHERE user_id = ?
            AND distance IS NOT NULL
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        total_distance_7d = c.fetchone()[0]

        # 运动总消耗卡路里数（7天）
        c.execute("""
            SELECT SUM(calories) FROM records
            WHERE user_id = ?
            AND (type = '跑步' OR type = '运动' OR distance IS NOT NULL)
            AND calories > 0
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        total_exercise_cal_7d = c.fetchone()[0]

        # 最近7天跑步平均心率
        c.execute("""
            SELECT AVG(heart_rate) FROM records
            WHERE user_id = ?
            AND heart_rate IS NOT NULL
            AND (type = '跑步' OR type = '运动')
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        avg_heart_rate_7d = c.fetchone()[0]

        # === 血压统计 ===
        # 最近7天平均血压（排除 0 值）
        c.execute("""
            SELECT AVG(systolic_pressure), AVG(diastolic_pressure), COUNT(*)
            FROM records
            WHERE user_id = ?
            AND systolic_pressure IS NOT NULL
            AND diastolic_pressure IS NOT NULL
            AND systolic_pressure > 0
            AND diastolic_pressure > 0
            AND timestamp > datetime('now', '-7 days')
        """, (current_user_id,))
        bp_7d_stats = c.fetchone()

        # 血压最高的一天（7天内，排除 0 值）
        c.execute("""
            SELECT MAX(systolic_pressure), MAX(diastolic_pressure),
                   DATE(timestamp) as day
            FROM records
            WHERE user_id = ?
            AND systolic_pressure IS NOT NULL
            AND systolic_pressure > 0
            AND timestamp > datetime('now', '-7 days')
            GROUP BY day
            ORDER BY systolic_pressure DESC
            LIMIT 1
        """, (current_user_id,))
        bp_max_day = c.fetchone()

        # 血压最低的一天（7天内，排除 0 值）
        c.execute("""
            SELECT MIN(systolic_pressure), MIN(diastolic_pressure),
                   DATE(timestamp) as day
            FROM records
            WHERE user_id = ?
            AND systolic_pressure IS NOT NULL
            AND systolic_pressure > 0
            AND timestamp > datetime('now', '-7 days')
            GROUP BY day
            ORDER BY systolic_pressure ASC
            LIMIT 1
        """, (current_user_id,))
        bp_min_day = c.fetchone()

        # === 用药情况 ===
        # 今日应服用的药物清单（根据频率筛选）
        today = datetime.datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        weekday_name = today.strftime('%A')  # Monday, Tuesday, etc.
        day_of_month = today.day

        c.execute("""
            SELECT medication_name, dosage, times_per_day, timing_notes, frequency, frequency_detail
            FROM medication_plans
            WHERE user_id = ?
            AND is_active = 1
            ORDER BY medication_name ASC
        """, (current_user_id,))
        all_meds = c.fetchall()

        active_medications = []
        for row in all_meds:
            med_dict = dict(zip(['name', 'dosage', 'times', 'timing', 'frequency', 'frequency_detail'], row))
            frequency = med_dict.get('frequency', 'daily')
            frequency_detail = med_dict.get('frequency_detail')

            # 判断今天是否应该服用
            should_take_today = False
            if frequency == 'daily':
                should_take_today = True
            elif frequency == 'weekly' and frequency_detail:
                should_take_today = (weekday_name == frequency_detail)
            elif frequency == 'monthly' and frequency_detail:
                allowed_days = [int(d.strip()) for d in frequency_detail.split(',')]
                should_take_today = (day_of_month in allowed_days)
            else:
                should_take_today = True  # 默认显示

            if should_take_today:
                # 只保留前端需要的字段
                active_medications.append({
                    'name': med_dict['name'],
                    'dosage': med_dict['dosage'],
                    'times': med_dict['times'],
                    'timing': med_dict['timing']
                })

        # Calculate Compliance Rate (7 days)
        # 使用科学的血糖达标标准（基于《中国糖尿病防治指南（2024版）》）
        c.execute("""
            SELECT value, type FROM records
            WHERE user_id = ?
            AND timestamp > datetime('now', '-7 days')
            AND value > 0
            AND is_predicted = 0
            AND systolic_pressure IS NULL
        """, (current_user_id,))
        recent_glucose = c.fetchall()
        total_glucose = len(recent_glucose)
        ok_count = 0
        optimal_count = 0

        for row in recent_glucose:
            val = row['value']
            glucose_type = row['type'] or ''
            result = settings.check_glucose_compliance(val, glucose_type)
            if result['is_compliant']:
                ok_count += 1
            if result['is_optimal']:
                optimal_count += 1

        compliance = int((ok_count / total_glucose * 100)) if total_glucose > 0 else 0
        optimal_rate = int((optimal_count / total_glucose * 100)) if total_glucose > 0 else 0

        # 获取对应的徽章
        compliance_badge = settings.get_badge_for_rate(compliance)

        # 获取总记录数用于分页显示
        c.execute("SELECT COUNT(*) FROM records WHERE user_id = ?", (current_user_id,))
        total_records = c.fetchone()[0]

        # ========== 今日概览数据 ==========
        # 定义今日血糖测量时间点
        today_schedule = [
            {'key': 'fasting', 'name': '空腹', 'time': '07:15', 'icon': 'sunrise'},
            {'key': 'post_exercise', 'name': '运动后餐前', 'time': '08:45', 'icon': 'bicycle'},
            {'key': 'post_breakfast', 'name': '早餐后2h', 'time': '11:00', 'icon': 'cup-hot'},
            {'key': 'post_lunch', 'name': '午餐后2h', 'time': '14:30', 'icon': 'sun'},
            {'key': 'post_dinner', 'name': '晚餐后2h', 'time': '20:00', 'icon': 'moon-stars'},
            {'key': 'bedtime', 'name': '睡前', 'time': '22:00', 'icon': 'moon'}
        ]

        # 查询今日所有血糖记录（实测值优先）
        c.execute("""
            SELECT value, type, timestamp, is_predicted
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND value > 0
            AND systolic_pressure IS NULL
            ORDER BY timestamp ASC, is_predicted ASC
        """, (current_user_id, today_str))
        today_glucose_records = c.fetchall()

        # 匹配今日记录到各时间点（实测值优先于预测值）
        today_overview = []
        for slot in today_schedule:
            slot_data = {
                'key': slot['key'],
                'name': slot['name'],
                'time': slot['time'],
                'icon': slot['icon'],
                'value': None,
                'is_predicted': False,
                'status': 'pending',  # pending, measured, predicted
                'compliance': None
            }

            # 分别收集匹配的实测值和预测值
            measured_match = None
            predicted_match = None

            for record in today_glucose_records:
                record_type = record['type'] or ''
                record_time = record['timestamp'].split(' ')[1][:5] if ' ' in record['timestamp'] else ''
                is_pred = record['is_predicted']

                # 匹配逻辑
                matched = False
                if slot['key'] == 'fasting' and '空腹' in record_type:
                    matched = True
                elif slot['key'] == 'post_exercise' and '运动后餐前' in record_type:
                    matched = True
                elif slot['key'] == 'post_breakfast' and ('早餐后' in record_type or ('餐后' in record_type and '11' in record_time)):
                    matched = True
                elif slot['key'] == 'post_lunch' and ('午餐后' in record_type or ('餐后' in record_type and '14' in record_time)):
                    matched = True
                elif slot['key'] == 'post_dinner' and ('晚餐后' in record_type or ('餐后' in record_type and '20' in record_time)):
                    matched = True
                elif slot['key'] == 'bedtime' and '睡前' in record_type:
                    matched = True

                if matched:
                    if not is_pred and measured_match is None:
                        measured_match = record
                    elif is_pred and predicted_match is None:
                        predicted_match = record

            # 优先使用实测值，其次使用预测值
            chosen_record = measured_match if measured_match else predicted_match
            if chosen_record:
                slot_data['value'] = chosen_record['value']
                slot_data['is_predicted'] = bool(chosen_record['is_predicted'])
                slot_data['status'] = 'predicted' if chosen_record['is_predicted'] else 'measured'
                result = settings.check_glucose_compliance(chosen_record['value'], chosen_record['type'])
                slot_data['compliance'] = result['level']

            today_overview.append(slot_data)

        # 计算今日完成率
        measured_count = sum(1 for s in today_overview if s['status'] == 'measured')
        predicted_count = sum(1 for s in today_overview if s['status'] == 'predicted')
        today_completion = {
            'measured': measured_count,
            'predicted': predicted_count,
            'total': len(today_schedule),
            'percentage': int(measured_count / len(today_schedule) * 100)
        }

        # 今日运动数据 - 支持多种运动类型
        c.execute("""
            SELECT type, distance, calories, duration, heart_rate, pace, timestamp
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND (type IN ('运动', '跑步', '走路', '骑行', '游泳', '健身')
                 OR type LIKE '%跑%' OR type LIKE '%走%' OR type LIKE '%骑%')
            ORDER BY timestamp DESC
            LIMIT 1
        """, (current_user_id, today_str))
        today_exercise_row = c.fetchone()
        today_exercise = None
        if today_exercise_row:
            today_exercise = {
                'type': today_exercise_row['type'],
                'distance': today_exercise_row['distance'],
                'calories': today_exercise_row['calories'],
                'duration': today_exercise_row['duration'],
                'heart_rate': today_exercise_row['heart_rate'],
                'pace': today_exercise_row['pace'],
                'time': today_exercise_row['timestamp'].split(' ')[1][:5] if ' ' in today_exercise_row['timestamp'] else ''
            }

        # 今日血压数据
        c.execute("""
            SELECT systolic_pressure, diastolic_pressure, heart_rate, timestamp
            FROM records
            WHERE user_id = ?
            AND DATE(timestamp) = ?
            AND systolic_pressure IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 1
        """, (current_user_id, today_str))
        today_bp_row = c.fetchone()
        today_bp = None
        if today_bp_row:
            today_bp = {
                'systolic': today_bp_row['systolic_pressure'],
                'diastolic': today_bp_row['diastolic_pressure'],
                'heart_rate': today_bp_row['heart_rate'],
                'time': today_bp_row['timestamp'].split(' ')[1][:5] if ' ' in today_bp_row['timestamp'] else ''
            }

        # 今日用药状态 - 获取详细信息
        # 1. 获取今日应服用的药物（激活的药物计划）
        today_weekday = datetime.datetime.now().strftime('%A')  # e.g., 'Monday'
        c.execute("""
            SELECT id, medication_name, dosage, times_per_day, timing_notes, frequency, frequency_detail
            FROM medication_plans
            WHERE user_id = ? AND is_active = 1
            AND (start_date IS NULL OR start_date <= ?)
            AND (end_date IS NULL OR end_date >= ?)
        """, (current_user_id, today_str, today_str))
        all_med_plans = c.fetchall()

        # 过滤出今日应服用的药物
        today_med_plans = []
        for plan in all_med_plans:
            freq = plan['frequency'] or 'daily'
            freq_detail = plan['frequency_detail'] or ''

            should_take_today = False
            if freq == 'daily':
                should_take_today = True
            elif freq == 'weekly' and today_weekday in freq_detail:
                should_take_today = True
            elif freq == 'custom':
                should_take_today = True  # 简化处理

            if should_take_today:
                today_med_plans.append({
                    'id': plan['id'],
                    'name': plan['medication_name'],
                    'dosage': plan['dosage'],
                    'times': plan['times_per_day'],
                    'timing': plan['timing_notes']
                })

        # 2. 获取今日已服用的记录
        c.execute("""
            SELECT plan_id, COUNT(*) as count FROM medication_logs
            WHERE user_id = ? AND log_date = ?
            GROUP BY plan_id
        """, (current_user_id, today_str))
        taken_logs = {row['plan_id']: row['count'] for row in c.fetchall()}

        # 3. 汇总今日用药状态
        today_med_status = {
            'plans': today_med_plans,
            'taken_count': sum(taken_logs.values()) if taken_logs else 0,
            'total_required': sum(p['times'] for p in today_med_plans),
            'taken_details': taken_logs
        }

        # 获取最新的健康分析
        c.execute("""
            SELECT * FROM health_analyses
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (current_user_id,))
        latest_analysis_row = c.fetchone()
        latest_analysis = None
        if latest_analysis_row:
            latest_analysis = dict(latest_analysis_row)
            # 解析 recommendations JSON
            if latest_analysis.get('recommendations'):
                try:
                    latest_analysis['recommendations'] = json.loads(latest_analysis['recommendations'])
                except:
                    latest_analysis['recommendations'] = []

        stats = {
            'total_records': total_records,
            'current_days': days,
            'show_all': show_all,
            'today_str': today_str,
            'user': settings.load_config(),
            'compliance': compliance,
            'optimal_rate': optimal_rate,
            'compliance_badge': compliance_badge,
            'glucose_targets': settings.GLUCOSE_TARGETS,
            'badge_system': settings.BADGE_SYSTEM,

            # === 今日概览 ===
            'today_overview': today_overview,
            'today_completion': today_completion,
            'today_exercise': today_exercise,
            'today_bp': today_bp,
            'today_med_status': today_med_status,

            # === 血糖统计（7天） ===
            'avg_fasting_7d': round(avg_fasting_7d, 1) if avg_fasting_7d else 0,
            'avg_post2h_7d': round(avg_post2h_7d, 1) if avg_post2h_7d else 0,
            'max_glucose_7d': round(max_glucose_7d, 1) if max_glucose_7d else 0,
            'min_glucose_7d': round(min_glucose_7d, 1) if min_glucose_7d else 0,

            # === 运动统计（7天） ===
            'total_distance_7d': round(total_distance_7d, 1) if total_distance_7d else 0,
            'total_exercise_cal_7d': int(total_exercise_cal_7d) if total_exercise_cal_7d else 0,
            'avg_heart_rate_7d': round(avg_heart_rate_7d) if avg_heart_rate_7d else 0,

            # === 血压统计（7天） ===
            'avg_systolic_7d': round(bp_7d_stats[0]) if bp_7d_stats[0] else 0,
            'avg_diastolic_7d': round(bp_7d_stats[1]) if bp_7d_stats[1] else 0,
            'bp_count_7d': bp_7d_stats[2] if bp_7d_stats[2] else 0,
            'bp_max_sys': bp_max_day[0] if bp_max_day else 0,
            'bp_max_dia': bp_max_day[1] if bp_max_day else 0,
            'bp_max_date': bp_max_day[2] if bp_max_day else '-',
            'bp_min_sys': bp_min_day[0] if bp_min_day else 0,
            'bp_min_dia': bp_min_day[1] if bp_min_day else 0,
            'bp_min_date': bp_min_day[2] if bp_min_day else '-',

            # === 用药情况 ===
            'active_medications': active_medications,

            # === 健康分析 ===
            'latest_analysis': latest_analysis
        }

        return render_template('index.html', records=records, stats=stats, timeline=sorted_dates)
    except Exception as e:
        traceback.print_exc()
        return f"Error loading records: {e}", 500

@app.route('/settings', methods=['GET'])
def get_settings():
    return jsonify(settings.load_config())

@app.route('/settings', methods=['POST'])
def update_settings():
    try:
        new_config = request.json
        # 简单验证逻辑
        if not new_config.get('weight') or not new_config.get('height'):
            return jsonify({"status": "error", "message": "Invalid data"}), 400
        
        settings.save_config(new_config)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    if 'avatar' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
    if file and allowed_file(file.filename):
        filename = f"avatar_{int(datetime.datetime.now().timestamp())}.png"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Update user config
        config = settings.load_config()
        config['avatar'] = filename
        settings.save_config(config)
        
        return jsonify({"status": "success", "avatar_url": url_for('static', filename=f'avatars/{filename}')})
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

@app.route('/add', methods=['POST'])
def add_record():
    try:
        # Support both form data and JSON
        if request.is_json:
            data = request.json
            value = data.get('value', 0)
            unit = data.get('unit', 'mmol/L')
            r_type = data.get('type')
            notes = data.get('notes', '')
            timestamp = data.get('timestamp')
            calories = data.get('calories', 0)
            diet_analysis = data.get('diet_analysis', '')
            is_predicted = data.get('is_predicted', 0)
            distance = data.get('distance')
            duration = data.get('duration')
            heart_rate = data.get('heart_rate')
            systolic_pressure = data.get('systolic_pressure')
            diastolic_pressure = data.get('diastolic_pressure')
            pulse_rate = data.get('pulse_rate')
        else:
            value = request.form.get('value')
            unit = request.form.get('unit')
            r_type = request.form.get('type')
            notes = request.form.get('notes')
            timestamp = request.form.get('timestamp')

            # Validation
            if not value:
                return "Value is required", 400

            # Optional fields
            calories = request.form.get('calories', 0)
            diet_analysis = request.form.get('diet_analysis', '')
            is_predicted = request.form.get('is_predicted', 0)
            distance = request.form.get('distance')
            duration = request.form.get('duration')
            heart_rate = request.form.get('heart_rate')
            systolic_pressure = request.form.get('systolic_pressure')
            diastolic_pressure = request.form.get('diastolic_pressure')
            pulse_rate = request.form.get('pulse_rate')

        # Handle empty timestamp (default to now)
        if not timestamp:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if 'T' in timestamp:
            timestamp = timestamp.replace('T', ' ')
            if len(timestamp) == 16: # Missing seconds
                timestamp += ':00'

        db = get_db()
        c = db.cursor()

        # Get current user ID
        current_user_id = user_manager.get_current_user_id()

        c.execute("""INSERT INTO records
                     (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                      distance, duration, heart_rate, systolic_pressure, diastolic_pressure, pulse_rate)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (current_user_id, value, unit, r_type, notes, timestamp, calories, diet_analysis, is_predicted,
                   distance, duration, heart_rate, systolic_pressure, diastolic_pressure, pulse_rate))

        # 如果是真实血糖记录，尝试关联当天的预测记录
        real_record_id = c.lastrowid
        try:
            numeric_value = float(value) if value else 0
            if not is_predicted and numeric_value > 0 and r_type:
                record_date = timestamp.split(' ')[0] if ' ' in timestamp else timestamp[:10]
                link_prediction_to_real_record(db, real_record_id, current_user_id, record_date, r_type, numeric_value)
        except (ValueError, TypeError) as e:
            print(f"Warning: Could not link prediction for record {real_record_id}: {e}")

        db.commit()

        if request.is_json:
            return jsonify({"status": "success"})
        return redirect(url_for('index'))
    except Exception as e:
        return f"Error adding record: {e}", 500

def get_user_stats(db, user_id=1):
    stats = {}
    try:
        c = db.cursor()
        # 1. Avg Fasting (Last 30 days)
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE user_id = ?
            AND type LIKE '%空腹%'
            AND is_predicted = 0
            AND timestamp > datetime('now', '-30 days')
        """, (user_id,))
        row = c.fetchone()
        stats['avg_fasting'] = round(row[0], 1) if row and row[0] else '未知'

        # 2. Avg Post-meal (Last 30 days)
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE user_id = ?
            AND type LIKE '%餐后%'
            AND is_predicted = 0
            AND timestamp > datetime('now', '-30 days')
        """, (user_id,))
        row = c.fetchone()
        stats['avg_postmeal'] = round(row[0], 1) if row and row[0] else '未知'

        # 3. Last record
        c.execute("SELECT value, type FROM records WHERE user_id = ? AND is_predicted = 0 ORDER BY timestamp DESC LIMIT 1", (user_id,))
        row = c.fetchone()
        if row:
            stats['last_value'] = row[0]
            stats['last_type'] = row[1]
        else:
            stats['last_value'] = '未知'
            stats['last_type'] = ''

    except Exception as e:
        print(f"Stats error: {e}")

    return stats

@app.route('/parse_ai', methods=['POST'])
def parse_ai():
    try:
        data = request.json
        text = data.get('text', '')
        images_b64 = data.get('images', []) # 接收图片数组
        mime_type = data.get('mime_type', 'image/jpeg')

        images_data = []
        if images_b64:
            import base64
            for img_b64 in images_b64:
                # 解码每张图片
                image_data = base64.b64decode(img_b64.split(',')[-1])
                images_data.append(image_data)

        # Get history context for better prediction
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        history_context = get_user_stats(db, current_user_id)

        results = parse_glucose_input(text, history_context, images_data, mime_type)
        return jsonify(results)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/batch_add', methods=['POST'])
def batch_add():
    try:
        data = request.json.get('records')
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        db = get_db()
        c = db.cursor()

        # Get current user ID
        current_user_id = user_manager.get_current_user_id()

        # 第一遍：插入所有记录，记录每条记录的信息
        inserted_records = []

        for r in data:
            if 'value' not in r or 'type' not in r:
                continue

            cal = r.get('calories', 0)
            da = r.get('diet_analysis', '')
            is_pred = 1 if r.get('is_predicted', False) else 0

            # 如果是预测记录，先删除同一天同一类型同一时间的其他预测记录，避免重复
            if is_pred and r.get('datetime'):
                timestamp = r.get('datetime')
                record_type = r['type']

                c.execute("""DELETE FROM records
                           WHERE user_id = ?
                           AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?)
                           AND type = ?
                           AND is_predicted = 1""",
                         (current_user_id, timestamp, record_type))

            c.execute("""INSERT INTO records
                      (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                       distance, duration, heart_rate, pace, cadence,
                       systolic_pressure, diastolic_pressure, pulse_rate)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (current_user_id, r['value'], r.get('unit', 'mmol/L'), r['type'], r.get('notes', ''),
                       r.get('datetime'), cal, da, is_pred,
                       r.get('distance'), r.get('duration'), r.get('heart_rate'),
                       r.get('pace'), r.get('cadence'),
                       r.get('systolic_pressure'), r.get('diastolic_pressure'), r.get('pulse_rate')))

            # 记录插入信息，用于第二遍关联
            inserted_records.append({
                'id': c.lastrowid,
                'is_pred': is_pred,
                'value': r.get('value', 0),
                'datetime': r.get('datetime', ''),
                'type': r['type']
            })

        # 第二遍：处理真实记录与预测记录的关联（此时所有记录都已插入）
        for record in inserted_records:
            if not record['is_pred'] and record['value'] and record['datetime']:
                try:
                    numeric_value = float(record['value'])
                    if numeric_value > 0:
                        record_date = record['datetime'].split(' ')[0] if ' ' in record['datetime'] else record['datetime'][:10]
                        link_prediction_to_real_record(db, record['id'], current_user_id, record_date, record['type'], numeric_value)
                except (ValueError, TypeError) as e:
                    print(f"Warning: Could not link prediction for batch record {record['id']}: {e}")

        db.commit()

        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/backfill_predictions', methods=['POST'])
def backfill_predictions():
    """
    批量补全历史运动后餐前血糖预测

    POST 参数:
    - days: 回溯天数（默认30天）
    """
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()

        days = request.json.get('days', 30) if request.json else 30

        result = backfill_post_exercise_predictions(db, current_user_id, days)

        return jsonify({
            "status": "success",
            "message": f"成功预测 {result['success']} 条记录，跳过 {result['skipped']} 条",
            "data": result
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/delete/<int:id>', methods=['POST'])
def delete(id):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("DELETE FROM records WHERE id = ?", (id,))
        db.commit()
        # 支持 AJAX 和表单提交两种方式
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"status": "success"})
        return redirect(url_for('index'))
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"status": "error", "message": str(e)}), 500
        return f"Error deleting record: {e}", 500

@app.route('/record/<int:id>')
def get_record(id):
    """获取单条记录用于编辑"""
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT * FROM records WHERE id = ?", (id,))
        row = c.fetchone()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "Record not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/update/<int:id>', methods=['POST'])
def update_record(id):
    """更新记录"""
    try:
        data = request.json
        db = get_db()
        c = db.cursor()

        # 构建更新语句
        c.execute("""UPDATE records SET
                     value = ?, unit = ?, type = ?, notes = ?, timestamp = ?,
                     calories = ?, diet_analysis = ?, is_predicted = ?,
                     distance = ?, duration = ?, heart_rate = ?, pace = ?, cadence = ?,
                     systolic_pressure = ?, diastolic_pressure = ?, pulse_rate = ?
                     WHERE id = ?""",
                  (data.get('value', 0), data.get('unit', 'mmol/L'), data.get('type', ''),
                   data.get('notes', ''), data.get('timestamp', ''),
                   data.get('calories', 0), data.get('diet_analysis', ''),
                   1 if data.get('is_predicted') else 0,
                   data.get('distance'), data.get('duration'), data.get('heart_rate'),
                   data.get('pace'), data.get('cadence'),
                   data.get('systolic_pressure'), data.get('diastolic_pressure'), data.get('pulse_rate'),
                   id))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/export')
def export():
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        # Use pandas to read sql, but use the connection object
        # Warning: pandas read_sql_query might not work well with the 'g' object if it closes too early,
        # but here we are in the request context.
        df = pd.read_sql_query("SELECT * FROM records WHERE user_id = ? ORDER BY timestamp DESC", db, params=(current_user_id,))

        # Use in-memory buffer
        buffer = io.BytesIO()
        df.to_csv(buffer, index=False, encoding='utf-8-sig')
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"glucose_records_{datetime.datetime.now().strftime('%Y%m%d')}.csv",
            mimetype='text/csv'
        )
    except Exception as e:
        return f"Error exporting data: {e}", 500

@app.route('/import', methods=['POST'])
def import_csv():
    """从 CSV 文件导入数据"""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "没有上传文件"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "error", "message": "没有选择文件"}), 400

        if not file.filename.endswith('.csv'):
            return jsonify({"status": "error", "message": "请上传 CSV 文件"}), 400

        # 读取 CSV
        df = pd.read_csv(file, encoding='utf-8-sig')

        # 列名映射（支持不同的列名格式）
        column_mapping = {
            'value': ['value', '血糖值', '数值'],
            'unit': ['unit', '单位'],
            'type': ['type', '类型', '测量类型'],
            'notes': ['notes', '备注', '说明'],
            'timestamp': ['timestamp', '时间', '测量时间', 'datetime'],
            'calories': ['calories', '热量', '卡路里'],
            'diet_analysis': ['diet_analysis', '饮食分析'],
            'is_predicted': ['is_predicted', '预测值'],
            'distance': ['distance', '距离'],
            'duration': ['duration', '时长'],
            'heart_rate': ['heart_rate', '心率'],
            'pace': ['pace', '配速'],
            'cadence': ['cadence', '步频']
        }

        # 标准化列名
        for standard_name, aliases in column_mapping.items():
            for alias in aliases:
                if alias in df.columns and standard_name not in df.columns:
                    df.rename(columns={alias: standard_name}, inplace=True)
                    break

        # 必须有 timestamp 列
        if 'timestamp' not in df.columns:
            return jsonify({"status": "error", "message": "CSV 缺少时间列 (timestamp)"}), 400

        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        imported_count = 0
        skipped_count = 0

        for _, row in df.iterrows():
            try:
                # 跳过 id 列（如果存在），让数据库自动生成
                value = row.get('value', 0)
                if pd.isna(value):
                    value = 0

                timestamp = row.get('timestamp', '')
                if pd.isna(timestamp) or timestamp == '':
                    skipped_count += 1
                    continue

                # 检查是否已存在相同记录（基于时间戳和数值）
                c.execute("SELECT id FROM records WHERE user_id = ? AND timestamp = ? AND value = ?",
                         (current_user_id, str(timestamp), float(value)))
                if c.fetchone():
                    skipped_count += 1
                    continue

                c.execute("""INSERT INTO records
                          (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                           distance, duration, heart_rate, pace, cadence)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (current_user_id,
                           float(value) if not pd.isna(value) else 0,
                           row.get('unit', 'mmol/L') if not pd.isna(row.get('unit')) else 'mmol/L',
                           row.get('type', '') if not pd.isna(row.get('type')) else '',
                           row.get('notes', '') if not pd.isna(row.get('notes')) else '',
                           str(timestamp),
                           int(row.get('calories', 0)) if not pd.isna(row.get('calories')) else 0,
                           row.get('diet_analysis', '') if not pd.isna(row.get('diet_analysis')) else '',
                           1 if row.get('is_predicted') else 0,
                           float(row.get('distance')) if not pd.isna(row.get('distance')) else None,
                           row.get('duration', '') if not pd.isna(row.get('duration')) else None,
                           int(row.get('heart_rate')) if not pd.isna(row.get('heart_rate')) else None,
                           row.get('pace', '') if not pd.isna(row.get('pace')) else None,
                           int(row.get('cadence')) if not pd.isna(row.get('cadence')) else None))
                imported_count += 1
            except Exception as row_error:
                print(f"Import row error: {row_error}")
                skipped_count += 1
                continue

        db.commit()
        return jsonify({
            "status": "success",
            "message": f"成功导入 {imported_count} 条记录，跳过 {skipped_count} 条"
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== Medication Plan Management APIs ==========

@app.route('/add_medication_plan', methods=['POST'])
def add_medication_plan():
    """添加药物方案"""
    try:
        data = request.json
        db = get_db()
        c = db.cursor()

        # Get current user ID
        current_user_id = user_manager.get_current_user_id()

        c.execute("""INSERT INTO medication_plans
                    (user_id, medication_name, dosage, times_per_day, timing_notes, start_date, end_date, is_active, notes, frequency, frequency_detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (current_user_id,
                  data.get('medication_name'),
                  data.get('dosage'),
                  data.get('times_per_day', 1),
                  data.get('timing_notes'),
                  data.get('start_date'),
                  data.get('end_date'),
                  data.get('is_active', 1),
                  data.get('notes', ''),
                  data.get('frequency', 'daily'),
                  data.get('frequency_detail')))

        db.commit()
        return jsonify({"status": "success", "id": c.lastrowid})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/medication_plans', methods=['GET'])
def get_medication_plans():
    """获取所有药物方案"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        c.execute("SELECT * FROM medication_plans WHERE user_id = ? ORDER BY is_active DESC, medication_name ASC", (current_user_id,))
        rows = c.fetchall()
        plans = [dict(row) for row in rows]
        return jsonify(plans)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/medication_plan/<int:plan_id>', methods=['GET'])
def get_medication_plan(plan_id):
    """获取单个药物方案"""
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT * FROM medication_plans WHERE id = ?", (plan_id,))
        row = c.fetchone()

        if row:
            return jsonify(dict(row))
        else:
            return jsonify({"error": "Plan not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/update_medication_plan/<int:plan_id>', methods=['POST'])
def update_medication_plan(plan_id):
    """更新药物方案"""
    try:
        data = request.json
        db = get_db()
        c = db.cursor()

        c.execute("""UPDATE medication_plans SET
                    medication_name = ?,
                    dosage = ?,
                    times_per_day = ?,
                    timing_notes = ?,
                    start_date = ?,
                    end_date = ?,
                    is_active = ?,
                    notes = ?
                    WHERE id = ?""",
                 (data.get('medication_name'),
                  data.get('dosage'),
                  data.get('times_per_day', 1),
                  data.get('timing_notes'),
                  data.get('start_date'),
                  data.get('end_date'),
                  data.get('is_active', 1),
                  data.get('notes', ''),
                  plan_id))

        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/delete_medication_plan/<int:plan_id>', methods=['POST'])
def delete_medication_plan(plan_id):
    """删除药物方案"""
    try:
        db = get_db()
        c = db.cursor()
        c.execute("DELETE FROM medication_plans WHERE id = ?", (plan_id,))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/toggle_medication_plan/<int:plan_id>', methods=['POST'])
def toggle_medication_plan(plan_id):
    """启用/停用药物方案"""
    try:
        db = get_db()
        c = db.cursor()
        c.execute("UPDATE medication_plans SET is_active = NOT is_active WHERE id = ?", (plan_id,))
        db.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== Health Analysis APIs ==========

@app.route('/analyze_health', methods=['POST'])
def analyze_health():
    """手动触发健康分析"""
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        data = request.json or {}
        days = data.get('days', 7)  # 默认7天，支持7/14/30天

        result = generate_health_analysis(db, current_user_id, is_auto=False, days=days)

        if result.get('success'):
            return jsonify({
                "status": "success",
                "analysis_id": result['analysis_id'],
                "result": result['result']
            })
        else:
            # 检查是否是配额错误
            error_type = result.get('error_type', '')
            retry_after = result.get('retry_after', 0)

            response_data = {
                "status": "error",
                "message": result.get('error', '分析失败'),
                "error_type": error_type,
                "retry_after": retry_after
            }

            # 配额错误返回 429 状态码
            status_code = 429 if error_type == 'quota_exceeded' else 500
            return jsonify(response_data), status_code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_latest_analysis', methods=['GET'])
def get_latest_analysis():
    """获取最新的健康分析"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        c.execute("""
            SELECT * FROM health_analyses
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (current_user_id,))
        row = c.fetchone()

        if row:
            analysis = dict(row)
            # 解析 recommendations JSON
            if analysis.get('recommendations'):
                try:
                    analysis['recommendations'] = json.loads(analysis['recommendations'])
                except:
                    pass
            return jsonify(analysis)
        else:
            return jsonify({"message": "暂无分析记录"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health_analyses', methods=['GET'])
def get_health_analyses():
    """获取所有健康分析历史"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        # 可选：分页参数
        limit = request.args.get('limit', 10, type=int)
        offset = request.args.get('offset', 0, type=int)

        c.execute("""
            SELECT * FROM health_analyses
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (current_user_id, limit, offset))
        rows = c.fetchall()

        analyses = []
        for row in rows:
            analysis = dict(row)
            # 解析 recommendations JSON
            if analysis.get('recommendations'):
                try:
                    analysis['recommendations'] = json.loads(analysis['recommendations'])
                except:
                    pass
            analyses.append(analysis)

        return jsonify(analyses)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/prediction_accuracy', methods=['GET'])
def prediction_accuracy():
    """获取预测准确性统计"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        days = request.args.get('days', 30, type=int)

        c.execute("""
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
            AND timestamp > datetime('now', ? || ' days')
        """, (current_user_id, f'-{days}'))

        row = c.fetchone()

        # 安全处理 NULL 值和空结果
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


@app.route('/prediction_status', methods=['GET'])
def prediction_status():
    """
    获取今日预测状态
    返回今日所有预测记录及其验证状态
    """
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')

        # 获取今日所有预测记录
        c.execute("""
            SELECT
                p.id,
                p.type,
                p.value as predicted_value,
                p.timestamp,
                p.notes,
                p.verified_by_real_id,
                p.prediction_error,
                r.value as real_value,
                r.timestamp as real_timestamp
            FROM records p
            LEFT JOIN records r ON p.verified_by_real_id = r.id
            WHERE p.user_id = ?
            AND DATE(p.timestamp) = ?
            AND p.is_predicted = 1
            AND p.value > 0
            ORDER BY p.timestamp ASC
        """, (current_user_id, today_str))

        predictions = []
        for row in c.fetchall():
            pred = {
                'id': row[0],
                'type': row[1],
                'predicted_value': round(row[2], 2),
                'timestamp': row[3],
                'notes': row[4],
                'verified': row[5] is not None,
                'real_value': round(row[7], 2) if row[7] else None,
                'real_timestamp': row[8],
                'error': round(row[6], 2) if row[6] else None
            }

            # 获取该类型的达标标准
            target = settings.get_glucose_target(pred['type'])
            pred['target'] = {
                'min': target['min'],
                'max': target['max'],
                'optimal_max': target['optimal_max']
            }

            predictions.append(pred)

        # 获取近7天预测准确性统计
        c.execute("""
            SELECT
                type,
                COUNT(*) as total,
                AVG(ABS(prediction_error)) as mae,
                SUM(CASE WHEN ABS(prediction_error) < 0.5 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as accuracy
            FROM records
            WHERE user_id = ?
            AND is_predicted = 1
            AND verified_by_real_id IS NOT NULL
            AND timestamp > datetime('now', '-7 days')
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
            'predictions': predictions,
            'accuracy_by_type': accuracy_by_type
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/prediction_comparison', methods=['GET'])
def prediction_comparison():
    """
    获取预测与真实值对比数据（用于对比图）
    """
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        days = request.args.get('days', 7, type=int)
        glucose_type = request.args.get('type', None)

        query = """
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
            AND p.timestamp > datetime('now', ? || ' days')
        """
        params = [current_user_id, f'-{days}']

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


@app.route('/trigger_prediction', methods=['POST'])
def trigger_prediction():
    """
    手动触发预测
    支持触发空腹血糖、运动后餐前血糖、以及剩余时间点预测
    """
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        data = request.json or {}
        prediction_type = data.get('type', 'all')
        target_date = data.get('date', datetime.datetime.now().strftime('%Y-%m-%d'))

        results = []

        if prediction_type in ['all', 'fpg', '空腹']:
            # 触发空腹血糖预测
            predict_morning_fpg(db, current_user_id)
            results.append({'type': '空腹', 'status': 'triggered'})

        if prediction_type in ['all', 'post_exercise', '运动后餐前']:
            # 触发运动后餐前血糖预测
            result = predict_post_exercise_glucose(db, current_user_id, target_date)
            if result:
                results.append({'type': '运动后餐前', 'status': 'success', 'value': result})
            else:
                results.append({'type': '运动后餐前', 'status': 'skipped', 'reason': '条件不满足或已存在'})

        if prediction_type in ['all', 'remaining', '剩余时间点']:
            # 触发剩余时间点预测（基于实测数据向后预测）
            remaining_results = predict_remaining_glucose_slots(db, current_user_id, target_date)
            if remaining_results:
                for pred in remaining_results:
                    results.append({
                        'type': pred['type'],
                        'status': 'success',
                        'value': pred['value'],
                        'base': f"{pred['base_type']}({pred['base_glucose']})"
                    })
            else:
                results.append({'type': '剩余时间点', 'status': 'skipped', 'reason': '无实测数据或已有预测'})

        return jsonify({
            'status': 'success',
            'results': results
        })

    except Exception as e:
        error_str = str(e)
        traceback.print_exc()

        # 特殊处理 429 配额错误
        if '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str:
            import re
            retry_match = re.search(r'retry in (\d+)', error_str.lower())
            retry_seconds = int(retry_match.group(1)) if retry_match else 60

            return jsonify({
                "status": "error",
                "message": f"AI 服务配额已用尽，请等待 {retry_seconds} 秒后重试",
                "error_type": "quota_exceeded",
                "retry_after": retry_seconds
            }), 429

        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/find_duplicates', methods=['GET'])
def find_duplicates():
    """查找重复的记录"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        # 查找在相同时间（精确到分钟）、相同类型、相同数值的重复记录
        c.execute("""
            SELECT
                strftime('%Y-%m-%d %H:%M', timestamp) as time_key,
                type,
                value,
                COUNT(*) as count,
                GROUP_CONCAT(id) as ids
            FROM records
            WHERE user_id = ?
            GROUP BY time_key, type, value
            HAVING count > 1
            ORDER BY timestamp DESC
        """, (current_user_id,))

        duplicates = []
        for row in c.fetchall():
            time_key, record_type, value, count, ids = row
            id_list = [int(id) for id in ids.split(',')]

            duplicates.append({
                'time': time_key,
                'type': record_type,
                'value': value,
                'count': count,
                'ids': id_list
            })

        return jsonify({
            'status': 'success',
            'duplicates': duplicates,
            'total_groups': len(duplicates)
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/delete_duplicates', methods=['POST'])
def delete_duplicates():
    """删除重复的记录，保留每组中的第一条"""
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        # 查找重复记录组
        c.execute("""
            SELECT
                strftime('%Y-%m-%d %H:%M', timestamp) as time_key,
                type,
                value,
                GROUP_CONCAT(id ORDER BY id) as ids
            FROM records
            WHERE user_id = ?
            GROUP BY time_key, type, value
            HAVING COUNT(*) > 1
        """, (current_user_id,))

        deleted_count = 0
        for row in c.fetchall():
            time_key, record_type, value, ids = row
            id_list = [int(id) for id in ids.split(',')]

            # 保留第一条，删除其他的
            ids_to_delete = id_list[1:]
            for id_to_delete in ids_to_delete:
                c.execute("DELETE FROM records WHERE id = ?", (id_to_delete,))
                deleted_count += 1

        db.commit()

        return jsonify({
            'status': 'success',
            'deleted_count': deleted_count,
            'message': f'已删除 {deleted_count} 条重复记录'
        })
    except Exception as e:
        traceback.print_exc()
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

# ========== User Management APIs ==========

@app.route('/switch_user/<int:user_id>', methods=['POST'])
def switch_user(user_id):
    """切换当前用户"""
    try:
        user = user_manager.get_user(user_id)
        if not user:
            return jsonify({"status": "error", "message": "用户不存在"}), 404

        user_manager.set_current_user(user_id)
        return jsonify({"status": "success", "user": user})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/get_users', methods=['GET'])
def get_users():
    """获取所有用户列表"""
    try:
        users = user_manager.get_all_users()
        return jsonify(users)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/get_current_user', methods=['GET'])
def get_current_user_api():
    """获取当前用户"""
    try:
        user_id = user_manager.get_current_user_id()
        user = user_manager.get_user(user_id)
        if not user:
            # 如果没有找到用户，返回默认用户
            user = {
                'id': 1,
                'username': 'default',
                'display_name': '默认用户',
                'enabled_modules': []
            }
        return jsonify(user)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5001)