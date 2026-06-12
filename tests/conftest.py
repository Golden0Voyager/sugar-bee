"""
Pytest 配置和共享 fixtures
"""
import os
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
def app(db_info):
    """创建测试用的 Flask 应用实例"""
    from app import app
    from utils.db import init_db

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    # 初始化数据库
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
        try:
            limiter.storage.reset()
        except Exception:
            pass

    with app.test_client() as client:
        yield client


@pytest.fixture
def client_authenticated(client, app):
    """创建已登录的测试客户端"""
    from user_manager import UserManager
    from core.config import DB_NAME

    um = UserManager(DB_NAME)
    # 获取或创建测试用户
    existing = um.get_user_by_username('_test')
    if not existing:
        user_id = um.create_user('_test', '测试',
                                 {'name': '测试', 'birth_year': 1980, 'height': 175, 'weight': 70, 'gender': 'male'},
                                 password='_test_pass')
    else:
        user_id = existing['id']

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
