"""
WSGI 入口文件

用于 Gunicorn 等 WSGI 服务器启动应用。
开发环境仍可直接运行：python app.py
"""

from app import app
from services.gcs_sync import restore_db_from_gcs
from utils.db import init_db

# 1. 先从 GCS 恢复数据库（Cloud Run 无状态环境必需）
restore_db_from_gcs()

# 2. 初始化数据库（幂等操作，每个 worker 启动时执行）
with app.app_context():
    init_db()

# Gunicorn 识别的 application 对象
application = app
