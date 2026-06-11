"""
mcp_server.py 单元测试 (0% → ~85%)

分层策略:
  1. Pure helpers — 无依赖，直接测试
  2. Inline business logic — mock DB 或真实 temp SQLite
  3. DB-dependent tools — 真实 temp SQLite + monkeypatch
  4. Async MCP tools — mock _api_post
"""
import asyncio
import datetime
import os
import re
import tempfile
from unittest.mock import MagicMock, PropertyMock, patch, AsyncMock

import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mcp_db():
    """创建临时 SQLite 数据库并初始化 mcp_server 需要的表结构。"""
    import sqlite3
    fd, path = tempfile.mkstemp(suffix='.db')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  value REAL, unit TEXT, type TEXT, notes TEXT,
                  timestamp DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  calories INTEGER DEFAULT 0, diet_analysis TEXT DEFAULT '',
                  is_predicted BOOLEAN DEFAULT 0, distance REAL, duration TEXT,
                  heart_rate INTEGER, pace TEXT, cadence INTEGER,
                  systolic_pressure INTEGER, diastolic_pressure INTEGER,
                  pulse_rate INTEGER, spo2 INTEGER, weight REAL, bmi REAL,
                  verified_by_real_id INTEGER, prediction_error REAL,
                  carbs_grams REAL, gi_value REAL, medication_name TEXT,
                  vo2max REAL, max_heart_rate INTEGER, steps INTEGER,
                  max_pace TEXT, user_id INTEGER DEFAULT 1,
                  external_id TEXT, source TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS app_users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  display_name TEXT NOT NULL,
                  avatar TEXT, is_active BOOLEAN DEFAULT 1,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_profiles
                 (user_id INTEGER PRIMARY KEY,
                  name TEXT, birth_year INTEGER, height INTEGER,
                  weight INTEGER, gender TEXT,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute("INSERT INTO app_users (id, username, display_name) VALUES (1, 'test', '测试')")
    c.execute("INSERT INTO user_profiles (user_id, name, height, weight, gender) VALUES (1, '测试', 175, 70, 'male')")
    conn.commit()
    yield path, conn
    conn.close()
    os.close(fd)
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def patch_db(mcp_db):
    """monkeypatch mcp_server.DB_PATH 指向临时数据库路径，返回连接供直接操作。
    
    _db() 每次调用创建新连接，避免 _user_label 关闭连接后影响后续调用。
    """
    path, conn = mcp_db
    with patch('mcp_server.DB_PATH', path):
        yield conn  # can be used for direct INSERT setup


# ============================================================
# Pure helper functions
# ============================================================

class TestNormalizeTimestamp:
    """_normalize_timestamp — 5 条分支"""

    def test_none_returns_now(self):
        from mcp_server import _normalize_timestamp
        result = _normalize_timestamp(None)
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)

    def test_empty_returns_now(self):
        from mcp_server import _normalize_timestamp
        result = _normalize_timestamp("")
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)

    def test_full_format_preserved(self):
        from mcp_server import _normalize_timestamp
        ts = "2026-05-02 06:30:00"
        assert _normalize_timestamp(ts) == ts

    def test_full_format_without_seconds(self):
        from mcp_server import _normalize_timestamp
        result = _normalize_timestamp("2026-05-02 06:30")
        assert result == "2026-05-02 06:30"  # matches \d{2}:\d{2}

    def test_missing_year_prefix_dash(self):
        from mcp_server import _normalize_timestamp
        result = _normalize_timestamp("-05-02 06:30:00")
        year = datetime.datetime.now().year
        assert result == f"{year}-05-02 06:30:00"

    def test_missing_year_no_dash(self):
        from mcp_server import _normalize_timestamp
        result = _normalize_timestamp("05-02 06:30")
        year = datetime.datetime.now().year
        assert result == f"{year}-05-02 06:30"

    def test_unrecognized_format_returns_now(self):
        from mcp_server import _normalize_timestamp
        result = _normalize_timestamp("随便写")
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", result)


class TestApiHeaders:
    def test_returns_correct_headers(self):
        from mcp_server import _api_headers
        with patch('mcp_server.AGENT_API_TOKEN', 'test-token'):
            headers = _api_headers(42)
            assert headers['X-Agent-Token'] == 'test-token'
            assert headers['X-User-Id'] == '42'
            assert headers['Content-Type'] == 'application/json'


class TestIsDupError:
    def test_dup_error_returns_message(self):
        from mcp_server import _is_dup_error
        data = {"status": "error", "error_type": "duplicate", "message": "重复啦"}
        assert _is_dup_error(data) == "重复啦"

    def test_not_dup_returns_none(self):
        from mcp_server import _is_dup_error
        data = {"status": "success", "data": {"id": 1}}
        assert _is_dup_error(data) is None

    def test_wrong_error_type_returns_none(self):
        from mcp_server import _is_dup_error
        data = {"status": "error", "error_type": "validation", "message": "无效"}
        assert _is_dup_error(data) is None


class TestValidateRecordData:
    """_validate_record_data — 6 条分支"""

    def test_valid_no_warnings(self):
        from mcp_server import _validate_record_data
        r = {"value": 5.5}
        assert _validate_record_data(r) == []

    def test_bp_systolic_high(self):
        from mcp_server import _validate_record_data
        r = {"systolic_pressure": 255, "diastolic_pressure": 80}
        warnings = _validate_record_data(r)
        assert any("收缩压" in w for w in warnings)

    def test_bp_diastolic_high(self):
        from mcp_server import _validate_record_data
        r = {"systolic_pressure": 120, "diastolic_pressure": 185}
        warnings = _validate_record_data(r)
        assert any("舒张压" in w for w in warnings)

    def test_bp_systolic_lt_diastolic(self):
        from mcp_server import _validate_record_data
        r = {"systolic_pressure": 100, "diastolic_pressure": 120}
        warnings = _validate_record_data(r)
        assert any("不应小于" in w for w in warnings)

    def test_spo2_out_of_range(self):
        from mcp_server import _validate_record_data
        r = {"spo2": 85}
        warnings = _validate_record_data(r)
        assert any("血氧饱和度" in w for w in warnings)

    def test_pulse_out_of_range(self):
        from mcp_server import _validate_record_data
        r = {"pulse_rate": 25}
        warnings = _validate_record_data(r)
        assert any("脉搏" in w for w in warnings)

    def test_glucose_out_of_range(self):
        from mcp_server import _validate_record_data
        r = {"value": 35.0}
        warnings = _validate_record_data(r)
        assert any("血糖值" in w for w in warnings)

    def test_weight_out_of_range(self):
        from mcp_server import _validate_record_data
        r = {"weight": 350.0}
        warnings = _validate_record_data(r)
        assert any("体重" in w for w in warnings)

    def test_weight_in_range_no_warning(self):
        from mcp_server import _validate_record_data
        r = {"weight": 70.0}
        assert _validate_record_data(r) == []


class TestBpStatus:
    def test_high(self):
        from mcp_server import _bp_status
        assert _bp_status(150, 80) == "偏高"
        assert _bp_status(120, 95) == "偏高"

    def test_warning(self):
        from mcp_server import _bp_status
        assert _bp_status(135, 80) == "警戒"
        assert _bp_status(120, 88) == "警戒"

    def test_normal(self):
        from mcp_server import _bp_status
        assert _bp_status(120, 80) == "正常"
        assert _bp_status(110, 70) == "正常"


class TestValidateBp:
    def test_valid(self):
        from mcp_server import _validate_bp
        assert _validate_bp(120, 80) is None

    def test_systolic_low(self):
        from mcp_server import _validate_bp
        assert "收缩压" in _validate_bp(50, 80)

    def test_systolic_high(self):
        from mcp_server import _validate_bp
        assert "收缩压" in _validate_bp(260, 80)

    def test_diastolic_low(self):
        from mcp_server import _validate_bp
        assert "舒张压" in _validate_bp(120, 30)

    def test_systolic_le_diastolic(self):
        from mcp_server import _validate_bp
        assert "大于" in _validate_bp(100, 120)


class TestValidateGlucose:
    def test_valid(self):
        from mcp_server import _validate_glucose
        assert _validate_glucose(5.5) is None

    def test_too_low(self):
        from mcp_server import _validate_glucose
        assert "超出" in _validate_glucose(0.5)

    def test_too_high(self):
        from mcp_server import _validate_glucose
        assert "超出" in _validate_glucose(35.0)


class TestValidateWeight:
    def test_valid(self):
        from mcp_server import _validate_weight
        assert _validate_weight(70.0) is None

    def test_too_low(self):
        from mcp_server import _validate_weight
        assert "超出" in _validate_weight(15.0)

    def test_too_high(self):
        from mcp_server import _validate_weight
        assert "超出" in _validate_weight(350.0)


class TestValidateHeartRate:
    def test_valid(self):
        from mcp_server import _validate_heart_rate
        assert _validate_heart_rate(72) is None

    def test_too_low(self):
        from mcp_server import _validate_heart_rate
        assert "超出" in _validate_heart_rate(20)

    def test_too_high(self):
        from mcp_server import _validate_heart_rate
        assert "超出" in _validate_heart_rate(250)


class TestHasNumericData:
    """_has_numeric_data — 4 条分支"""

    def test_empty_returns_false(self):
        from mcp_server import _has_numeric_data
        assert _has_numeric_data("") is False
        assert _has_numeric_data(None) is False

    def test_bp_format_returns_true(self):
        from mcp_server import _has_numeric_data
        assert _has_numeric_data("血压 120/80") is True

    def test_value_with_unit_returns_true(self):
        from mcp_server import _has_numeric_data
        assert _has_numeric_data("血糖 5.5 mmol/L") is True

    def test_numeric_sequence_returns_true(self):
        from mcp_server import _has_numeric_data
        assert _has_numeric_data("103/69、64，54.20") is True

    def test_plain_text_returns_false(self):
        from mcp_server import _has_numeric_data
        assert _has_numeric_data("今天感觉不错") is False


class TestFormatParsedPreview:
    """_format_parsed_preview"""

    def test_formats_bp_record(self):
        from mcp_server import _format_parsed_preview
        records = [{
            "type": "血压测量", "systolic_pressure": 120, "diastolic_pressure": 80,
            "pulse_rate": 72, "datetime": "2026-05-02 06:30:00"
        }]
        lines = _format_parsed_preview(records)
        assert len(lines) == 1
        assert "120/80" in lines[0]
        assert "脉搏 72" in lines[0]

    def test_formats_glucose_record(self):
        from mcp_server import _format_parsed_preview
        records = [{
            "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"
        }]
        lines = _format_parsed_preview(records)
        assert len(lines) == 1
        assert "5.5" in lines[0]
        assert "空腹" in lines[0]

    def test_spo2_warning(self):
        from mcp_server import _format_parsed_preview
        records = [{
            "type": "血压测量", "spo2": 85, "systolic_pressure": 120,
            "diastolic_pressure": 80, "datetime": "2026-05-02 06:30:00"
        }]
        lines = _format_parsed_preview(records)
        assert any("⚠️" in l for l in lines)

    def test_multiple_records(self):
        from mcp_server import _format_parsed_preview
        records = [
            {"type": "血压测量", "systolic_pressure": 120, "diastolic_pressure": 80, "datetime": "2026-05-02 06:30"},
            {"type": "体重记录", "weight": 70.5, "datetime": "2026-05-02 06:35"},
        ]
        lines = _format_parsed_preview(records)
        assert len(lines) == 2
        assert "血压 120/80" in lines[0]
        assert "体重 70.5" in lines[1]


# ============================================================
# Inline business logic (needs DB)
# ============================================================

class TestCheckDuplicate:
    """_check_duplicate — 3 条分支 (bp/weight/glucose)"""

    def test_bp_no_duplicate(self, patch_db):
        from mcp_server import _check_duplicate
        conn = patch_db
        assert _check_duplicate(conn, 1, {
            "systolic_pressure": 120, "diastolic_pressure": 80,
            "datetime": "2026-05-02 06:30:00"
        }) is None

    def test_bp_finds_duplicate(self, patch_db):
        from mcp_server import _check_duplicate
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, systolic_pressure, diastolic_pressure, timestamp) "
                  "VALUES (1, 120, 80, '2026-05-02 06:30:00')")
        conn.commit()
        dup = _check_duplicate(conn, 1, {
            "systolic_pressure": 120, "diastolic_pressure": 80,
            "datetime": "2026-05-02 06:30:00"
        })
        assert dup is not None
        assert "血压" in dup

    def test_weight_no_duplicate(self, patch_db):
        from mcp_server import _check_duplicate
        conn = patch_db
        assert _check_duplicate(conn, 1, {
            "weight": 70.0, "datetime": "2026-05-02 06:30:00"
        }) is None

    def test_weight_finds_duplicate(self, patch_db):
        from mcp_server import _check_duplicate
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, weight, timestamp) "
                  "VALUES (1, 70.0, '2026-05-02 06:30:00')")
        conn.commit()
        dup = _check_duplicate(conn, 1, {
            "weight": 70.0, "datetime": "2026-05-02 06:30:00"
        })
        assert dup is not None
        assert "体重" in dup

    def test_glucose_finds_duplicate(self, patch_db):
        from mcp_server import _check_duplicate
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, value, type, timestamp, is_predicted) "
                  "VALUES (1, 5.5, '空腹', '2026-05-02 07:00:00', 0)")
        conn.commit()
        dup = _check_duplicate(conn, 1, {
            "value": 5.5, "type": "空腹", "datetime": "2026-05-02 07:00:00"
        })
        assert dup is not None
        assert "空腹" in dup

    def test_glucose_predicted_skips_check(self, patch_db):
        """预测记录不检测重复。"""
        from mcp_server import _check_duplicate
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, value, type, timestamp, is_predicted) "
                  "VALUES (1, 5.5, '空腹', '2026-05-02 07:00:00', 0)")
        conn.commit()
        dup = _check_duplicate(conn, 1, {
            "value": 5.5, "type": "空腹", "datetime": "2026-05-02 07:00:00",
            "is_predicted": True
        })
        assert dup is None  # predicted records skip duplicate check


class TestInsertRecord:
    """_insert_record"""

    def test_insert_glucose(self, patch_db):
        from mcp_server import _insert_record
        conn = patch_db
        rid = _insert_record(conn, {
            "user_id": 1, "type": "空腹", "value": 5.5, "unit": "mmol/L",
            "timestamp": "2026-05-02 07:00:00"
        })
        assert rid > 0
        c = conn.cursor()
        c.execute("SELECT value, type FROM records WHERE id = ?", (rid,))
        row = c.fetchone()
        assert row["value"] == 5.5
        assert row["type"] == "空腹"

    def test_insert_with_T_timestamp(self, patch_db):
        from mcp_server import _insert_record
        conn = patch_db
        rid = _insert_record(conn, {
            "user_id": 1, "type": "空腹", "value": 5.5,
            "timestamp": "2026-05-02T07:00"
        })
        assert rid > 0

    def test_insert_bp_without_bmi(self, patch_db):
        from mcp_server import _insert_record
        conn = patch_db
        rid = _insert_record(conn, {
            "user_id": 1, "type": "血压测量", "value": 0,
            "systolic_pressure": 120, "diastolic_pressure": 80,
            "timestamp": "2026-05-02 06:30:00"
        })
        assert rid > 0


class TestInlineBatchInsert:
    """_inline_batch_insert — 冲突处理 3 种策略"""

    def test_overwrite_no_conflict(self, patch_db):
        from mcp_server import _inline_batch_insert
        conn = patch_db
        result = _inline_batch_insert(conn, [
            {"user_id": 1, "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00"},
        ], conflict_resolution="overwrite")
        assert len(result["inserted_ids"]) == 1
        assert result["duplicates_skipped"] == []

    def test_skip_duplicate(self, patch_db):
        from mcp_server import _inline_batch_insert
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, value, type, timestamp) "
                  "VALUES (1, 5.5, '空腹', '2026-05-02 07:00:00')")
        conn.commit()
        result = _inline_batch_insert(conn, [
            {"user_id": 1, "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00"},
        ], conflict_resolution="skip")
        assert result["inserted_ids"] == []
        assert len(result["duplicates_skipped"]) == 1

    def test_overwrite_duplicate(self, patch_db):
        from mcp_server import _inline_batch_insert
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, value, type, timestamp) "
                  "VALUES (1, 5.5, '空腹', '2026-05-02 07:00:00')")
        conn.commit()
        result = _inline_batch_insert(conn, [
            {"user_id": 1, "type": "空腹", "value": 6.0, "datetime": "2026-05-02 07:00"},
        ], conflict_resolution="overwrite")
        # Overwrite: delete old + insert new
        assert len(result["inserted_ids"]) == 1
        assert result["duplicates_skipped"] == []
        # Verify old record is gone, new one exists
        c.execute("SELECT value FROM records WHERE type = '空腹'")
        rows = c.fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 6.0

    def test_validation_warnings_collected(self, patch_db):
        from mcp_server import _inline_batch_insert
        conn = patch_db
        result = _inline_batch_insert(conn, [
            {"user_id": 1, "type": "血压测量", "systolic_pressure": 260, "diastolic_pressure": 80,
             "datetime": "2026-05-02 07:00"},
        ], conflict_resolution="overwrite")
        assert len(result["warnings"]) == 1
        assert "收缩压" in result["warnings"][0]


# ============================================================
# DB-dependent tools (real temp SQLite)
# ============================================================

class TestUserLabel:
    def test_returns_label(self, patch_db):
        from mcp_server import _user_label
        with patch('settings.USER_EMOJI_MAP', {1: '🐰'}):
            label = _user_label(1)
            assert "测试" in label

    def test_fallback_when_no_user(self, patch_db):
        from mcp_server import _user_label
        label = _user_label(999)
        assert "用户999" in label


class TestUndoLastRecord:
    def test_no_records(self, patch_db):
        from mcp_server import undo_last_record
        result = asyncio.run(undo_last_record(1))
        assert "没有任何记录" in result

    def test_deletes_last_record(self, patch_db):
        from mcp_server import undo_last_record
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, type, value, timestamp) "
                  "VALUES (1, '空腹', 5.5, '2026-05-02 07:00:00')")
        conn.commit()
        result = asyncio.run(undo_last_record(1))
        assert "已删除" in result
        assert "5.5" in result
        c.execute("SELECT COUNT(*) FROM records")
        assert c.fetchone()[0] == 0

    def test_deletes_bp_record(self, patch_db):
        from mcp_server import undo_last_record
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, type, value, systolic_pressure, "
                  "diastolic_pressure, pulse_rate, timestamp) "
                  "VALUES (1, '血压测量', 0, 120, 80, 72, '2026-05-02 06:30:00')")
        conn.commit()
        result = asyncio.run(undo_last_record(1))
        assert "血压 120/80" in result
        assert "脉搏72" in result

    def test_deletes_weight_record(self, patch_db):
        from mcp_server import undo_last_record
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, type, weight, timestamp) "
                  "VALUES (1, '体重记录', 70.5, '2026-05-02 07:00:00')")
        conn.commit()
        result = asyncio.run(undo_last_record(1))
        assert "体重 70.5" in result


class TestTodaySummary:
    def test_no_records(self, patch_db):
        from mcp_server import today_summary
        result = asyncio.run(today_summary(1))
        assert "今日暂无记录" in result

    def test_shows_summary(self, patch_db):
        from mcp_server import today_summary
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, value, timestamp) "
                  "VALUES (1, '空腹', 5.5, ?)", (today,))
        conn.commit()
        with patch('settings.USER_EMOJI_MAP', {1: '🐰'}):
            result = asyncio.run(today_summary(1))
        assert "5.5" in result
        assert "1 条记录" in result


class TestListTodayRecords:
    def test_no_records(self, patch_db):
        from mcp_server import list_today_records
        result = asyncio.run(list_today_records(1))
        assert "今日暂无记录" in result

    def test_shows_records(self, patch_db):
        from mcp_server import list_today_records
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, value, timestamp) "
                  "VALUES (1, '空腹', 5.5, ?)", (today,))
        conn.commit()
        result = asyncio.run(list_today_records(1))
        assert "空腹" in result
        assert "5.5" in result


class TestGetUserInfo:
    def test_user_not_found(self, patch_db):
        from mcp_server import get_user_info
        result = asyncio.run(get_user_info(999))
        assert "不存在" in result

    def test_returns_info(self, patch_db):
        from mcp_server import get_user_info
        result = asyncio.run(get_user_info(1))
        assert "测试" in result
        assert "175" in result
        assert "male" in result


# ============================================================
# Async MCP tools (mock _api_post)
# ============================================================

@pytest.fixture
def mock_api():
    """mock mcp_server._api_post 返回成功响应"""
    with patch('mcp_server._api_post', new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success", "data": {"id": 42, "bmi": 22.5}}
        yield mock


class TestRecordBloodPressure:
    @pytest.mark.asyncio
    async def test_validation_error(self):
        from mcp_server import record_blood_pressure
        result = await record_blood_pressure(1, systolic=50, diastolic=80)
        assert "❌" in result
        assert "收缩压" in result

    @pytest.mark.asyncio
    async def test_pulse_validation_error(self):
        from mcp_server import record_blood_pressure
        result = await record_blood_pressure(1, systolic=120, diastolic=80, pulse_rate=250)
        assert "❌" in result
        assert "心率" in result

    @pytest.mark.asyncio
    async def test_success(self, mock_api):
        from mcp_server import record_blood_pressure
        result = await record_blood_pressure(1, systolic=120, diastolic=80, pulse_rate=72)
        assert "✅" in result
        assert "120/80" in result

    @pytest.mark.asyncio
    async def test_duplicate(self, mock_api):
        mock_api.return_value = {"status": "error", "error_type": "duplicate", "message": "3 分钟内已有相同血压记录"}
        from mcp_server import record_blood_pressure
        result = await record_blood_pressure(1, systolic=120, diastolic=80)
        assert "⚠️" in result


class TestRecordWeight:
    @pytest.mark.asyncio
    async def test_validation_error(self):
        from mcp_server import record_weight
        result = await record_weight(1, weight=15.0)
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_success(self, mock_api):
        from mcp_server import record_weight
        result = await record_weight(1, weight=70.0)
        assert "✅" in result
        assert "70.0" in result

    @pytest.mark.asyncio
    async def test_duplicate(self, mock_api):
        mock_api.return_value = {"status": "error", "error_type": "duplicate", "message": "重复"}
        from mcp_server import record_weight
        result = await record_weight(1, weight=70.0)
        assert "⚠️" in result


class TestRecordGlucose:
    @pytest.mark.asyncio
    async def test_validation_error(self):
        from mcp_server import record_glucose
        result = await record_glucose(1, value=0.5, record_type="空腹")
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_success(self, mock_api):
        from mcp_server import record_glucose
        result = await record_glucose(1, value=5.5, record_type="空腹")
        assert "✅" in result
        assert "5.5" in result

    @pytest.mark.asyncio
    async def test_duplicate(self, mock_api):
        mock_api.return_value = {"status": "error", "error_type": "duplicate", "message": "重复"}
        from mcp_server import record_glucose
        result = await record_glucose(1, value=5.5, record_type="空腹")
        assert "⚠️" in result


class TestRecordExercise:
    @pytest.mark.asyncio
    async def test_hr_validation_error(self):
        from mcp_server import record_exercise
        result = await record_exercise(1, exercise_type="跑步", distance=5.0, heart_rate=250)
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_success_minimal(self, mock_api, patch_db):
        from mcp_server import record_exercise
        result = await record_exercise(1, exercise_type="跑步", distance=5.0)
        assert "✅" in result
        assert "跑步" in result
        assert "5.0" in result

    @pytest.mark.asyncio
    async def test_success_full(self, mock_api, patch_db):
        from mcp_server import record_exercise
        result = await record_exercise(1, exercise_type="跑步", distance=5.0,
                                       duration="30:00", pace="6:00", heart_rate=145,
                                       steps=6000, calories=300)
        assert "✅" in result
        assert "心率 145" in result
        assert "6000步" in result


class TestBatchRecord:
    @pytest.mark.asyncio
    async def test_empty_records(self, patch_db):
        from mcp_server import batch_record
        result = await batch_record(1, records=[])
        assert "未提供任何记录" in result

    @pytest.mark.asyncio
    async def test_param_validation_error(self, patch_db):
        from mcp_server import batch_record
        result = await batch_record(1, records=[
            {"type": "", "systolic_pressure": 260, "diastolic_pressure": 80}
        ])
        assert "❌" in result
        assert "缺少 type" in result

    @pytest.mark.asyncio
    async def test_batch_insert_success(self, patch_db):
        from mcp_server import batch_record
        with patch('settings.USER_EMOJI_MAP', {1: '🐰'}):
            result = await batch_record(1, records=[
                {"type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00"},
                {"type": "血压测量", "systolic_pressure": 120, "diastolic_pressure": 80,
                 "datetime": "2026-05-02 06:30"},
            ])
        assert "✅" in result
        assert "2 条" in result
        assert "血糖 5.5" in result
        assert "血压 120/80" in result

    @pytest.mark.asyncio
    async def test_bp_param_validation(self, patch_db):
        from mcp_server import batch_record
        result = await batch_record(1, records=[
            {"type": "血压测量", "systolic_pressure": 50, "diastolic_pressure": 80}
        ])
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_db_exception_rollback(self, patch_db):
        """验证 DB 异常时 rollback 且返回错误消息。"""
        from mcp_server import batch_record
        bad_conn = MagicMock()
        bad_conn.cursor.side_effect = Exception("corrupt db")
        with patch('mcp_server._db', return_value=bad_conn):
            result = await batch_record(1, records=[
                {"type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00"}
            ])
        assert "❌" in result
        assert "corrupt db" in result
        bad_conn.rollback.assert_called_once()
        bad_conn.close.assert_called_once()


class TestParseAndRecord:
    @pytest.mark.asyncio
    async def test_parse_api_error(self):
        from mcp_server import parse_and_record
        with patch('mcp_server._api_post', new_callable=AsyncMock) as mock:
            mock.return_value = {"status": "error", "message": "AI 解析失败"}
            result = await parse_and_record(1, "爸爸空腹血糖 6.2")
        assert "❌" in result
        assert "解析失败" in result

    @pytest.mark.asyncio
    async def test_no_records_parsed(self):
        from mcp_server import parse_and_record
        with patch('mcp_server._api_post', new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = await parse_and_record(1, "今天天气不错")
        assert "未解析到有效记录" in result

    @pytest.mark.asyncio
    async def test_no_records_with_hint(self):
        from mcp_server import parse_and_record
        with patch('mcp_server._api_post', new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = await parse_and_record(1, "120/80")
        assert "💡" in result

    @pytest.mark.asyncio
    async def test_success_flow(self, patch_db):
        from mcp_server import parse_and_record
        records_data = [
            {"type": "空腹", "value": 5.5, "unit": "mmol/L", "datetime": "2026-05-02 07:00"}
        ]
        with patch('mcp_server._api_post', new_callable=AsyncMock) as mock:
            mock.side_effect = [
                records_data,  # First call: /parse_ai returns parsed records
                {"status": "success", "data": {"id": 42}}  # Second call: /batch_add returns success
            ]
            result = await parse_and_record(1, "爸爸空腹血糖 5.5")
        assert "成功解析" in result
        assert "1 条" in result


class TestBatchParseAndRecord:
    """batch_parse_and_record — 正则解析 + 内联写入"""

    @pytest.mark.asyncio
    async def test_regex_fast_path(self, patch_db):
        from mcp_server import batch_parse_and_record, _try_regex_parse
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Patch EMOJI_USER_MAP in settings (imported inside _try_regex_parse)
        # and mock _api_post to avoid HTTP calls on the fallback path
        with patch('mcp_server._normalize_timestamp', return_value=ts), \
             patch('mcp_server._api_post', new_callable=AsyncMock) as mock_api, \
             patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            mock_api.return_value = []
            result = await batch_parse_and_record("🐰103/69")
        # Regex should parse BP '103/69' -> systolic=103, diastolic=69
        assert "✅" in result
        assert "103/69" in result
