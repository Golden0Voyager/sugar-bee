"""
Pytest 配置和共享 fixtures
"""
import contextlib
import os
import sqlite3
import tempfile

import pytest

# 强制测试环境配置
os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')
os.environ.setdefault('FLASK_ENV', 'testing')


@pytest.fixture
def db_info():
    """提供临时数据库路径信息（在所有 fixture 之前运行）"""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.environ['SUGAR_BEE_DB_PATH'] = db_path
    yield {'path': db_path, 'fd': db_fd}
    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def app(db_info, monkeypatch):
    """创建测试用的 Flask 应用实例"""
    from app import app
    from core import config as core_config
    from utils.db import init_db

    # 强制覆盖 config.DB_NAME：core.config 可能在 db_info 设置环境变量之前就被
    # 其他模块 import 时缓存了生产路径，monkeypatch 确保所有测试使用 db_info 指定的临时数据库。
    monkeypatch.setattr(core_config, 'DB_NAME', db_info['path'])

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    # 初始化数据库（幂等操作，每个 worker 启动时执行）
    with app.app_context():
        init_db()

    yield app


@pytest.fixture
def client(app):
    """创建测试客户端"""
    # 清除 limiter 存储
    from app import limiter
    if hasattr(limiter, '_storage') and limiter._storage:
        limiter._storage.reset()
    elif hasattr(limiter, 'storage') and limiter.storage:
        with contextlib.suppress(Exception):
            limiter.storage.reset()

    with app.test_client() as client:
        yield client


@pytest.fixture
def client_authenticated(client, app):
    """创建已登录的测试客户端"""
    from core.config import DB_NAME
    from user_manager import UserManager

    um = UserManager(DB_NAME)

    # 若数据库里已残留 _test 用户（含 is_active=0 或之前的测试数据），先清理再创建，
    # 避免 UNIQUE constraint failed。
    conn = sqlite3.connect(DB_NAME)
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM app_users WHERE username = ?", ('_test',))
        row = c.fetchone()
        if row:
            user_id = row[0]
            c.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM app_users WHERE id = ?", (user_id,))
            conn.commit()
    finally:
        conn.close()

    user_id = um.create_user('_test', '测试',
                             {'name': '测试', 'birth_year': 1980, 'height': 175, 'weight': 70, 'gender': 'male'},
                             password='_test_pass')

    with client.session_transaction() as sess:
        sess['current_user_id'] = user_id

    return client


@pytest.fixture
def isolate_db(monkeypatch, db_info):
    """将 core.config.DB_NAME 指向 db_info 创建的临时 SQLite 数据库，并初始化表。

    集成测试使用真实 SQLite（不 mock get_db）时，需要用此 fixture 确保每个测试
    使用独立的临时数据库文件，互不干扰。

    依赖 db_info（确保 env var 已设）→ monkeypatch 覆盖模块级 DB_NAME
    （避免 Python 模块缓存导致的多测试共享同一路径）→ 显式 init_db()。
    """
    from core import config
    from utils.db import init_db
    db_path = db_info['path']
    monkeypatch.setattr(config, 'DB_NAME', db_path)
    init_db()
    yield


@pytest.fixture
def runner(app):
    """创建 CLI 测试 runner"""
    return app.test_cli_runner()
