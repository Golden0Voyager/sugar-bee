import contextlib
import json
import traceback

from flask import Blueprint, jsonify, request

from core.config import DB_NAME
from services import generate_health_analysis
from user_manager import UserManager
from utils.auth import login_required
from utils.db import get_db
from utils.responses import api_error, api_success

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

        if result.get('skipped'):
            return api_success(message=result.get('message', '今日已生成分析'))
        if result.get('success'):
            return api_success(data={"analysis_id": result['analysis_id'], "result": result['result']})

        error_type = result.get('error_type', 'analysis_failed')
        status_code = 429 if error_type == 'quota_exceeded' else 500
        return api_error(
            result.get('error', '分析失败'),
            status_code=status_code,
            error_type=error_type,
            details=result.get('details', {})
        )
    except Exception as e:
        traceback.print_exc()
        err_text = str(e).lower()
        if '429' in err_text or 'quota' in err_text or 'rate limit' in err_text:
            return api_error('AI 服务配额已用尽，请稍后重试', status_code=429, error_type='quota_exceeded', details={'retry_after': 60})
        return api_error(str(e), status_code=500, error_type='analysis_failed')

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
                with contextlib.suppress(Exception):
                    analysis['recommendations'] = json.loads(analysis['recommendations'])
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
