from flask import Blueprint, request, jsonify
import traceback
import datetime
import json

import settings
from user_manager import UserManager
from core.config import DB_NAME
from utils.responses import api_success, api_error
from utils.auth import login_required
from utils.db import get_db
from services import generate_health_analysis

user_manager = UserManager(DB_NAME)
bp_health = Blueprint('health', __name__)

@bp_health.route('/analyze_health', methods=['POST'])
@login_required
def analyze_health():
    try:
        db = get_db()
        current_user_id = user_manager.get_current_user_id()
        data = request.json or {}
        days = data.get('days', 7)

        result = generate_health_analysis(db, current_user_id, is_auto=False, days=days)

        if result.get('success'):
            return api_success(data={"analysis_id": result['analysis_id'], "result": result['result']})
        else:
            return api_error(result.get('error', '分析失败'), status_code=500)
    except Exception as e:
        traceback.print_exc()
        return api_error(str(e), status_code=500)

@bp_health.route('/get_latest_analysis', methods=['GET'])
@login_required
def get_latest_analysis():
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        c.execute("SELECT * FROM health_analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (current_user_id,))
        row = c.fetchone()
        if row:
            analysis = dict(row)
            if analysis.get('recommendations'):
                try: analysis['recommendations'] = json.loads(analysis['recommendations'])
                except: pass
            return jsonify(analysis)
        return jsonify({"message": "暂无分析记录"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp_health.route('/health_analyses', methods=['GET'])
@login_required
def get_health_analyses():
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = user_manager.get_current_user_id()
        limit = request.args.get('limit', 10, type=int)
        offset = request.args.get('offset', 0, type=int)
        c.execute("SELECT * FROM health_analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (current_user_id, limit, offset))
        analyses = [dict(row) for row in c.fetchall()]
        return jsonify(analyses)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
