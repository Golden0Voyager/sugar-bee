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
def app():
    """创建测试用的 Flask 应用实例"""
    from app import app
    from utils.db import init_db

    # 使用临时数据库，避免污染生产数据
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.environ['SUGAR_BEE_DB_PATH'] = db_path
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    # 重新初始化数据库
    with app.app_context():
        init_db()

    yield app

    # 清理
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """创建测试客户端"""
    # 清除 limiter 存储，避免测试间限速状态互相影响
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
def runner(app):
    """创建 CLI 测试 runner"""
    return app.test_cli_runner()
