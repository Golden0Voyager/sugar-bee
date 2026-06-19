import atexit
import datetime
import glob as glob_mod
import os
import shutil
import threading
import traceback

from dotenv import load_dotenv
from flask import Flask, render_template, session

import settings

load_dotenv(override=True)

from core.config import AVATAR_FOLDER, DB_NAME  # noqa: E402
from services import (  # noqa: E402
    build_timeline,
    get_dashboard_stats,
    predict_morning_fpg,
    predict_post_exercise_glucose,
)
from services.gcs_sync import (  # noqa: E402
    backup_db_to_gcs,
    sync_file_to_gcs,
)
from user_manager import UserManager  # noqa: E402
from utils.auth import login_required  # noqa: E402
from utils.db import close_db, get_db, get_raw_conn, init_db, put_raw_conn  # noqa: E402

# ========== 配置 ==========
app = Flask(__name__)

# 运行环境判断
_is_prod = os.environ.get('FLASK_ENV') == 'production'

# SECRET_KEY：生产环境必须显式配置；开发环境使用临时 key（安全提示）
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    if _is_prod:  # pragma: no cover (子进程隔离)
        raise RuntimeError(
            "SECRET_KEY 环境变量未设置。"
            "请执行：export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        )  # pragma: no cover
    import secrets as _secrets  # pragma: no cover（SECRET_KEY 在测试/生产环境始终有值）
    _secret_key = _secrets.token_hex(16)  # pragma: no cover
    print("[WARN] 使用随机生成的临时 SECRET_KEY（开发模式）。生产环境请务必显式设置。")  # pragma: no cover
app.secret_key = _secret_key

app.config['UPLOAD_FOLDER'] = AVATAR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# Session 安全配置
app.config['SESSION_COOKIE_SECURE'] = _is_prod       # 仅 HTTPS 传输 cookie
app.config['SESSION_COOKIE_HTTPONLY'] = True          # 禁止 JS 访问 cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'         # CSRF 基础防护
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=7)

os.makedirs(AVATAR_FOLDER, exist_ok=True)

# ========== 请求限速 ==========
from flask_limiter import Limiter  # noqa: E402
from flask_limiter.util import get_remote_address  # noqa: E402

# 生产环境多 worker 时建议配置 REDIS_URL 实现共享限速状态
_limiter_storage = os.environ.get('REDIS_URL', 'memory://')
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per minute", "1000 per hour"],
    storage_uri=_limiter_storage,
    strategy="fixed-window",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
user_manager = UserManager(DB_NAME)

# AI 功能可用性检查
try:
    from ai_client import AI_AVAILABLE, call_ai
except ImportError:  # pragma: no cover (子进程隔离)
    AI_AVAILABLE = False  # pragma: no cover
    def call_ai(*args, **kwargs): return "AI Not Available"  # pragma: no cover

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
            pred_db = None
            try:
                pred_db = get_raw_conn()
                predict_morning_fpg(pred_db, user_id)
                predict_post_exercise_glucose(pred_db, user_id)
            except Exception as e:
                print(f"[AI] 后台预测出错: {e}")
            finally:
                if pred_db:
                    put_raw_conn(pred_db)
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

        # Cloud Run / 生产环境：同时上传当前数据库到 GCS
        if os.environ.get('GCS_BUCKET_NAME'):
            backup_db_to_gcs()

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


# ========== GCS 周期性备份（Cloud Run 无状态环境）==========
GCS_BACKUP_INTERVAL = int(os.environ.get('GCS_BACKUP_INTERVAL', 300))
_periodic_gcs_timer = None


def periodic_gcs_backup():
    """每 5 分钟将当前数据库备份到 GCS，并同步 Garmin token。"""
    global _periodic_gcs_timer
    try:
        if os.environ.get('GCS_BUCKET_NAME'):
            backup_db_to_gcs()
            # Garmin token 持久化
            token_dir = os.environ.get('GARMIN_TOKEN_DIR', os.path.join(BASE_DIR, '.garmin_tokens'))
            token_file = os.path.join(token_dir, 'garmin_tokens.json')
            if os.path.isfile(token_file):
                sync_file_to_gcs(token_file, 'garmin_tokens/garmin_tokens.json')
    except Exception as e:
        print(f'[GCS Periodic] Error: {e}')
    finally:
        _periodic_gcs_timer = threading.Timer(GCS_BACKUP_INTERVAL, periodic_gcs_backup)
        _periodic_gcs_timer.daemon = True
        _periodic_gcs_timer.start()


atexit.register(lambda: _periodic_gcs_timer.cancel() if _periodic_gcs_timer else None)

# 停机/退出时尽量把当前数据库备份到 GCS
atexit.register(lambda: backup_db_to_gcs() if os.environ.get('GCS_BUCKET_NAME') else None)

# ========== Garmin 自动同步 ==========
GARMIN_SYNC_INTERVAL = int(os.environ.get('GARMIN_SYNC_INTERVAL', 7200))
_garmin_sync_timer = None
_garmin_lock = threading.Lock()

def auto_garmin_sync():
    global _garmin_sync_timer
    try:
        user_id = int(os.environ.get('GARMIN_USER_ID', 0) or 0)
        if user_id and os.environ.get('GARMIN_EMAIL'):
            _app_dir = os.path.dirname(os.path.abspath(__file__))
            token_dir = os.environ.get('GARMIN_TOKEN_DIR', os.path.join(_app_dir, '.garmin_tokens'))
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


def start_background_tasks():
    """启动所有后台定时任务（自动备份、Garmin 同步、GCS 周期性备份）。

    由 Gunicorn master 进程通过 when_ready 钩子调用，
    或在开发模式（python app.py）下直接调用。
    """
    auto_backup()
    if os.environ.get('GARMIN_EMAIL'):
        auto_garmin_sync()
    # Cloud Run / 生产环境启用更频繁的 GCS 备份
    if os.environ.get('GCS_BUCKET_NAME'):
        periodic_gcs_backup()


# ========== 蓝图注册 ==========
from routes.api_admin import bp_admin  # noqa: E402
from routes.api_auth import bp_auth  # noqa: E402
from routes.api_chat import bp_chat  # noqa: E402
from routes.api_dashboard import bp_dashboard  # noqa: E402
from routes.api_health import bp_health  # noqa: E402
from routes.api_health_sync import bp_health_sync  # noqa: E402
from routes.api_internal import bp_internal  # noqa: E402
from routes.api_meds import bp_meds  # noqa: E402
from routes.api_prediction import bp_prediction  # noqa: E402
from routes.api_records import bp_records  # noqa: E402
from routes.api_user import bp_user  # noqa: E402

app.register_blueprint(bp_auth)
app.register_blueprint(bp_chat)
app.register_blueprint(bp_records)
app.register_blueprint(bp_meds)
app.register_blueprint(bp_health)
app.register_blueprint(bp_admin)
app.register_blueprint(bp_user)
app.register_blueprint(bp_dashboard)
app.register_blueprint(bp_prediction)
app.register_blueprint(bp_internal)
app.register_blueprint(bp_health_sync)

# 为敏感端点添加请求限速（在 Blueprint 注册后配置，避免循环导入）
# Flask Blueprint view_functions 使用 "blueprint.endpoint" 格式
app.view_functions['auth.login'] = limiter.limit("10 per minute")(app.view_functions['auth.login'])
app.view_functions['auth.set_password'] = limiter.limit("5 per minute")(app.view_functions['auth.set_password'])
app.view_functions['auth.change_password'] = limiter.limit("10 per minute")(app.view_functions['auth.change_password'])


# ========== 健康检查 ==========
@app.route('/health')
def health_check():
    """服务健康检查端点（无需认证，用于 Docker/Nginx 探活）。"""
    return {'status': 'ok', 'service': 'sugar-bee'}, 200


if __name__ == '__main__':
    with app.app_context():
        init_db()
    start_background_tasks()
    app.run(host='0.0.0.0', debug=True, port=5001)
