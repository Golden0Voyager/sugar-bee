from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file, g, session, after_this_request
import sqlite3
import pandas as pd
import datetime
import os
import io
import traceback
import threading
import glob as glob_mod
import atexit
import re
import json
import shutil
from functools import wraps
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()

import settings
from user_manager import UserManager
from core.config import DB_NAME, AVATAR_FOLDER, ALLOWED_EXTENSIONS
from utils.responses import success_res, error_res, api_success, api_error
from utils.auth import login_required
from utils.db import get_db, close_db, init_db
import services
from services import (
    build_timeline, 
    generate_health_analysis, 
    auto_trigger_health_analysis,
    predict_morning_fpg,
    predict_post_exercise_glucose,
    backfill_post_exercise_predictions,
    predict_remaining_glucose_slots,
    get_dashboard_stats
)

# 配置
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'glucose_secret_key_123')
app.config['UPLOAD_FOLDER'] = AVATAR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
os.makedirs(AVATAR_FOLDER, exist_ok=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
user_manager = UserManager(DB_NAME)

# AI 功能可用性检查
try:
    from ai_client import call_ai, AI_AVAILABLE
except ImportError:
    AI_AVAILABLE = False
    def call_ai(*args, **kwargs): return "AI Not Available"

# 数据库连接生命周期管理
@app.teardown_appcontext
def teardown_db(exception):
    close_db()

# ========== 全局变量 ==========
_prediction_lock = threading.Lock()
_prediction_running = set()

# ========== 核心路由 ==========

@app.route('/')
@login_required
def index():
    try:
        db = get_db()
        c = db.cursor()
        current_user_id = session.get('current_user_id')
        
        # 1. 自动触发分析与预测 (后台运行)
        def _run_predictions(user_id):
            try:
                pred_db = sqlite3.connect(DB_NAME)
                pred_db.row_factory = sqlite3.Row
                predict_morning_fpg(pred_db, user_id)
                predict_post_exercise_glucose(pred_db, user_id)
                pred_db.close()
            except Exception as e:
                print(f"[AI] 后台预测出错: {e}")
            finally:
                with _prediction_lock:
                    _prediction_running.discard(user_id)

        already_running = False
        with _prediction_lock:
            if current_user_id not in _prediction_running:
                _prediction_running.add(current_user_id)
            else:
                already_running = True

        if not already_running:
            threading.Thread(target=_run_predictions, args=(current_user_id,), daemon=True).start()

        # 2. 调用汇总服务获取统计数据
        # 默认加载 90 天数据
        sorted_dates, records = build_timeline(c, current_user_id, days=90)
        stats = get_dashboard_stats(db, current_user_id)
        
        # 补全 stats 中的时间轴数据（如果需要）
        stats['current_days'] = 90
        
        return render_template('index.html', records=records, stats=stats, timeline=sorted_dates)
    except Exception as e:
        traceback.print_exc()
        return f"Error loading index: {e}", 500

# ========== 自动备份 ==========
AUTO_BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
AUTO_BACKUP_KEEP_DAYS = 30
_auto_backup_timer = None

def auto_backup():
    global _auto_backup_timer
    try:
        if not os.path.exists(DB_NAME): return
        os.makedirs(AUTO_BACKUP_DIR, exist_ok=True)
        today = datetime.date.today().strftime('%Y%m%d')
        backup_path = os.path.join(AUTO_BACKUP_DIR, f'glucose_auto_{today}.db')
        if not os.path.exists(backup_path):
            shutil.copy2(DB_NAME, backup_path)
        
        cutoff = datetime.date.today() - datetime.timedelta(days=AUTO_BACKUP_KEEP_DAYS)
        for f in glob_mod.glob(os.path.join(AUTO_BACKUP_DIR, 'glucose_auto_*.db')):
            try:
                date_str = os.path.basename(f).replace('glucose_auto_', '').replace('.db', '')
                if datetime.datetime.strptime(date_str, '%Y%m%d').date() < cutoff:
                    os.remove(f)
            except: pass
    except Exception as e:
        print(f'[AutoBackup] Error: {e}')
    finally:
        _auto_backup_timer = threading.Timer(86400, auto_backup)
        _auto_backup_timer.daemon = True
        _auto_backup_timer.start()

atexit.register(lambda: _auto_backup_timer.cancel() if _auto_backup_timer else None)

# ========== 蓝图注册 ==========
from routes.api_auth    import bp_auth
from routes.api_chat    import bp_chat
from routes.api_records import bp_records
from routes.api_meds    import bp_meds
from routes.api_health  import bp_health
from routes.api_admin   import bp_admin
from routes.api_user    import bp_user
from routes.api_dashboard  import bp_dashboard
from routes.api_prediction import bp_prediction

app.register_blueprint(bp_auth)
app.register_blueprint(bp_chat)
app.register_blueprint(bp_records)
app.register_blueprint(bp_meds)
app.register_blueprint(bp_health)
app.register_blueprint(bp_admin)
app.register_blueprint(bp_user)
app.register_blueprint(bp_dashboard)
app.register_blueprint(bp_prediction)

if __name__ == '__main__':
    with app.app_context():
        init_db()
    auto_backup()
    app.run(debug=True, port=5001)