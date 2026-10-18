"""数据库隔离回归测试。

目标：任何测试都不允许写入真实的 glucose.db。

历史上 `tests/test_api_health_sync.py` 等测试未走临时库，直接读写生产数据，
导致库里混入 21 条测试记录和 18 个 `_sync_test_*` 账号。根因是生产代码在
import 时把数据库路径固定成真实路径（`UserManager(DB_NAME)`、
`utils/db.py` 读 `config.DB_NAME`），测试改了环境变量也不生效。

现在生产代码统一在运行时解析路径（`core.config.get_db_name()` + `UserManager()`），
conftest 另有一层会话级安全网兜底。本文件锁定这两点。
"""
import hashlib
import os
import sqlite3

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DB = os.path.join(PROJECT_ROOT, 'glucose.db')


def _md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def _real_db_signature():
    return _md5(REAL_DB) if os.path.exists(REAL_DB) else None


@pytest.fixture
def real_db_unchanged():
    """记录真实库指纹，测试结束后校验未被改动。"""
    before = _real_db_signature()
    yield before
    after = _real_db_signature()
    assert after == before, "测试写入了真实数据库：{}".format(REAL_DB)


class TestEnvDrivenPath:
    """路径解析跟随环境变量。"""

    def test_get_db_name_follows_env(self, db_info):
        from core.config import get_db_name
        assert get_db_name() == db_info['path']

    def test_isolate_db_repairs_module_constant(self, isolate_db):
        from core.config import DB_NAME, get_db_name
        assert get_db_name() == DB_NAME


class TestIsolationInPractice:
    """真实写入路径落在临时库。"""

    def test_route_write_stays_in_temp_db(self, client_authenticated, db_info,
                                          real_db_unchanged):
        resp = client_authenticated.post('/add', json={
            'value': 5.5,
            'unit': 'mmol/L',
            'type': '空腹',
            'timestamp': '2024-06-01 07:30:00',
        })
        assert resp.status_code == 200

        conn = sqlite3.connect(db_info['path'])
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM records WHERE type = '空腹' AND value = 5.5"
            ).fetchone()[0]
        finally:
            conn.close()
        assert n == 1, "记录没有写进 db_info 提供的临时库"

    def test_module_singletons_resolve_dynamically(self, db_info):
        """各路由模块的模块级 user_manager 单例不能钉死路径。"""
        from utils.db import init_db
        init_db()

        import routes.api_admin as admin_mod
        import routes.api_auth as auth_mod
        import routes.api_dashboard as dashboard_mod
        import routes.api_health as health_mod
        import routes.api_health_sync as sync_mod
        import routes.api_meds as meds_mod
        import routes.api_prediction as prediction_mod
        import routes.api_records as records_mod
        import routes.api_user as user_mod

        modules = [admin_mod, auth_mod, dashboard_mod, health_mod,
                   sync_mod, meds_mod, prediction_mod, records_mod, user_mod]
        created = [m.user_manager.create_user(
            'iso_probe_%d' % i, '隔离探针', {}, password='probe_pass')
            for i, m in enumerate(modules)]

        conn = sqlite3.connect(db_info['path'])
        try:
            users = dict(conn.execute(
                "SELECT id, username FROM app_users WHERE username LIKE 'iso_probe_%'"
            ).fetchall())
        finally:
            conn.close()
        assert set(users) == set(created)

    def test_get_db_uses_temp_db(self, db_info, real_db_unchanged):
        from app import app
        from utils.db import init_db
        init_db()
        with app.app_context():
            from utils.db import get_db
            db = get_db()
            db.execute(
                "INSERT INTO records (user_id, value, unit, type, timestamp) "
                "VALUES (1, 6.6, 'mmol/L', '睡前', '2024-06-01 22:00:00')")
            db.commit()

        conn = sqlite3.connect(db_info['path'])
        try:
            n = conn.execute("SELECT COUNT(*) FROM records WHERE type = '睡前'").fetchone()[0]
        finally:
            conn.close()
        assert n == 1, "get_db() 没有连到 db_info 的临时库"
