from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, g
import sqlite3
import pandas as pd
import datetime
import os
import io
import traceback
from werkzeug.utils import secure_filename
from parser import parse_glucose_input
import settings
import google.generativeai as genai
import json
import re

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "glucose.db")
AVATAR_FOLDER = os.path.join(BASE_DIR, "static", "avatars")
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

app.config['UPLOAD_FOLDER'] = AVATAR_FOLDER
# Ensure avatar folder exists
os.makedirs(AVATAR_FOLDER, exist_ok=True)

# Configure Gemini API for FPG prediction
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

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

        conn.commit()
    except Exception as e:
        print(f"DB Init Error: {e}")
    finally:
        if conn:
            conn.close()

init_db()

def predict_morning_fpg(db):
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
        c = db.cursor()
        c.execute("""
            SELECT id FROM records
            WHERE DATE(timestamp) = ?
            AND type LIKE '%空腹%'
            AND is_predicted = 1
        """, (today_str,))
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
            WHERE DATE(timestamp) = ?
            AND value > 0
            AND is_predicted = 0
            ORDER BY timestamp ASC
        """, (yesterday_str,))
        yesterday_glucose = c.fetchall()

        # 4.2 前一天的饮食与运动卡路里
        c.execute("""
            SELECT type, calories FROM records
            WHERE DATE(timestamp) = ?
            AND calories > 0
        """, (yesterday_str,))
        yesterday_calories = c.fetchall()

        cal_in = sum(row[1] for row in yesterday_calories if row[0] not in ['跑步', '运动'])
        cal_out_exercise = sum(row[1] for row in yesterday_calories if row[0] in ['跑步', '运动'])
        user_bmr = settings.calculate_bmr()
        net_calories = cal_in - (user_bmr + cal_out_exercise)

        # 4.3 近 7 天空腹血糖趋势（真实数据）
        c.execute("""
            SELECT value, timestamp FROM records
            WHERE type LIKE '%空腹%'
            AND value > 0
            AND is_predicted = 0
            AND timestamp > datetime('now', '-7 days')
            ORDER BY timestamp DESC
        """)
        recent_fpg = c.fetchall()

        # 4.4 当前用药情况
        c.execute("""
            SELECT medication_name, dosage, times_per_day, timing_notes
            FROM medication_plans
            WHERE is_active = 1
        """)
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
        model = genai.GenerativeModel('gemini-3-flash-preview')
        response = model.generate_content(prompt)
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
            (value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (predicted_value, 'mmol/L', '空腹', f'AI预测: {reasoning}',
              prediction_timestamp, 0, '', 1))
        db.commit()

        print(f"✓ FPG Prediction generated: {predicted_value} mmol/L for {today_str} 07:15")

    except Exception as e:
        print(f"ERROR in predict_morning_fpg: {e}")
        traceback.print_exc()


@app.route('/')
def index():
    try:
        db = get_db()
        c = db.cursor()

        # 自动生成早晨空腹血糖预测（如果符合条件）
        predict_morning_fpg(db)

        # 获取分页参数，默认显示最近 14 天
        days = request.args.get('days', 14, type=int)
        page = request.args.get('page', 1, type=int)
        show_all = request.args.get('all', False, type=bool)

        # 1. Fetch records with pagination
        if show_all:
            c.execute("SELECT * FROM records ORDER BY timestamp ASC")
        else:
            c.execute("""SELECT * FROM records
                        WHERE timestamp > datetime('now', ?)
                        ORDER BY timestamp ASC""", (f'-{days} days',))
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
            if r.get('calories') and r['calories'] > 0:
                # 判断是摄入还是消耗：运动类型是消耗，其他是摄入
                if r['type'] in ['跑步', '运动'] or r.get('distance'):
                    day_group['stats']['cal_out_exercise'] += r['calories']
                else:
                    day_group['stats']['cal_in'] += r['calories']

            if r['value'] > 0:
                day_group['stats']['avg_glucose'] += r['value']
                day_group['stats']['glucose_count'] += 1

        # 4. Fetch active medication plans
        c.execute("""SELECT * FROM medication_plans
                    WHERE is_active = 1
                    ORDER BY medication_name ASC""")
        med_plan_rows = c.fetchall()
        medication_plans = [dict(row) for row in med_plan_rows]

        # 为每个日期添加当天应服用的药物方案
        for date_str in grouped_records.keys():
            for plan in medication_plans:
                # 检查日期是否在方案有效期内
                plan_start = plan['start_date']
                plan_end = plan['end_date'] if plan['end_date'] else '9999-12-31'

                if plan_start <= date_str <= plan_end:
                    grouped_records[date_str]['medication_plans'].append(plan)

        # Post-process stats: BMR correction for today & Net Calories
        now = datetime.datetime.now()
        today_str = now.strftime('%Y-%m-%d')

        sorted_dates = []
        for date_str, data in grouped_records.items():
            stats = data['stats']
            
            # 1. BMR Correction for Today
            # 如果是今天，BMR 应该按当前时间比例计算
            if date_str == today_str:
                current_minutes = now.hour * 60 + now.minute
                total_minutes = 24 * 60
                stats['cal_out_bmr'] = int(USER_BMR * (current_minutes / total_minutes))
            
            # 2. Glucose Average
            if stats['glucose_count'] > 0:
                stats['avg_glucose'] = round(stats['avg_glucose'] / stats['glucose_count'], 1)

            # 3. Net Calories Calculation
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
        # 近7天平均早空腹血糖
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE (type LIKE '%空腹%' OR type LIKE '%早空腹%')
            AND value > 0
            AND timestamp > datetime('now', '-7 days')
        """)
        avg_fasting_7d = c.fetchone()[0]

        # 近7天平均餐后2小时血糖
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE type LIKE '%餐后2小时%'
            AND value > 0
            AND timestamp > datetime('now', '-7 days')
        """)
        avg_post2h_7d = c.fetchone()[0]

        # 近7日最高血糖
        c.execute("""
            SELECT MAX(value) FROM records
            WHERE value > 0
            AND timestamp > datetime('now', '-7 days')
        """)
        max_glucose_7d = c.fetchone()[0]

        # 近7日最低血糖
        c.execute("""
            SELECT MIN(value) FROM records
            WHERE value > 0
            AND timestamp > datetime('now', '-7 days')
        """)
        min_glucose_7d = c.fetchone()[0]

        # === 运动统计 ===
        # 最近7天跑步总里程数
        c.execute("""
            SELECT SUM(distance) FROM records
            WHERE distance IS NOT NULL
            AND timestamp > datetime('now', '-7 days')
        """)
        total_distance_7d = c.fetchone()[0]

        # 运动总消耗卡路里数（7天）
        c.execute("""
            SELECT SUM(calories) FROM records
            WHERE (type = '跑步' OR type = '运动' OR distance IS NOT NULL)
            AND calories > 0
            AND timestamp > datetime('now', '-7 days')
        """)
        total_exercise_cal_7d = c.fetchone()[0]

        # 最近7天跑步平均心率
        c.execute("""
            SELECT AVG(heart_rate) FROM records
            WHERE heart_rate IS NOT NULL
            AND (type = '跑步' OR type = '运动')
            AND timestamp > datetime('now', '-7 days')
        """)
        avg_heart_rate_7d = c.fetchone()[0]

        # === 血压统计 ===
        # 最近7天平均血压
        c.execute("""
            SELECT AVG(systolic_pressure), AVG(diastolic_pressure), COUNT(*)
            FROM records
            WHERE systolic_pressure IS NOT NULL
            AND diastolic_pressure IS NOT NULL
            AND timestamp > datetime('now', '-7 days')
        """)
        bp_7d_stats = c.fetchone()

        # 血压最高的一天（7天内）
        c.execute("""
            SELECT MAX(systolic_pressure), MAX(diastolic_pressure),
                   DATE(timestamp) as day
            FROM records
            WHERE systolic_pressure IS NOT NULL
            AND timestamp > datetime('now', '-7 days')
            GROUP BY day
            ORDER BY systolic_pressure DESC
            LIMIT 1
        """)
        bp_max_day = c.fetchone()

        # 血压最低的一天（7天内）
        c.execute("""
            SELECT MIN(systolic_pressure), MIN(diastolic_pressure),
                   DATE(timestamp) as day
            FROM records
            WHERE systolic_pressure IS NOT NULL
            AND timestamp > datetime('now', '-7 days')
            GROUP BY day
            ORDER BY systolic_pressure ASC
            LIMIT 1
        """)
        bp_min_day = c.fetchone()

        # === 用药情况 ===
        # 当前服用的药物清单（is_active=1）
        c.execute("""
            SELECT medication_name, dosage, times_per_day, timing_notes
            FROM medication_plans
            WHERE is_active = 1
            ORDER BY medication_name ASC
        """)
        active_medications = [dict(zip(['name', 'dosage', 'times', 'timing'], row))
                             for row in c.fetchall()]

        # Calculate Compliance Rate (7 days)
        # Using strict criteria from settings
        user_config = settings.load_config()
        targets = user_config.get('target', {
            'fasting_min': 3.9, 'fasting_max': 7.0,
            'postmeal_max': 7.8, 'premeal_max': 6.5
        })

        c.execute("SELECT * FROM records WHERE timestamp > datetime('now', '-7 days')")
        recent_rows = c.fetchall()
        total_recent = len(recent_rows)
        ok_count = 0
        for row in recent_rows:
            val = row['value']
            t = row['type']
            is_ok = False
            if '空腹' in t:
                if targets['fasting_min'] <= val <= targets['fasting_max']: is_ok = True
            elif '餐后' in t:
                if targets['fasting_min'] <= val <= targets['postmeal_max']: is_ok = True
            elif '餐前' in t or '运动' in t:
                if targets['fasting_min'] <= val <= targets['premeal_max']: is_ok = True
            else:
                # Default fallback
                if targets['fasting_min'] <= val <= targets['postmeal_max']: is_ok = True

            if is_ok: ok_count += 1

        compliance = int((ok_count / total_recent * 100)) if total_recent > 0 else 0

        # 获取总记录数用于分页显示
        c.execute("SELECT COUNT(*) FROM records")
        total_records = c.fetchone()[0]

        stats = {
            'total_records': total_records,
            'current_days': days,
            'show_all': show_all,
            'today_str': today_str,
            'user': settings.load_config(),
            'compliance': compliance,

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
            'active_medications': active_medications
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
        c.execute("""INSERT INTO records
                     (value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                      distance, duration, heart_rate, systolic_pressure, diastolic_pressure, pulse_rate)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (value, unit, r_type, notes, timestamp, calories, diet_analysis, is_predicted,
                   distance, duration, heart_rate, systolic_pressure, diastolic_pressure, pulse_rate))
        db.commit()

        if request.is_json:
            return jsonify({"status": "success"})
        return redirect(url_for('index'))
    except Exception as e:
        return f"Error adding record: {e}", 500

def get_user_stats(db):
    stats = {}
    try:
        c = db.cursor()
        # 1. Avg Fasting (Last 30 days)
        c.execute("""
            SELECT AVG(value) FROM records 
            WHERE type LIKE '%空腹%' 
            AND is_predicted = 0
            AND timestamp > datetime('now', '-30 days')
        """)
        row = c.fetchone()
        stats['avg_fasting'] = round(row[0], 1) if row and row[0] else '未知'

        # 2. Avg Post-meal (Last 30 days)
        c.execute("""
            SELECT AVG(value) FROM records 
            WHERE type LIKE '%餐后%' 
            AND is_predicted = 0
            AND timestamp > datetime('now', '-30 days')
        """)
        row = c.fetchone()
        stats['avg_postmeal'] = round(row[0], 1) if row and row[0] else '未知'

        # 3. Last record
        c.execute("SELECT value, type FROM records WHERE is_predicted = 0 ORDER BY timestamp DESC LIMIT 1")
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
        image_b64 = data.get('image') # Base64 string
        mime_type = data.get('mime_type', 'image/jpeg')
        
        image_data = None
        if image_b64:
            import base64
            image_data = base64.b64decode(image_b64.split(',')[-1])
        
        # Get history context for better prediction
        db = get_db()
        history_context = get_user_stats(db)
        
        results = parse_glucose_input(text, history_context, image_data, mime_type)
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
                # 提取日期和时间（精确到分钟）
                datetime_str = timestamp.rsplit(':', 1)[0] if ':' in timestamp else timestamp  # 去掉秒

                c.execute("""DELETE FROM records
                           WHERE DATE(timestamp) = DATE(?)
                           AND type = ?
                           AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?)
                           AND is_predicted = 1""",
                         (timestamp, record_type, timestamp))

            c.execute("""INSERT INTO records
                      (value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                       distance, duration, heart_rate, pace, cadence,
                       systolic_pressure, diastolic_pressure, pulse_rate)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (r['value'], r.get('unit', 'mmol/L'), r['type'], r.get('notes', ''),
                       r.get('datetime'), cal, da, is_pred,
                       r.get('distance'), r.get('duration'), r.get('heart_rate'),
                       r.get('pace'), r.get('cadence'),
                       r.get('systolic_pressure'), r.get('diastolic_pressure'), r.get('pulse_rate')))
        db.commit()
        return jsonify({"status": "success"})
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
        # Use pandas to read sql, but use the connection object
        # Warning: pandas read_sql_query might not work well with the 'g' object if it closes too early,
        # but here we are in the request context.
        df = pd.read_sql_query("SELECT * FROM records ORDER BY timestamp DESC", db)

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
                c.execute("SELECT id FROM records WHERE timestamp = ? AND value = ?",
                         (str(timestamp), float(value)))
                if c.fetchone():
                    skipped_count += 1
                    continue

                c.execute("""INSERT INTO records
                          (value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                           distance, duration, heart_rate, pace, cadence)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (float(value) if not pd.isna(value) else 0,
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

        c.execute("""INSERT INTO medication_plans
                    (medication_name, dosage, times_per_day, timing_notes, start_date, end_date, is_active, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                 (data.get('medication_name'),
                  data.get('dosage'),
                  data.get('times_per_day', 1),
                  data.get('timing_notes'),
                  data.get('start_date'),
                  data.get('end_date'),
                  data.get('is_active', 1),
                  data.get('notes', '')))

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
        c.execute("SELECT * FROM medication_plans ORDER BY is_active DESC, medication_name ASC")
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

if __name__ == '__main__':
    app.run(debug=True, port=5001)