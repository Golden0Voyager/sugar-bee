from flask import Blueprint, request, jsonify, redirect, url_for, send_file
import datetime
import io
import traceback
import pandas as pd
import base64

import settings
from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.auth import login_required
from utils.db import get_db
from glucose_parser import parse_glucose_input
from services import (
    link_prediction_to_real_record
)

user_manager = UserManager(DB_NAME)
bp_records = Blueprint('records', __name__)

def get_user_stats(db, user_id=1):
    stats = {}
    try:
        c = db.cursor()
        # 1. Avg Fasting (Last 30 days)
        c.execute("""
            SELECT AVG(value) FROM records
            WHERE user_id = ?
            AND type LIKE '%空腹%'
            AND type NOT LIKE '%血压%'
            AND systolic_pressure IS NULL
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
            AND type NOT LIKE '%血压%'
            AND systolic_pressure IS NULL
            AND is_predicted = 0
            AND timestamp > datetime('now', '-30 days')
        """, (user_id,))
        row = c.fetchone()
        stats['avg_postmeal'] = round(row[0], 1) if row and row[0] else '未知'

        # 3. Last record
        c.execute("""SELECT value, type FROM records
            WHERE user_id = ? AND is_predicted = 0
            AND value > 0 AND systolic_pressure IS NULL
            AND type NOT LIKE '%血压%'
            AND type NOT IN ('跑步', '运动', '体重记录')
            ORDER BY timestamp DESC LIMIT 1""", (user_id,))
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

@bp_records.route('/add', methods=['POST'])
@login_required
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
            spo2 = data.get('spo2')
            carbs_grams = data.get('carbs_grams')
            gi_value = data.get('gi_value')
            weight = data.get('weight')
            bmi = data.get('bmi')
            vo2max = data.get('vo2max')
            max_heart_rate = data.get('max_heart_rate')
            steps = data.get('steps')
            pace = data.get('pace')
            max_pace = data.get('max_pace')
            cadence = data.get('cadence')
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
            spo2 = request.form.get('spo2')
            carbs_grams = request.form.get('carbs_grams')
            gi_value = request.form.get('gi_value')
            weight = request.form.get('weight')
            bmi = request.form.get('bmi')
            vo2max = request.form.get('vo2max')
            max_heart_rate = request.form.get('max_heart_rate')
            steps = request.form.get('steps')
            pace = request.form.get('pace')
            max_pace = request.form.get('max_pace')
            cadence = request.form.get('cadence')

        # Auto-calculate BMI if weight is provided but BMI is not
        if weight and not bmi:
            try:
                bmi = settings.calculate_bmi(float(weight))
            except (ValueError, TypeError):
                pass

        # If weight record, update user config weight
        if weight:
            try:
                config = settings.load_config()
                config['weight'] = float(weight)
                settings.save_config(config)
            except (ValueError, TypeError):
                pass

        # Handle empty timestamp (default to now)
        if not timestamp:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if 'T' in timestamp:
            timestamp = timestamp.replace('T', ' ')
            if len(timestamp) == 16: # Missing seconds
                timestamp += ':00'

        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        c.execute("""INSERT INTO records
                     (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                      distance, duration, heart_rate, systolic_pressure, diastolic_pressure, pulse_rate,
                      carbs_grams, gi_value, weight, bmi, spo2, vo2max, max_heart_rate, steps,
                      pace, max_pace, cadence)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (current_user_id, value, unit, r_type, notes, timestamp, calories, diet_analysis, is_predicted,
                   distance, duration, heart_rate, systolic_pressure, diastolic_pressure, pulse_rate,
                   carbs_grams, gi_value, weight, bmi, spo2, vo2max, max_heart_rate, steps,
                   pace, max_pace, cadence))

        real_record_id = c.lastrowid
        try:
            numeric_value = float(value) if value else 0
            if not is_predicted and numeric_value > 0 and r_type:
                timestamp_str = str(timestamp) if timestamp else ""
                record_date = timestamp_str[:10]  # Take YYYY-MM-DD
                link_prediction_to_real_record(db, real_record_id, current_user_id, record_date, r_type, numeric_value, timestamp)
        except (ValueError, TypeError) as e:
            print(f"Warning: Could not link prediction for record {real_record_id}: {e}")

        db.commit()
        if request.is_json:
            return api_success(data={"id": real_record_id}, message="Record added successfully")
        return redirect(url_for('index'))
    except Exception as e:
        if request.is_json:
            return api_error(str(e), status_code=500, error_type="add_record_error")
        return f"Error adding record: {e}", 500

@bp_records.route('/parse_ai', methods=['POST'])
@login_required
def parse_ai():
    try:
        data = request.json
        text = data.get('text', '')
        images_b64 = data.get('images', [])
        mime_type = data.get('mime_type', 'image/jpeg')

        images_data = []
        if images_b64:
            for img_b64 in images_b64:
                image_data = base64.b64decode(img_b64.split(',')[-1])
                images_data.append(image_data)

        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        history_context = get_user_stats(db, current_user_id)
        results = parse_glucose_input(text, history_context, images_data, mime_type)

        try:
            c = db.cursor()
            for record in results:
                if record.get('value') and record.get('value') > 0 and record.get('datetime'):
                    timestamp = record.get('datetime')
                    c.execute("""
                        SELECT value, notes FROM records
                        WHERE user_id = ?
                        AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?)
                        AND is_predicted = 1
                        AND value > 0
                        AND systolic_pressure IS NULL
                        ORDER BY created_at DESC LIMIT 1
                    """, (current_user_id, timestamp))
                    existing_prediction = c.fetchone()
                    if existing_prediction:
                        record['predicted_value'] = existing_prediction[0]
                        record['prediction_source'] = 'database'
                    else:
                        record['prediction_source'] = 'ai_realtime'
        except Exception as e:
            print(f"Error matching predictions: {e}")

        return jsonify(results)
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500, error_type="parse_ai_error")

@bp_records.route('/batch_add', methods=['POST'])
@login_required
def batch_add():
    try:
        data = request.json.get('records')
        conflict_resolution = request.json.get('conflict_resolution', 'ask')
        if not data:
            return api_error("No data provided")

        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        # Phase 1: Conflict Detection
        conflicts = []
        for idx, r in enumerate(data):
            if 'value' not in r or 'type' not in r:
                continue
            is_pred = r.get('is_predicted', False)
            timestamp = r.get('datetime')
            record_value = r.get('value', 0)
            if timestamp and not is_pred and record_value > 0:
                c.execute("""SELECT id, value, type, timestamp, notes FROM records
                           WHERE user_id = ? AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?)
                           AND is_predicted = 0 AND value > 0 AND systolic_pressure IS NULL""",
                         (current_user_id, timestamp))
                existing = c.fetchone()
                if existing:
                    conflicts.append({'index': idx, 'new_record': r, 'existing_record': dict(existing)})

        if conflicts and conflict_resolution == 'ask':
            return jsonify({'status': 'conflict', 'message': f'发现 {len(conflicts)} 条冲突', 'conflicts': conflicts, 'total_records': len(data)})

        # Phase 2: Insert
        inserted_records = []
        for r in data:
            if 'value' not in r or 'type' not in r:
                continue
            is_pred = 1 if r.get('is_predicted', False) else 0
            timestamp = r.get('datetime')
            r_type = r['type']
            
            if timestamp:
                if is_pred:
                    c.execute("DELETE FROM records WHERE user_id = ? AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?) AND type = ? AND is_predicted = 1", (current_user_id, timestamp, r_type))
                elif conflict_resolution == 'overwrite':
                    c.execute("DELETE FROM records WHERE user_id = ? AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?) AND is_predicted = 0", (current_user_id, timestamp))
                elif conflict_resolution == 'skip':
                    continue

            # Cleanup pressure/weight
            systolic = r.get('systolic_pressure') or None
            diastolic = r.get('diastolic_pressure') or None
            weight = r.get('weight')
            bmi = r.get('bmi')
            if weight and not bmi:
                try:
                    bmi = settings.calculate_bmi(float(weight))
                except Exception:
                    pass

            c.execute("""INSERT INTO records (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
                                            distance, duration, heart_rate, max_heart_rate, systolic_pressure, diastolic_pressure,
                                            pulse_rate, weight, bmi, medication_name, steps, pace, max_pace, cadence, vo2max,
                                            spo2, carbs_grams, gi_value)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (current_user_id, r['value'], r.get('unit', 'mmol/L'), r_type, r.get('notes', ''), timestamp,
                       r.get('calories', 0), r.get('diet_analysis', ''), is_pred, r.get('distance'), r.get('duration'),
                       r.get('heart_rate'), r.get('max_heart_rate'), systolic, diastolic,
                       r.get('pulse_rate'), weight, bmi, r.get('medication_name'), r.get('steps'),
                       r.get('pace'), r.get('max_pace'), r.get('cadence'), r.get('vo2max'),
                       r.get('spo2'), r.get('carbs_grams'), r.get('gi_value')))
            inserted_records.append({'id': c.lastrowid, 'is_pred': is_pred, 'value': r['value'], 'datetime': timestamp, 'type': r_type})

        db.commit()
        return api_success(message="Batch add successful")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)

@bp_records.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        c.execute("DELETE FROM records WHERE id = ? AND user_id = ?", (id, current_user_id))
        db.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return api_success(message="Record deleted")
        return redirect(url_for('index'))
    except Exception as e:
        return api_error(str(e), status_code=500)

@bp_records.route('/record/<int:id>')
@login_required
def get_record(id):
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        c.execute("SELECT * FROM records WHERE id = ? AND user_id = ?", (id, current_user_id))
        row = c.fetchone()
        return jsonify(dict(row)) if row else (jsonify({"error": "Not found"}), 404)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_records.route('/update/<int:id>', methods=['POST'])
@login_required
def update_record(id):
    try:
        data = request.json
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        c = db.cursor()
        c.execute("""UPDATE records SET value=?, unit=?, type=?, notes=?, timestamp=?, calories=?, diet_analysis=?
                     WHERE id=? AND user_id=?""",
                  (data.get('value'), data.get('unit'), data.get('type'), data.get('notes'), data.get('timestamp'),
                   data.get('calories'), data.get('diet_analysis'), id, current_user_id))
        db.commit()
        return api_success(message="Updated")
    except Exception as e:
        return api_error(str(e), status_code=500)

@bp_records.route('/export')
@login_required
def export():
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        df = pd.read_sql_query("SELECT * FROM records WHERE user_id = ? ORDER BY timestamp DESC", db, params=(current_user_id,))
        buffer = io.BytesIO()
        df.to_csv(buffer, index=False, encoding='utf-8-sig')
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"glucose_records_{datetime.datetime.now().strftime('%Y%m%d')}.csv", mimetype='text/csv')
    except Exception as e:
        return f"Error: {e}", 500

@bp_records.route('/import', methods=['POST'])
@login_required
def import_csv():
    try:
        file = request.files.get('file')
        if not file:
            return api_error("No file")
        df = pd.read_csv(file, encoding='utf-8-sig') if file.filename.endswith('.csv') else pd.read_excel(file)
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        # Simplified import logic for brevity, keeping core functionality
        for _, row in df.iterrows():
            c.execute("INSERT INTO records (user_id, value, type, timestamp) VALUES (?, ?, ?, ?)",
                     (current_user_id, row.get('value'), row.get('type'), row.get('timestamp')))
        db.commit()
        return api_success(message="Imported")
    except Exception as e:
        return api_error(str(e), status_code=500)


@bp_records.route('/preview_import', methods=['POST'])
@login_required
def preview_import():
    """预览导入文件内容，返回前几行数据和列名"""
    try:
        if 'file' not in request.files:
            return api_error("没有上传文件", error_type="upload_error")

        file = request.files['file']
        if file.filename == '':
            return api_error("没有选择文件", error_type="upload_error")

        if file.filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file, nrows=10)
        else:
            df = pd.read_csv(file, encoding='utf-8-sig', nrows=10)

        return api_success(data={
            "columns": list(df.columns),
            "rows": df.fillna('').to_dict('records'),
            "total_preview": len(df)
        })
    except Exception as e:
        return api_error(str(e), status_code=500, error_type="preview_error")
