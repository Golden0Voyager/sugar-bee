"""
WSGI 入口文件

用于 Gunicorn 等 WSGI 服务器启动应用。
开发环境仍可直接运行：python app.py
"""

from app import app
from utils.db import init_db

# 初始化数据库（幂等操作，每个 worker 启动时执行）
with app.app_context():
    init_db()

# Gunicorn 识别的 application 对象
application = app
