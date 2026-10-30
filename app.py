import atexit
import datetime
import hashlib
import ipaddress
import os
import re
import threading
import traceback

from dotenv import load_dotenv
from flask import Flask, abort, render_template, request, send_from_directory, session, url_for
from werkzeug.security import safe_join

import settings

load_dotenv(override=True)
import branding  # noqa: E402
from core.config import AVATAR_FOLDER, get_db_name  # noqa: E402
from services import (  # noqa: E402
    build_timeline,
    get_active_medication_plans,
    get_dashboard_stats,
    predict_morning_fpg,
    predict_post_exercise_glucose,
)
from user_manager import UserManager  # noqa: E402
from utils.auth import is_current_user_admin, login_required  # noqa: E402
from utils.avatars import DEFAULT_AVATARS  # noqa: E402
from utils.db import close_db, get_db, get_raw_conn, init_db, put_raw_conn  # noqa: E402

# ========== 配置 ==========
app = Flask(__name__)

# 运行环境判断
_is_prod = os.environ.get('FLASK_ENV') == 'production'

# SECRET_KEY：生产环境必须显式配置；开发环境使用临时 key（安全提示）
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:  # pragma: no cover (子进程隔离)
    if _is_prod:
        raise RuntimeError(
            "SECRET_KEY 环境变量未设置。"
            "请执行：export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        )
    import secrets as _secrets
    _secret_key = _secrets.token_hex(16)
    print("[WARN] 使用随机生成的临时 SECRET_KEY（开发模式）。生产环境请务必显式设置。")
app.secret_key = _secret_key

app.config['UPLOAD_FOLDER'] = AVATAR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# JSON 序列化不转义非 ASCII（P1c）：中文写成原文而不是 \uXXXX。
# timeline 里全是中文（备注、AI 饮食分析），转义会让体积白涨 3 倍。
# Flask 的 tojson 仍会转义 < > &（htmlsafe_dumps），防 </script> 逃逸不受影响。
app.json.ensure_ascii = False

# Session 安全配置
# SESSION_COOKIE_SECURE 默认跟随 FLASK_ENV（生产模式=仅经 HTTPS 传输 cookie）。
# 若暂时以 HTTP / IP 直连访问（例如无域名的试用环境），需显式设为 false，
# 否则浏览器会直接丢弃带 Secure 标志的 cookie，表现为「登录成功后又被踢回登录页」。
_sc_secure_env = os.environ.get('SESSION_COOKIE_SECURE')
app.config['SESSION_COOKIE_SECURE'] = (
    _sc_secure_env.strip().lower() == 'true' if _sc_secure_env else _is_prod
)
app.config['SESSION_COOKIE_HTTPONLY'] = True          # 禁止 JS 访问 cookie（XSS 偷不走会话）
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'         # CSRF 基础防护
# 会话有效期（小时）：闲置超过这个时间就要重新登录。
# 默认 720 小时 = 30 天 —— 家人嫌重登烦，一个月不碰才需要重登；
# 不是「永久登录」，cookie 仍有明确过期时间，设备丢了也能靠它兜底。
# 想更严就调小（例如 168 = 7 天），想更松就调大。
# 注意：只有登录时标记了 session.permanent 才生效（见 utils.auth.start_session）。
SESSION_LIFETIME_HOURS = float(os.environ.get('SESSION_LIFETIME_HOURS', '720'))
app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(hours=SESSION_LIFETIME_HOURS)

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
# 不传 db_path：路径在每次访问时动态解析，测试改环境变量即可隔离
user_manager = UserManager()

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


# ========== 静态资源版本化（缓存 busting）============
# 为什么需要：nginx 已给 /static/ 设了 30 天缓存，而模板里写的是固定路径
# /static/js/chat.js —— 一旦发了新版本，浏览器和中间缓存仍会用旧文件，
# 热修根本发不出去（要等 30 天）。加上内容 hash 后，文件一变 URL 就变。
#
# 为什么用「路径 hash」而不是「?v= 查询串」：
#   运营商/企业网关的透明代理会按 path 归一化缓存、忽略 query，
#   那时 ?v= 会静默失效——热修发不出去且毫无报错。
#   路径 hash 让每个版本有唯一 URL，中间缓存无从混淆；
#   而且旧 hash 直接 404（明确的失败），不会悄悄返回旧内容。
#
# 为什么不用构建体系生成 hash 文件名：项目没有前端构建链，
# 运行时按文件内容算 hash 是零构建的等效方案。
_static_hashes: dict[str, str] = {}


def _compute_static_hashes() -> dict[str, str]:
    """扫描 static 目录，按文件内容算 hash 前 8 位。

    启动时算一次就固定：容器运行期间 static 文件被替换（docker cp 热修）
    不会更新版本号，旧 URL 会 404 而不是静默返回新内容——
    这比「悄悄给旧文件」安全，也让"改了文件必须重启容器"这条规矩更明确
    （部署流程本来就要重建容器）。
    """
    hashes: dict[str, str] = {}
    base = app.static_folder
    for root, dirs, files in os.walk(base):
        # 头像目录是用户上传的私人图片，不参与版本化
        dirs[:] = [d for d in dirs if d != 'avatars']
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base)
            try:
                with open(full, 'rb') as f:
                    hashes[rel] = hashlib.sha256(f.read()).hexdigest()[:8]
            except OSError:
                continue
    return hashes


def static_hv(filename: str) -> str:
    """返回静态文件内容 hash 的前 8 位（启动时算好的固定值）。"""
    return _static_hashes.get(filename, 'missing')


@app.template_global()
def static_url(filename: str) -> str:
    """带内容 hash 的静态资源 URL，例如 /v/js/chat.21f46411.js。"""
    hv = static_hv(filename)
    base, ext = os.path.splitext(filename)
    return url_for('versioned_static', filename=f'{base}.{hv}{ext}')


# /v/<路径>.<hash><扩展名>：hash 必须与当前文件内容一致，否则 404。
# 校验这一步是路径 hash 方案的安全底线——否则任何人都能拿
# /v/js/chat.deadbeef.js 猜到未来的文件名。
_V_PATH_RE = re.compile(r'^(?P<stem>.+)\.(?P<hv>[0-9a-f]{8})(?P<ext>\.[A-Za-z0-9]+)$')


@app.route('/v/<path:filename>')
def versioned_static(filename: str):
    """按内容 hash 提供的静态资源，带 immutable 长缓存。"""
    m = _V_PATH_RE.match(filename)
    if not m:
        abort(404)
    rel = f"{m.group('stem')}{m.group('ext')}"
    # safe_join 挡住 ../ 之类的路径穿越
    if safe_join(app.static_folder, rel) is None:
        abort(404)
    if static_hv(rel) != m.group('hv'):
        # hash 与内容不符：文件已变（或压根不存在）→ 明确的 404
        abort(404)
    resp = send_from_directory(app.static_folder, rel, conditional=True)
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@app.after_request
def add_html_cache_headers(response):
    """HTML 明确 no-cache（但保留 ETag），静态资源各自走自己的缓存策略。

    用 no-cache 而不是 no-store：浏览器每次都带 If-None-Match 来验证，
    内容没变就拿 304（只传 header 不传 body）。

    注意 Flask 3 不再自动生成 ETag（实测：渲染响应的 ETag 为 None），
    没有 ETag 的 no-cache 等于「每次全量重下」——线上首页 gzip 后仍有 160KB。
    所以这里自己补 ETag，并调用 make_conditional 处理条件请求。

    private：页面含个人健康数据，不能被共享缓存（公司代理 / 运营商缓存）留存。
    Vary: Cookie：不同登录用户拿到的 HTML 不同，必须按 Cookie 区分缓存。
    """
    if response.mimetype == 'application/json':
        # JSON 是接口数据（timeline/stats/records），个人健康数据不能被共享缓存，
        # 也不该走启发式缓存（无 Cache-Control 时浏览器按 Last-Modified 推 10% 过期）
        # —— 那会让 /api/timeline 返回陈旧数据。
        response.headers.setdefault('Cache-Control', 'private, no-store')
        response.vary.add('Cookie')
        return response
    if response.mimetype == 'text/html' and not response.is_streamed:
        response.headers.setdefault('Cache-Control', 'private, no-cache, must-revalidate')
        if 'ETag' not in response.headers:
            # md5 与 Werkzeug 惯例一致（ETag 不用于安全用途）
            response.set_etag(hashlib.md5(response.get_data()).hexdigest())
        # 命中 If-None-Match → 304 + 空 body
        response.make_conditional(request)
        response.vary.add('Cookie')  # add 而非赋值：不覆盖上游已有 Vary 项
    return response


# 静态资源版本表：启动时算一次，进程生命周期内固定
_static_hashes.update(_compute_static_hashes())

# ========== 全局变量 ==========
_prediction_lock = threading.Lock()
_prediction_running = set()
_prediction_last_run = {}  # user_id -> timestamp
_prediction_fail_count = {}  # user_id -> consecutive failures
_PREDICTION_COOLDOWN_SUCCESS = 120  # 成功后 2 分钟冷却
_PREDICTION_COOLDOWN_FAIL_BASE = 60  # 失败后首次 60 秒
_PREDICTION_COOLDOWN_FAIL_MAX = 1800  # 最大 30 分钟

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
            failed = False
            try:
                pred_db = get_raw_conn()
                predict_morning_fpg(pred_db, user_id)
                predict_post_exercise_glucose(pred_db, user_id)
            except Exception as e:
                print(f"[AI] 后台预测出错: {e}")
                failed = True
            finally:
                if pred_db:
                    put_raw_conn(pred_db)
                with _prediction_lock:
                    _prediction_running.discard(user_id)
                    _prediction_last_run[user_id] = _time.time()
                    if failed:
                        n = _prediction_fail_count.get(user_id, 0) + 1
                        _prediction_fail_count[user_id] = n
                    else:
                        _prediction_fail_count.pop(user_id, None)

        import time as _time

        # 3. 获取当前用户启用的功能模块（空/None 视为全部启用）
        user = user_manager.get_user(current_user_id)
        enabled_modules = (user.get('enabled_modules') if user else None) or branding.ALL_MODULES

        # 血糖预测只服务于血糖模块：没开血糖的用户连后台线程都不启动，
        # 否则 AI 会给他凭空生成一条「空腹血糖」写进时间线。
        if 'glucose' not in enabled_modules:
            already_running = True
        else:
            with _prediction_lock:
                already_running = current_user_id in _prediction_running
                if not already_running:
                    last_run = _prediction_last_run.get(current_user_id, 0)
                    fail_n = _prediction_fail_count.get(current_user_id, 0)
                    cooldown = _PREDICTION_COOLDOWN_SUCCESS if fail_n == 0 else min(
                        _PREDICTION_COOLDOWN_FAIL_BASE * (2 ** (fail_n - 1)),
                        _PREDICTION_COOLDOWN_FAIL_MAX,
                    )
                    if _time.time() - last_run < cooldown:
                        already_running = True
                    else:
                        _prediction_running.add(current_user_id)
                        _prediction_last_run[current_user_id] = _time.time()
                        _prediction_fail_count.pop(current_user_id, None)

        if not already_running:
            threading.Thread(target=_run_predictions, args=(current_user_id,), daemon=True).start()

        # 2. 调用汇总服务获取统计数据
        # P1c：初始时间轴 90→30 天。前端已有懒加载（ensureTimelineData 按需
        # fetch /api/timeline 并按日期合并），默认趋势视图是 14 天、日历只显示本月，
        # 30 天足够覆盖首屏；往回翻或切到 60/90/全部时才补拉。
        sorted_dates, records = build_timeline(c, current_user_id, days=30)
        stats = get_dashboard_stats(db, current_user_id)

        # 补全 stats 中的时间轴数据（如果需要）
        stats['current_days'] = 30

        # 3.1 应用品牌个性化（应用名/图标/标语/使用场景），未自定义时回落默认品牌
        brand = branding.resolve_branding(user)

        # 4. 是否已绑定 Garmin（控制运动卡片上的"同步 Garmin"按钮显示）
        from services.garmin_service import has_garmin_binding
        is_garmin_user = has_garmin_binding(current_user_id)

        # P1b：页面 JS 拆到版本化静态文件后，数据统一经 window.__BOOT__ 传入。
        # slotPredictions 原来在模板里用 Jinja 循环拼 JS 对象字面量，服务端直接
        # 算好传 JSON。float(format(...)) 保持 "%.1f"|format 的 round-half-even 语义。
        slot_predictions = {
            slot['key']: (float(format(slot['value'], '.1f')) if slot.get('value') else None)
            for slot in (stats.get('today_overview') or [])
        }

        return render_template('index.html', records=records, stats=stats, timeline=sorted_dates,
                               enabled_modules=enabled_modules, is_garmin_user=is_garmin_user,
                               brand=brand, is_admin=is_current_user_admin(),
                               medication_plans=get_active_medication_plans(c, current_user_id),
                               avatar_catalog=DEFAULT_AVATARS,
                               user_emoji_map=settings.USER_EMOJI_MAP,
                               slot_predictions=slot_predictions)
    except Exception as e:
        traceback.print_exc()
        return f"Error loading index: {e}", 500

# ========== 备份 ==========
# 备份只有一份实现：scripts/ops/backup_db.py 的 make_backup()（SQLite Online Backup API）。
# 这里曾经有两个 threading.Timer 定时备份，都已摘除：
#   1) auto_backup：用 shutil.copy2 直拷活库，产出"外观合法但可能撕裂"的副本，
#      混在可信备份目录里 —— 恢复时选中即拿到坏库；
#   2) periodic_gcs_backup：Cloud Run 时代遗产，Lighthouse 上未配 GCS 即静默跳过。
# 现在定时备份由宿主机 cron 跑 backup_host.sh（容器停着也照跑），
# web 侧只在用户点"下载备份"时用同一个 make_backup() 现拍一份。
# 不要在这里加回任何自动备份定时器 —— 两个备份源 = 恢复时的判断题。

# ========== Garmin 自动同步 ==========
GARMIN_SYNC_INTERVAL = int(os.environ.get('GARMIN_SYNC_INTERVAL', 7200))
_garmin_sync_timer = None
_garmin_lock = threading.Lock()

def auto_garmin_sync():
    global _garmin_sync_timer
    try:
        from services import garmin_service
        # 老配置（GARMIN_USER_ID + 全局 token）先登记成绑定，之后按绑定逐用户同步
        garmin_service.ensure_legacy_binding()
        acquired = _garmin_lock.acquire(blocking=False)
        if acquired:
            try:
                results = garmin_service.sync_all_bindings()
                if results:
                    print(f'[Garmin] 定时同步: {results}')
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
    """启动后台定时任务（目前只剩 Garmin 同步）。

    由 Gunicorn master 进程通过 when_ready 钩子调用，
    或在开发模式（python app.py）下直接调用。

    备份**不在这里**：定时备份由宿主机 cron 跑 backup_host.sh，
    不依赖容器/worker 生命周期（见本文件"备份"段注释）。
    """
    # 绑定关系存数据库，不能再用 GARMIN_EMAIL 判断是否启动；无绑定时本轮为空
    auto_garmin_sync()


# ========== 蓝图注册 ==========
from routes.api_admin import bp_admin  # noqa: E402
from routes.api_auth import bp_auth  # noqa: E402
from routes.api_bone import bp_bone  # noqa: E402
from routes.api_chat import bp_chat  # noqa: E402
from routes.api_checkup import bp_checkup  # noqa: E402
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
app.register_blueprint(bp_bone)
app.register_blueprint(bp_checkup)

# 为敏感端点添加请求限速（在 Blueprint 注册后配置，避免循环导入）
# Flask Blueprint view_functions 使用 "blueprint.endpoint" 格式
app.view_functions['auth.login'] = limiter.limit("10 per minute")(app.view_functions['auth.login'])
app.view_functions['auth.set_password'] = limiter.limit("5 per minute")(app.view_functions['auth.set_password'])
app.view_functions['auth.change_password'] = limiter.limit("10 per minute")(app.view_functions['auth.change_password'])

# Apple Health 同步端点限速
app.view_functions['health_sync.bind_device'] = limiter.limit("5 per minute")(app.view_functions['health_sync.bind_device'])
app.view_functions['health_sync.bind_from_shortcut'] = limiter.limit("10 per minute")(app.view_functions['health_sync.bind_from_shortcut'])
app.view_functions['health_sync.sync_health_data'] = limiter.limit("30 per minute")(app.view_functions['health_sync.sync_health_data'])

# ========== robots.txt ==========
# 必须挂在根路径 /robots.txt（爬虫规范只认根路径）。
# 文件放 static/ 下是不够的——那只能从 /static/robots.txt 访问到，
# Googlebot 请求 /robots.txt 会得到 404，按"无协议"继续爬（review 实测确认）。
@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt', mimetype='text/plain')


# ========== 健康检查 ==========
# 只回 {'status':'ok'} 是不够的：数据库锁死、模板损坏、依赖没起来时页面早就 500 了，
# 探活却仍然 200 —— docker healthcheck 和 run_container.sh 的自动回滚永远不会触发。
# 回滚演练实测过：喂了个坏容器，探活一路绿灯，坏容器被当成"健康"留在生产上。
# 所以这里真的去读一次库、编译一次模板，任一项失败就返回 503。


def _client_is_internal(req) -> bool:
    """判断请求来源是否属于内网/本机。

    /health 通过 nginx 的 location / 对公网可达，异常原文里带着
    绝对路径和 SQLite 错误细节——没必要让外人看到。
    所以明细只给内网看，公网只拿到状态码和 status。

    判定顺序：
      - remote_addr 不是回环 → 直接按它判（容器被外部直接访问的情况）
      - remote_addr 是回环（说明经过了同机的 nginx 反代）→ 看 X-Forwarded-For
        的最后一段。nginx 用 $proxy_add_x_forwarded_for 追加，追加的是
        $remote_addr（真实客户端 IP），所以最后一段才可信；
        客户端自己伪造的头会排在前面。
      - 没有 XFF → 说明是直连本机（health_guard / docker healthcheck），算内网
    """
    remote = (req.remote_addr or '').strip()
    if remote not in ('127.0.0.1', '::1', 'localhost'):
        return _is_private_ip(remote)
    xff = req.headers.get('X-Forwarded-For', '')
    if not xff:
        return True
    return _is_private_ip(xff.split(',')[-1].strip())


def _is_private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


@app.route('/health')
def health_check():
    """服务健康检查端点（无需认证，用于 Docker/Nginx 探活）。

    返回 200 表示「能真正服务一个请求」；任一子项失败返回 503，
    让 run_container.sh / health_guard.sh 能据此触发回滚或重启。
    """
    checks = {}
    ok = True

    # 1) 数据库：文件必须在、大小非零、核心表可读、且不是"空库"
    db_path = get_db_name()
    try:
        if not os.path.isfile(db_path):
            raise FileNotFoundError(f'数据库文件不存在: {db_path}')
        if os.path.getsize(db_path) == 0:
            raise ValueError('数据库文件为空（0 字节）')
        db = get_db()
        db.execute('SELECT 1')
        # 光 SELECT 1 挡不住一种故障：SQLite 的 connect() 遇到不存在的路径会
        # 自动建一个空库，那时 SELECT 1 照样成功，但数据已经全没了。
        # 所以再确认两张核心表确实存在且可读。
        db.execute('SELECT 1 FROM records LIMIT 1').fetchall()
        # 关键一条：光有表还不够。应用启动时会自动给缺失的库建表，
        # 于是「.env 里 DB 路径写错 / 挂载点没挂上」会得到一个全新的空库，
        # 表齐全、探活全绿，但家人的数据"消失"了——比服务挂掉更危险。
        # 判据：用户表至少要有 1 行（这个系统必须有人才能用）。
        # 全新部署（还没注册第一个用户）可设 SB_MIN_USERS=0 放行。
        min_users = int(os.environ.get('SB_MIN_USERS', '1') or 0)
        n_users = db.execute('SELECT COUNT(*) FROM app_users').fetchone()[0]
        if n_users < min_users:
            raise RuntimeError(
                f'用户表只有 {n_users} 行（期望 >= {min_users}）'
                f'，疑似连到了空库而不是真正的数据文件'
            )
        checks['db'] = 'ok'
    except Exception as exc:  # noqa: BLE001 —— 探活的语义就是"任何毛病都报出来"
        checks['db'] = f'{type(exc).__name__}: {exc}'
        ok = False

    # 2) 模板：只编译不渲染。渲染一次首页要近百毫秒，编译命中 Jinja 缓存几乎零成本。
    # debug 模式下 TEMPLATES_AUTO_RELOAD 会让它每次重编 11880 行，本地开发没必要付这个代价。
    if app.debug:
        checks['template'] = 'skipped(debug)'
    else:
        try:
            # 两张都要编译：login.html 是所有人的入口，它坏了探活也得报
            for _tpl_name in ('index.html', 'login.html'):
                app.jinja_env.get_template(_tpl_name)
            checks['template'] = 'ok'
        except Exception as exc:  # noqa: BLE001
            checks['template'] = f'{type(exc).__name__}: {exc}'
            ok = False

    if not _client_is_internal(request):
        # 公网只暴露"健康/不健康"，异常原文和路径留在内网
        return {'status': 'ok' if ok else 'degraded', 'service': 'sugar-bee'}, 200 if ok else 503

    payload = {
        'status': 'ok' if ok else 'degraded',
        'service': 'sugar-bee',
        'checks': checks,
    }
    return payload, 200 if ok else 503


if __name__ == '__main__':
    with app.app_context():
        init_db()
    start_background_tasks()
    # 端口默认 5001，可用 PORT 环境变量覆盖（如本机 5001 被 Kimi 内置预览占用时）
    app.run(host='0.0.0.0', debug=True, port=int(os.environ.get('PORT', 5001)))
