"""针对本次重构修复的回归测试。

覆盖两处会真实丢数据 / 误判的缺陷：
1. overwrite 策略原先按「同一分钟」删除，会把同分钟的其它维度记录一起删掉。
2. 重复检测原先用 SQLite 专有的 datetime()/date() 函数，PostgreSQL 模式下报错。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    cur = c.cursor()
    cur.execute(
        """CREATE TABLE records
           (id INTEGER PRIMARY KEY AUTOINCREMENT,
            value REAL, unit TEXT, type TEXT, notes TEXT, timestamp DATETIME,
            calories INTEGER DEFAULT 0, diet_analysis TEXT DEFAULT '',
            is_predicted BOOLEAN DEFAULT 0, distance REAL, duration TEXT,
            heart_rate INTEGER, max_heart_rate INTEGER, pace TEXT, max_pace TEXT,
            cadence INTEGER, systolic_pressure INTEGER, diastolic_pressure INTEGER,
            pulse_rate INTEGER, spo2 INTEGER, weight REAL, bmi REAL,
            medication_name TEXT, vo2max REAL, steps INTEGER, carbs_grams REAL,
            gi_value REAL, user_id INTEGER DEFAULT 1)"""
    )
    cur.execute(
        """CREATE TABLE user_profiles
           (user_id INTEGER PRIMARY KEY, name TEXT, height INTEGER,
            weight INTEGER, gender TEXT)"""
    )
    cur.execute("INSERT INTO user_profiles (user_id, height, weight) VALUES (1, 175, 70)")
    c.commit()
    yield c
    c.close()
    os.close(fd)
    if os.path.exists(path):
        os.unlink(path)


def _insert(conn, **fields):
    fields.setdefault("user_id", 1)
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    conn.execute(f"INSERT INTO records ({cols}) VALUES ({marks})", tuple(fields.values()))
    conn.commit()


class TestOverwriteScope:
    def test_weight_overwrite_keeps_glucose_same_minute(self, conn):
        """补录体重不应删掉同一分钟的血糖。"""
        from mcp_adapter.servers.writer import _inline_batch_insert

        _insert(conn, type="空腹", value=5.5, timestamp="2026-05-02 07:00:00")
        _insert(conn, type="体重记录", weight=70.0, timestamp="2026-05-02 07:00:00")

        _inline_batch_insert(
            conn,
            [{"user_id": 1, "type": "体重记录", "weight": 70.0, "datetime": "2026-05-02 07:00"}],
            conflict_resolution="overwrite",
        )

        rows = conn.execute("SELECT type, value, weight FROM records ORDER BY id").fetchall()
        assert len(rows) == 2
        glucose = [r for r in rows if r["type"] == "空腹"]
        assert len(glucose) == 1
        assert glucose[0]["value"] == 5.5

    def test_glucose_overwrite_keeps_other_glucose_type(self, conn):
        """同为血糖但测量类型不同，不应互相覆盖。"""
        from mcp_adapter.servers.writer import _inline_batch_insert

        _insert(conn, type="空腹", value=5.5, timestamp="2026-05-02 07:00:00")
        _insert(conn, type="睡前", value=6.6, timestamp="2026-05-02 07:00:00")

        _inline_batch_insert(
            conn,
            [{"user_id": 1, "type": "空腹", "value": 6.0, "datetime": "2026-05-02 07:00"}],
            conflict_resolution="overwrite",
        )

        rows = conn.execute("SELECT type, value FROM records ORDER BY id").fetchall()
        assert len(rows) == 2
        by_type = {r["type"]: r["value"] for r in rows}
        assert by_type == {"空腹": 6.0, "睡前": 6.6}

    def test_same_kind_still_replaces(self, conn):
        """同类记录仍然是覆盖语义，不产生重复行。"""
        from mcp_adapter.servers.writer import _inline_batch_insert

        _insert(conn, type="空腹", value=5.5, timestamp="2026-05-02 07:00:00")

        _inline_batch_insert(
            conn,
            [{"user_id": 1, "type": "空腹", "value": 6.0, "datetime": "2026-05-02 07:00"}],
            conflict_resolution="overwrite",
        )

        rows = conn.execute("SELECT value FROM records WHERE type = '空腹'").fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 6.0

    def test_overwrite_scope_by_kind(self):
        from mcp_adapter.servers.writer import _overwrite_scope

        assert _overwrite_scope({"systolic_pressure": 120}) == (
            " AND systolic_pressure IS NOT NULL",
            (),
        )
        assert _overwrite_scope({"weight": 70}) == (" AND weight IS NOT NULL", ())
        sql, params = _overwrite_scope({"medication_name": "二甲双胍"})
        assert "medication_name" in sql
        assert params == ("二甲双胍",)
        sql, params = _overwrite_scope({"type": "空腹", "value": 5.5})
        assert "type = ?" in sql
        assert params == ("空腹",)


class TestDuplicateDetection:
    def test_bp_duplicate_within_three_minutes(self, conn):
        from mcp_adapter.servers.writer import _check_duplicate

        _insert(conn, type="血压测量", systolic_pressure=120, diastolic_pressure=80,
                timestamp="2026-05-02 07:00:00")
        dup = _check_duplicate(
            conn, 1,
            {"systolic_pressure": 120, "diastolic_pressure": 80,
             "datetime": "2026-05-02 07:03:00"},
        )
        assert dup is not None and "血压" in dup

    def test_bp_outside_window_is_not_duplicate(self, conn):
        from mcp_adapter.servers.writer import _check_duplicate

        _insert(conn, type="血压测量", systolic_pressure=120, diastolic_pressure=80,
                timestamp="2026-05-02 07:00:00")
        dup = _check_duplicate(
            conn, 1,
            {"systolic_pressure": 120, "diastolic_pressure": 80,
             "datetime": "2026-05-02 07:05:00"},
        )
        assert dup is None

    def test_glucose_duplicate_is_day_scoped(self, conn):
        from mcp_adapter.servers.writer import _check_duplicate

        _insert(conn, type="空腹", value=5.5, timestamp="2026-05-02 23:50:00")

        same_day = _check_duplicate(
            conn, 1, {"type": "空腹", "value": 5.6, "datetime": "2026-05-02 00:10:00"}
        )
        next_day = _check_duplicate(
            conn, 1, {"type": "空腹", "value": 5.6, "datetime": "2026-05-03 00:10:00"}
        )
        assert same_day is not None
        assert next_day is None
