"""
WSGI 入口文件

用于 Gunicorn 等 WSGI 服务器启动应用。
开发环境仍可直接运行：python app.py
"""

import time

from app import app
from core.config import DB_TYPE
from services.gcs_sync import restore_db_from_gcs
from utils.db import init_db

# 1. 先从 GCS 恢复数据库（仅 SQLite 模式；Cloud Run PostgreSQL 由 Cloud SQL 托管）
if DB_TYPE == 'sqlite':
    restore_db_from_gcs()

# 2. 初始化数据库（幂等操作，每个 worker 启动时执行）
# Cloud Run + Cloud SQL 场景下，Unix socket 可能需要短暂时间才能就绪，这里带重试。
_max_retries = 5 if DB_TYPE == 'postgres' else 1
_retry_delay = 3
for attempt in range(1, _max_retries + 1):
    try:
        with app.app_context():
            init_db()
        print(f"[wsgi] 数据库初始化成功（尝试 {attempt}/{_max_retries}）")
        break
    except Exception as e:
        print(f"[wsgi] 数据库初始化失败（尝试 {attempt}/{_max_retries}）: {e}")
        if attempt == _max_retries:
            raise
        time.sleep(_retry_delay)

# Gunicorn 识别的 application 对象
application = app
