from flask import Flask, render_template, session
import sqlite3
import datetime
import os
import traceback
import threading
import glob as glob_mod
import atexit
import shutil
from dotenv import load_dotenv
import settings

load_dotenv()

from user_manager import UserManager  # noqa: E402
from core.config import DB_NAME, AVATAR_FOLDER  # noqa: E402
from utils.auth import login_required  # noqa: E402
from utils.db import get_db, close_db, init_db  # noqa: E402
from services import (  # noqa: E402
    build_timeline,
    predict_morning_fpg,
    predict_post_exercise_glucose,
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

        # 3. 获取当前用户启用的功能模块（空/None 视为全部启用）
        user = user_manager.get_user(current_user_id)
        enabled_modules = (user.get('enabled_modules') if user else None) or [
            'glucose', 'blood_pressure', 'exercise', 'weight', 'medication'
        ]

        # 4. 是否为绑定 Garmin 的用户（控制运动卡片上的"同步 Garmin"按钮显示）
        garmin_uid = int(os.environ.get('GARMIN_USER_ID', 0) or 0)
        is_garmin_user = bool(garmin_uid and os.environ.get('GARMIN_EMAIL') and current_user_id == garmin_uid)

        return render_template('index.html', records=records, stats=stats, timeline=sorted_dates,
                               enabled_modules=enabled_modules, is_garmin_user=is_garmin_user,
                               user_emoji_map=settings.USER_EMOJI_MAP)
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
        if not os.path.exists(DB_NAME):
            return
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
            except Exception:
                pass
    except Exception as e:
        print(f'[AutoBackup] Error: {e}')
    finally:
        _auto_backup_timer = threading.Timer(86400, auto_backup)
        _auto_backup_timer.daemon = True
        _auto_backup_timer.start()

atexit.register(lambda: _auto_backup_timer.cancel() if _auto_backup_timer else None)

# ========== Garmin 自动同步 ==========
GARMIN_SYNC_INTERVAL = int(os.environ.get('GARMIN_SYNC_INTERVAL', 7200))
_garmin_sync_timer = None
_garmin_lock = threading.Lock()

def auto_garmin_sync():
    global _garmin_sync_timer
    try:
        user_id = int(os.environ.get('GARMIN_USER_ID', 0) or 0)
        if user_id and os.environ.get('GARMIN_EMAIL'):
            token_dir = os.environ.get('GARMIN_TOKEN_DIR', '.garmin_tokens')
            token_file = os.path.join(token_dir, 'garmin_tokens.json')
            # 首次没有 token 时跳过自动同步，避免撞 Garmin IP 限流；需用户手动触发一次完成首登
            if not os.path.isfile(token_file):
                print('[Garmin] 未找到持久化 token，跳过自动同步；请先手动触发一次以完成首次登录')
            else:
                acquired = _garmin_lock.acquire(blocking=False)
                if acquired:
                    try:
                        from services.garmin_service import sync_activities
                        result = sync_activities(user_id, days=30)
                        print(f'[Garmin] 定时同步: {result}')
                    finally:
                        _garmin_lock.release()
                else:
                    print('[Garmin] 上次同步未完成，跳过本轮')
    except Exception as e:
        print(f'[Garmin] 同步出错: {e}')
    finally:
        _garmin_sync_timer = threading.Timer(GARMIN_SYNC_INTERVAL, auto_garmin_sync)
        _garmin_sync_timer.daemon = True
        _garmin_sync_timer.start()

atexit.register(lambda: _garmin_sync_timer.cancel() if _garmin_sync_timer else None)

# ========== 蓝图注册 ==========
from routes.api_auth import bp_auth  # noqa: E402
from routes.api_chat import bp_chat  # noqa: E402
from routes.api_records import bp_records  # noqa: E402
from routes.api_meds import bp_meds  # noqa: E402
from routes.api_health import bp_health  # noqa: E402
from routes.api_admin import bp_admin  # noqa: E402
from routes.api_user import bp_user  # noqa: E402
from routes.api_dashboard import bp_dashboard  # noqa: E402
from routes.api_prediction import bp_prediction  # noqa: E402

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
    if os.environ.get('GARMIN_EMAIL'):
        auto_garmin_sync()
    app.run(host='0.0.0.0', debug=True, port=5001)