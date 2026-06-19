"""
Gunicorn 生产环境配置文件

文档：https://docs.gunicorn.org/en/stable/configure.html
"""

import os

# ========== 服务器套接字 ==========
# Cloud Run 通过 PORT 环境变量注入端口；本地仍可用 GUNICORN_BIND / 5000 兜底
_port = os.environ.get("PORT", os.environ.get("GUNICORN_PORT", "5000"))
bind = os.environ.get("GUNICORN_BIND", f"0.0.0.0:{_port}")

# ========== Worker 进程 ==========
# Cloud Run 实例默认 1 vCPU，且 SQLite 不支持并发写入，默认 1 个 worker
# 本地或明确配置 GUNICORN_WORKERS 时可覆盖
workers = int(os.environ.get("GUNICORN_WORKERS", 1))

# Worker 类型
# sync: 同步 worker，适合大多数场景
worker_class = "sync"

# 每个 worker 处理的最大请求数，超过后自动重启（防止内存泄漏）
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = int(os.environ.get("GUNICORN_MAX_REQUESTS_JITTER", 50))

# ========== 超时设置 ==========
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", 30))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", 5))

# ========== 日志 ==========
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")  # "-" 表示输出到 stdout
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")    # "-" 表示输出到 stderr
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ========== 进程名 ==========
proc_name = "sugar_bee"

# ========== 安全 ==========
# 限制请求头大小，防止 Slowloris 攻击
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

# ========== 钩子函数 ==========

def when_ready(server):
    """
    Gunicorn master 进程就绪后调用（仅执行一次）。
    用于启动全局后台任务（自动备份、Garmin 同步）。
    """
    import sys

    _cwd = os.path.dirname(os.path.abspath(__file__))
    if _cwd not in sys.path:
        sys.path.insert(0, _cwd)

    try:
        from app import start_background_tasks
        start_background_tasks()
        print("[Gunicorn] 后台任务已启动（自动备份、Garmin 同步）")
    except Exception as e:
        print(f"[Gunicorn] 后台任务启动失败: {e}")


def worker_int(worker):
    """Worker 收到 SIGINT/SIGQUIT 时调用"""
    print(f"[Gunicorn] Worker {worker.pid} 正在优雅退出...")


def on_exit(server):
    """Master 进程退出时调用"""
    from core.config import DB_TYPE
    if DB_TYPE == 'sqlite':
        try:
            from services.gcs_sync import backup_db_to_gcs
            backup_db_to_gcs()
        except Exception as e:  # pragma: no cover
            print(f"[Gunicorn] GCS 停机备份失败: {e}")
    print("[Gunicorn] 服务器已关闭")
