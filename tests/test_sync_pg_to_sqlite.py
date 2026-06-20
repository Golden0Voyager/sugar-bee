"""测试 PostgreSQL → SQLite 同步脚本。"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

import scripts.sync_pg_to_sqlite as sync_module


@pytest.fixture
def local_db(tmp_path):
    """创建本地 SQLite 测试数据库，包含与脚本匹配的表结构。"""
    db_path = tmp_path / "local.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE app_users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            display_name TEXT,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME
        );
        CREATE TABLE user_profiles (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            height INTEGER,
            weight INTEGER,
            updated_at DATETIME
        );
        CREATE TABLE records (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            type TEXT,
            value REAL,
            timestamp DATETIME,
            created_at DATETIME
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            session_id TEXT,
            role TEXT,
            content TEXT,
            created_at DATETIME
        );
        """
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def sync_state(tmp_path):
    """使用临时状态文件。"""
    state_path = tmp_path / "sync_state.json"
    original_state_file = sync_module.STATE_FILE
    sync_module.STATE_FILE = state_path
    yield state_path
    sync_module.STATE_FILE = original_state_file


@pytest.fixture
def mock_pg(monkeypatch):
    """模拟 psycopg2 连接与游标。"""
    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__ = MagicMock(return_value=fake_cursor)
    fake_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    def fake_connect(url):
        return fake_conn

    monkeypatch.setattr(sync_module.psycopg2, "connect", fake_connect)
    return fake_cursor


def _make_pg_row(row_id: int, table: str) -> dict:
    """构造模拟的 PostgreSQL 行。"""
    if table == "app_users":
        return {
            "id": row_id,
            "username": f"user{row_id}",
            "display_name": f"User {row_id}",
            "is_active": True,
            "created_at": "2026-06-16 10:00:00",
        }
    if table == "user_profiles":
        return {
            "user_id": row_id,
            "name": f"User {row_id}",
            "height": 170,
            "weight": 70,
            "updated_at": "2026-06-16 10:00:00",
        }
    if table == "records":
        return {
            "id": row_id,
            "user_id": 1,
            "type": "空腹",
            "value": 5.5,
            "timestamp": "2026-06-16 08:00:00",
            "created_at": "2026-06-16 08:00:00",
        }
    if table == "chat_messages":
        return {
            "id": row_id,
            "user_id": 1,
            "session_id": "s1",
            "role": "user",
            "content": "hello",
            "created_at": "2026-06-16 08:00:00",
        }
    return {"id": row_id}


def _setup_columns(mock_pg, table: str) -> None:
    """设置模拟的列信息查询。"""

    def columns_for(query, params=None):
        if "information_schema.columns" in query:
            if table == "app_users":
                return [
                    {"column_name": "id"},
                    {"column_name": "username"},
                    {"column_name": "display_name"},
                    {"column_name": "is_active"},
                    {"column_name": "created_at"},
                ]
            if table == "user_profiles":
                return [
                    {"column_name": "user_id"},
                    {"column_name": "name"},
                    {"column_name": "height"},
                    {"column_name": "weight"},
                    {"column_name": "updated_at"},
                ]
            if table == "records":
                return [
                    {"column_name": "id"},
                    {"column_name": "user_id"},
                    {"column_name": "type"},
                    {"column_name": "value"},
                    {"column_name": "timestamp"},
                    {"column_name": "created_at"},
                ]
            if table == "chat_messages":
                return [
                    {"column_name": "id"},
                    {"column_name": "user_id"},
                    {"column_name": "session_id"},
                    {"column_name": "role"},
                    {"column_name": "content"},
                    {"column_name": "created_at"},
                ]
        return []

    mock_pg.execute.side_effect = columns_for
    mock_pg.fetchall.side_effect = lambda: columns_for("information_schema.columns")


class TestSyncPgToSqlite:
    def test_full_sync_app_users(self, local_db, sync_state, mock_pg, monkeypatch):
        monkeypatch.setenv("SUGAR_BEE_DATABASE_URL", "postgresql://fake")
        monkeypatch.setenv("SUGAR_BEE_DB_PATH", local_db)

        # 仅同步 app_users 表以验证全量逻辑
        monkeypatch.setattr(sync_module, "SYNC_CONFIG", {"app_users": ("id", None, True)})

        mock_pg.execute.side_effect = None
        mock_pg.fetchall.return_value = [
            {"column_name": "id"},
            {"column_name": "username"},
            {"column_name": "display_name"},
            {"column_name": "is_active"},
            {"column_name": "created_at"},
        ]

        # 第二次 execute 为 SELECT 数据
        call_count = [0]

        def execute_side_effect(query, params=None):
            call_count[0] += 1

        def fetchall_side_effect():
            if call_count[0] == 1:
                return [
                    {"column_name": "id"},
                    {"column_name": "username"},
                    {"column_name": "display_name"},
                    {"column_name": "is_active"},
                    {"column_name": "created_at"},
                ]
            return [_make_pg_row(1, "app_users"), _make_pg_row(2, "app_users")]

        mock_pg.execute.side_effect = execute_side_effect
        mock_pg.fetchall.side_effect = fetchall_side_effect

        assert sync_module.main() == 0

        conn = sqlite3.connect(local_db)
        rows = conn.execute("SELECT id, username, is_active FROM app_users").fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0] == (1, "user1", 1)
        assert rows[1] == (2, "user2", 1)

    def test_incremental_sync_records(self, local_db, sync_state, mock_pg, monkeypatch):
        monkeypatch.setenv("SUGAR_BEE_DATABASE_URL", "postgresql://fake")
        monkeypatch.setenv("SUGAR_BEE_DB_PATH", local_db)
        monkeypatch.setattr(sync_module, "SYNC_CONFIG", {"records": ("id", "id", False)})

        # 写入初始状态：已同步到 id=5
        sync_module.save_state({"records": 5})

        call_count = [0]

        def execute_side_effect(query, params=None):
            call_count[0] += 1

        def fetchall_side_effect():
            if call_count[0] == 1:
                return [
                    {"column_name": "id"},
                    {"column_name": "user_id"},
                    {"column_name": "type"},
                    {"column_name": "value"},
                    {"column_name": "timestamp"},
                    {"column_name": "created_at"},
                ]
            return [_make_pg_row(6, "records"), _make_pg_row(7, "records")]

        mock_pg.execute.side_effect = execute_side_effect
        mock_pg.fetchall.side_effect = fetchall_side_effect

        assert sync_module.main() == 0

        conn = sqlite3.connect(local_db)
        rows = conn.execute("SELECT id, type, value FROM records ORDER BY id").fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0] == (6, "空腹", 5.5)
        assert rows[1] == (7, "空腹", 5.5)

        state = sync_module.load_state()
        assert state["records"] == 7

    def test_missing_pg_url_exits(self, monkeypatch):
        monkeypatch.delenv("SUGAR_BEE_DATABASE_URL", raising=False)
        with pytest.raises(SystemExit):
            sync_module.get_pg_url()

    def test_normalize_value(self):
        assert sync_module._normalize_value(True) == 1
        assert sync_module._normalize_value(False) == 0
        assert sync_module._normalize_value([1, 2]) == "[1, 2]"
        assert sync_module._normalize_value({"a": 1}) == '{"a": 1}'
        assert sync_module._normalize_value(None) is None
