"""Garmin 同步天数（每用户可自定义）相关测试。

覆盖三处：
1. garmin_service.get_sync_days —— 未设置回落到 30、越界夹取、就近吸附档位
2. user_manager.update_user_profile_partial —— 写入时 clamp 到 30~240
3. sync_all_bindings —— 未显式传 days 时按每个用户自己的设置
"""
import sqlite3
import tempfile

import pytest


def _make_um():
    """建一个带 garmin_sync_days 列的临时库，返回 UserManager。"""
    fd, path = tempfile.mkstemp(suffix='.db')
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE app_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            avatar TEXT,
            is_active BOOLEAN DEFAULT 1,
            phone TEXT,
            email TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE user_profiles (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            birth_year INTEGER,
            birth_month INTEGER,
            birth_day INTEGER,
            height INTEGER,
            weight INTEGER,
            gender TEXT,
            target_weight REAL,
            default_meals TEXT,
            target_ranges TEXT,
            enabled_modules TEXT,
            app_name TEXT,
            app_emoji TEXT,
            app_tagline TEXT,
            persona TEXT,
            garmin_sync_days INTEGER DEFAULT 30,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    c.execute("INSERT INTO app_users (id, username, display_name) VALUES (7, 'u7', '用户七')")
    c.execute("INSERT INTO user_profiles (user_id, name, garmin_sync_days) VALUES (7, '用户七', 30)")
    conn.commit()
    conn.close()

    from user_manager import UserManager
    return UserManager(path), path


class TestGetSyncDays:
    def _patch(self, monkeypatch, cfg):
        import user_manager

        class _FakeUM:
            def __init__(self, *a, **k):
                pass

            def get_user_config(self, uid):
                return cfg

        monkeypatch.setattr(user_manager, 'UserManager', _FakeUM)

    def test_unset_falls_back_to_30(self, monkeypatch):
        self._patch(monkeypatch, {})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 30

    def test_none_falls_back_to_30(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': None})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 30

    def test_snaps_down_to_nearest_option(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': 100})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 90

    def test_snaps_up_to_nearest_option(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': 50})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 60

    def test_out_of_range_high(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': 999})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 240

    def test_out_of_range_low(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': 1})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 30

    def test_bad_value_falls_back(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': 'abc'})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 30

    def test_exact_option_kept(self, monkeypatch):
        self._patch(monkeypatch, {'garmin_sync_days': 180})
        from services.garmin_service import get_sync_days
        assert get_sync_days(1) == 180


class TestUpdateClampsSyncDays:
    def test_clamps_to_range(self):
        um, path = _make_um()
        um.update_user_profile_partial(7, {'garmin_sync_days': 10})
        assert um.get_user(7)['garmin_sync_days'] == 30

        um.update_user_profile_partial(7, {'garmin_sync_days': 999})
        assert um.get_user(7)['garmin_sync_days'] == 240

    def test_accepts_valid_option(self):
        um, _ = _make_um()
        um.update_user_profile_partial(7, {'garmin_sync_days': 90})
        assert um.get_user(7)['garmin_sync_days'] == 90

    def test_ignores_non_numeric(self):
        um, _ = _make_um()
        um.update_user_profile_partial(7, {'garmin_sync_days': 'abc'})
        assert um.get_user(7)['garmin_sync_days'] == 30

    def test_user_config_exposes_value(self):
        um, _ = _make_um()
        um.update_user_profile_partial(7, {'garmin_sync_days': 210})
        assert um.get_user_config(7)['garmin_sync_days'] == 210


class TestSyncAllBindingsUsesPerUserDays:
    def test_uses_each_users_setting(self, monkeypatch):
        from services import garmin_service as g

        monkeypatch.setattr(g, 'list_bindings', lambda: [{'user_id': 1}, {'user_id': 2}])
        monkeypatch.setattr(g, 'get_sync_days', lambda uid: 90 if uid == 1 else 180)
        monkeypatch.setattr(g, '_legacy_user_id', lambda: None)
        called = {}

        def fake_sync(uid, days=30):
            called[uid] = days
            return {'inserted': 0, 'skipped': 0, 'enriched': 0, 'total': 0}

        monkeypatch.setattr(g, 'sync_activities', fake_sync)
        g.sync_all_bindings()
        assert called == {1: 90, 2: 180}

    def test_explicit_days_overrides(self, monkeypatch):
        from services import garmin_service as g

        monkeypatch.setattr(g, 'list_bindings', lambda: [{'user_id': 1}])
        monkeypatch.setattr(g, 'get_sync_days', lambda uid: 90)
        monkeypatch.setattr(g, '_legacy_user_id', lambda: None)
        called = {}

        def fake_sync(uid, days=30):
            called[uid] = days
            return {'inserted': 0, 'skipped': 0, 'enriched': 0, 'total': 0}

        monkeypatch.setattr(g, 'sync_activities', fake_sync)
        g.sync_all_bindings(days=365)
        assert called == {1: 365}
