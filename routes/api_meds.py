from flask import Blueprint, request, jsonify
import traceback

from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.auth import login_required
from utils.db import get_db

user_manager = UserManager(DB_NAME)
bp_meds = Blueprint('meds', __name__)

@bp_meds.route('/add_medication_plan', methods=['POST'])
@login_required
def add_medication_plan():
    try:
        data = request.json
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()

        c.execute("""INSERT INTO medication_plans
                    (user_id, medication_name, dosage, times_per_day, timing_notes, start_date, end_date, is_active, notes, frequency, frequency_detail, category, dose_quantity, dose_unit, med_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                  data.get('frequency_detail'),
                  data.get('category', 'long_term'),
                  data.get('dose_quantity', '1'),
                  data.get('dose_unit', '片'),
                  data.get('med_type', '')))

        db.commit()
        return api_success(data={"id": c.lastrowid}, message="Medication plan added successfully")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500, error_type="medication_error")

@bp_meds.route('/medication_plans', methods=['GET'])
@login_required
def get_medication_plans():
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        c.execute("SELECT * FROM medication_plans WHERE user_id = ? ORDER BY is_active DESC, medication_name ASC", (current_user_id,))
        rows = c.fetchall()
        plans = [dict(row) for row in rows]
        for plan in plans:
            c.execute("SELECT old_dosage, new_dosage, changed_at FROM dosage_history WHERE plan_id = ? ORDER BY changed_at DESC", (plan['id'],))
            plan['dosage_history'] = [dict(r) for r in c.fetchall()]
        return jsonify(plans)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_meds.route('/medication_plan/<int:plan_id>', methods=['GET'])
@login_required
def get_medication_plan(plan_id):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("SELECT * FROM medication_plans WHERE id = ?", (plan_id,))
        row = c.fetchone()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "Plan not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_meds.route('/update_medication_plan/<int:plan_id>', methods=['POST'])
@login_required
def update_medication_plan(plan_id):
    try:
        data = request.json
        db = get_db()
        c = db.cursor()

        # 记录剂量变更历史
        new_dosage = data.get('dosage')
        new_dq = data.get('dose_quantity', '1')
        new_du = data.get('dose_unit', '片')
        c.execute("SELECT dosage, dose_quantity, dose_unit FROM medication_plans WHERE id = ?", (plan_id,))
        old = c.fetchone()
        if old:
            old_dosage = old['dosage']
            old_dq = old['dose_quantity'] or '1'
            old_du = old['dose_unit'] or '片'
            if old_dosage != new_dosage or old_dq != new_dq or old_du != new_du:
                old_str = f"{old_dosage} x{old_dq}{old_du}" if str(old_dq) != '1' else (old_dosage or '')
                new_str = f"{new_dosage} x{new_dq}{new_du}" if str(new_dq) != '1' else (new_dosage or '')
                c.execute("INSERT INTO dosage_history (plan_id, old_dosage, new_dosage) VALUES (?, ?, ?)",
                          (plan_id, old_str, new_str))

        c.execute("""UPDATE medication_plans SET
                    medication_name = ?, dosage = ?, times_per_day = ?, timing_notes = ?,
                    start_date = ?, end_date = ?, is_active = ?, notes = ?,
                    frequency = ?, frequency_detail = ?, category = ?, dose_quantity = ?,
                    dose_unit = ?, med_type = ?
                    WHERE id = ?""",
                 (data.get('medication_name'), data.get('dosage'), data.get('times_per_day', 1),
                  data.get('timing_notes'), data.get('start_date'), data.get('end_date'),
                  data.get('is_active', 1), data.get('notes', ''), data.get('frequency', 'daily'),
                  data.get('frequency_detail'), data.get('category', 'long_term'),
                  data.get('dose_quantity', '1'), data.get('dose_unit', '片'),
                  data.get('med_type', ''), plan_id))

        db.commit()
        return api_success(message="Medication plan updated successfully")
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500, error_type="medication_error")

@bp_meds.route('/delete_medication_plan/<int:plan_id>', methods=['POST'])
@login_required
def delete_medication_plan(plan_id):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("DELETE FROM medication_logs WHERE plan_id = ?", (plan_id,))
        c.execute("DELETE FROM dosage_history WHERE plan_id = ?", (plan_id,))
        c.execute("DELETE FROM medication_plans WHERE id = ?", (plan_id,))
        db.commit()
        return api_success(message="Medication plan deleted successfully")
    except Exception as e:
        return api_error(str(e), status_code=500, error_type="medication_error")

@bp_meds.route('/toggle_medication_plan/<int:plan_id>', methods=['POST'])
@login_required
def toggle_medication_plan(plan_id):
    try:
        db = get_db()
        c = db.cursor()
        c.execute("UPDATE medication_plans SET is_active = NOT is_active WHERE id = ?", (plan_id,))
        db.commit()
        return api_success(message="Medication plan toggled successfully")
    except Exception as e:
        return api_error(str(e), status_code=500, error_type="medication_error")
