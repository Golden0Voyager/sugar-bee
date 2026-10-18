"""
Pytest 配置和共享 fixtures
"""
import atexit
import contextlib
import os
import sqlite3
import tempfile

import pytest

# 强制测试环境配置
os.environ.setdefault('SECRET_KEY', 'test-secret-key-not-for-production')
os.environ.setdefault('FLASK_ENV', 'testing')


def _make_session_guard_db():
    """会话级安全网：把 SUGAR_BEE_DB_PATH 指向一个临时文件。

    conftest 先于任何测试模块被 import。若不先占住这个环境变量，测试代码里
    `from core.config import DB_NAME` 之类在 import 时求值的写法拿到的就是真实
    的 glucose.db 路径，测试一旦误写就会污染生产数据（历史上已发生过）。

    db_info / isolate_db 仍会在每个测试里换成各自的临时库，这里只兜底。
    """
    fd, path = tempfile.mkstemp(prefix='sugar_bee_guard_', suffix='.db')
    os.close(fd)
    os.environ['SUGAR_BEE_DB_PATH'] = path

    # 顺手把表结构建好：少数旧测试会命中这个安全网库去查真实表名
    # （它们以前是直接查真实库的），没有 schema 会变成 no such table。
    from utils.db import init_db
    init_db()

    def _cleanup():
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    atexit.register(_cleanup)
    return path


GUARD_DB_PATH = _make_session_guard_db()


@pytest.fixture(autouse=True)
def _restore_db_env():
    """兜底：每个测试结束后把 SUGAR_BEE_DB_PATH 恢复到安全网路径。

    不用「恢复成测试开始时的值」：autouse fixture 的实际建立顺序晚于 db_info
    （观测到的顺序是 db_info → autouse），所以「开始时的值」本身就是那条临时库，
    正是上一轮测试留下的失效路径。安全网路径是唯一合法的默认值，直接还原它。
    """
    yield
    os.environ['SUGAR_BEE_DB_PATH'] = GUARD_DB_PATH


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
def app(db_info):
    """创建测试用的 Flask 应用实例"""
    from app import app
    from utils.db import init_db

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
    from core.config import get_db_name
    from user_manager import UserManager

    db_path = get_db_name()
    um = UserManager()

    # 若数据库里已残留 _test 用户（含 is_active=0 或之前的测试数据），先清理再创建，
    # 避免 UNIQUE constraint failed。
    conn = sqlite3.connect(db_path)
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
    """把数据库指向 db_info 创建的临时 SQLite 文件，并初始化表。

    集成测试使用真实 SQLite（不 mock get_db）时，需要用此 fixture 确保每个测试
    使用独立的临时数据库文件，互不干扰。

    依赖 db_info（确保 SUGAR_BEE_DB_PATH 已指向临时库）→ 同时覆盖模块级
    DB_NAME（兼容仍在 import 期固定路径的旧代码）→ 显式 init_db()。
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
