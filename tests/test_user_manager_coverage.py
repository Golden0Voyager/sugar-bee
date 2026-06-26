"""
user_manager.py 最后覆盖冲刺 (91% -> ~97%)

未覆盖行:
  L45   — get_all_users enabled_modules else []
  L48-51 — get_all_users default_meals else {}
  L56-59 — get_all_users target_ranges else {}
  L125-164 — update_user_profile + update_user_profile_partial body
  L196  — update_user_profile_partial updated_at = CURRENT_TIMESTAMP
  L239-240 — get_user_config age calculation except (ValueError/ImportError)
"""
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def real_um():
    """创建使用临时文件数据库的 UserManager 实例。

    使用 tempfile 而非 :memory:，因为 UserManager 内部多次调用
    sqlite3.connect(self.db_path)，每个 :memory: 连接互不共享。
    """
    fd, path = tempfile.mkstemp(suffix='.db')
    from user_manager import UserManager
    um = UserManager(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS app_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            avatar TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            password_hash TEXT,
            phone TEXT,
            email TEXT
        );
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            name TEXT, birth_year INTEGER, height INTEGER,
            weight INTEGER, gender TEXT,
            default_meals TEXT, target_ranges TEXT, enabled_modules TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            target_weight REAL, birth_month INTEGER, birth_day INTEGER
        )
    ''')
    c.execute("INSERT INTO app_users (username, display_name) VALUES ('test', 'Test')")
    c.execute("INSERT INTO user_profiles (user_id) VALUES (1)")
    conn.commit()
    conn.close()
    yield um, path
    os.close(fd)
    if os.path.exists(path):
        os.unlink(path)


# ============================================================
# get_all_users JSON 字段空值 fallback (L45, L48-59)
# ============================================================

class TestGetAllUsersJsonFallback:
    """get_all_users 中 JSON 字段 None -> 空值兜底"""

    def test_enabled_modules_null(self, real_um):
        um, path = real_um
        conn = sqlite3.connect(path)
        conn.execute("UPDATE user_profiles SET enabled_modules = NULL WHERE user_id = 1")
        conn.commit()
        conn.close()
        users = um.get_all_users()
        assert len(users) >= 1
        assert users[0]['enabled_modules'] == []

    def test_default_meals_null(self, real_um):
        um, path = real_um
        conn = sqlite3.connect(path)
        conn.execute("UPDATE user_profiles SET default_meals = NULL WHERE user_id = 1")
        conn.commit()
        conn.close()
        users = um.get_all_users()
        assert users[0]['default_meals'] == {}

    def test_target_ranges_null(self, real_um):
        um, path = real_um
        conn = sqlite3.connect(path)
        conn.execute("UPDATE user_profiles SET target_ranges = NULL WHERE user_id = 1")
        conn.commit()
        conn.close()
        users = um.get_all_users()
        assert users[0]['target_ranges'] == {}


# ============================================================
# update_user_profile (L125-138)
# ============================================================

class TestUpdateUserProfile:
    """update_user_profile 全量更新"""

    def test_updates_profile(self, real_um):
        um, _ = real_um
        um.update_user_profile(1, {
            'name': 'Updated', 'birth_year': 1990, 'height': 180, 'weight': 80,
            'gender': 'male',
            'default_meals': {'breakfast': {'calories': 400}},
            'target_ranges': {'fasting_min': 4.0},
            'enabled_modules': ['glucose', 'exercise'],
        })
        config = um.get_user_config(1)
        assert config['name'] == 'Updated'
        assert config['height'] == 180
        assert config['default_meals'] == {'breakfast': {'calories': 400}}

    def test_update_empty_fields(self, real_um):
        um, _ = real_um
        um.update_user_profile(1, {})
        # Should not raise


# ============================================================
# update_user_profile_partial (L140-196, han L196 updated_at)
# ============================================================

class TestUpdateUserProfilePartialBranches:
    """update_user_profile_partial 分支覆盖"""

    def test_partial_single_field(self, real_um):
        um, _ = real_um
        um.update_user_profile_partial(1, {'name': 'PartialName'})
        config = um.get_user_config(1)
        assert config['name'] == 'PartialName'

    def test_partial_json_enabled_modules(self, real_um):
        um, path = real_um
        um.update_user_profile_partial(1, {'enabled_modules': ['glucose']})
        conn = sqlite3.connect(path)
        val = conn.execute("SELECT enabled_modules FROM user_profiles WHERE user_id = 1").fetchone()[0]
        conn.close()
        assert 'glucose' in val

    def test_partial_json_fields_default_meals(self, real_um):
        um, _ = real_um
        um.update_user_profile_partial(1, {'default_meals': {'breakfast': {'calories': 300}}})
        config = um.get_user_config(1)
        assert config['default_meals'] == {'breakfast': {'calories': 300}}

    def test_partial_alias_target(self, real_um):
        um, _ = real_um
        um.update_user_profile_partial(1, {'target': {'fasting_min': 4.5}})
        config = um.get_user_config(1)
        assert config['target']['fasting_min'] == 4.5

    def test_partial_target_weight(self, real_um):
        um, _ = real_um
        um.update_user_profile_partial(1, {'target_weight': 72.0})
        config = um.get_user_config(1)
        assert config['target_weight'] == 72.0

    def test_partial_empty_noop(self, real_um):
        um, _ = real_um
        old = um.get_user_config(1)
        um.update_user_profile_partial(1, {})
        assert um.get_user_config(1)['name'] == old['name']

    def test_partial_unknown_key_ignored(self, real_um):
        um, _ = real_um
        um.update_user_profile_partial(1, {'unknown_field': 'val'})
        # Should not raise, no changes


# ============================================================
# get_user_config age exception (L239-240)
# ============================================================

class TestGetUserConfigAgeException:
    """get_user_config age 计算异常分支"""

    def test_invalid_birth_date_uses_year_fallback(self, real_um):
        um, path = real_um
        import datetime
        conn = sqlite3.connect(path)
        conn.execute("UPDATE user_profiles SET birth_year = 2000, birth_month = 13, birth_day = 1 WHERE user_id = 1")
        conn.commit()
        conn.close()
        config = um.get_user_config(1)
        current_year = datetime.datetime.now().year
        assert config['age'] == current_year - 2000
