"""
mcp_adapter/server.py 补充测试（覆盖剩余未覆盖行）

目标：将 server.py 覆盖率从 ~82% 提升到 >=95%。
策略：针对 missing lines 列表逐块覆盖，复用现有 fixture 风格。
"""
import asyncio
import datetime
import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def mcp_db():
    """创建临时 SQLite 数据库并初始化 mcp_adapter.server 需要的表结构。"""
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
    """monkeypatch mcp_adapter.server.DB_PATH 指向临时数据库路径，返回连接供直接操作。"""
    path, conn = mcp_db
    with patch('mcp_adapter.server.DB_PATH', path):
        yield conn


@pytest.fixture
def mock_api():
    """mock mcp_adapter.server._api_post 返回成功响应"""
    with patch('mcp_adapter.server._api_post', new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "success", "data": {"id": 42, "bmi": 22.5}}
        yield mock


# ============================================================
# _api_post exception branches (lines 85-116)
# ============================================================

class TestApiPostExceptions:
    """覆盖 _api_post 中 4 种异常分支 + 409 成功分支。"""

    @pytest.mark.asyncio
    async def test_connect_error(self):
        from mcp_adapter.server import _api_post
        with patch('httpx.AsyncClient.post', side_effect=httpx.ConnectError("Connection refused")):
            result = await _api_post(1, "/add", {})
        assert result["status"] == "error"
        assert result["error_type"] == "connection_error"
        assert "无法连接" in result["message"]

    @pytest.mark.asyncio
    async def test_timeout_exception(self):
        from mcp_adapter.server import _api_post
        with patch('httpx.AsyncClient.post', side_effect=httpx.TimeoutException("Timed out")):
            result = await _api_post(1, "/add", {})
        assert result["status"] == "error"
        assert result["error_type"] == "timeout"
        assert "超时" in result["message"]

    @pytest.mark.asyncio
    async def test_http_status_error(self):
        from mcp_adapter.server import _api_post
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        exc = httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)
        with patch('httpx.AsyncClient.post', side_effect=exc):
            result = await _api_post(1, "/add", {})
        assert result["status"] == "error"
        assert result["error_type"] == "http_error"
        assert "500" in result["message"]

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        from mcp_adapter.server import _api_post
        with patch('httpx.AsyncClient.post', side_effect=ValueError("boom")):
            result = await _api_post(1, "/add", {})
        assert result["status"] == "error"
        assert result["error_type"] == "unknown"
        assert "boom" in result["message"]

    @pytest.mark.asyncio
    async def test_409_response(self):
        from mcp_adapter.server import _api_post
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {"status": "error", "error_type": "duplicate", "message": "重复"}
        with patch('httpx.AsyncClient.post', return_value=mock_response):
            result = await _api_post(1, "/add", {})
        assert result["status"] == "error"
        assert result["error_type"] == "duplicate"

    @pytest.mark.asyncio
    async def test_success_response(self):
        from mcp_adapter.server import _api_post
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "data": {"id": 1}}
        with patch('httpx.AsyncClient.post', return_value=mock_response):
            result = await _api_post(1, "/add", {})
        assert result["status"] == "success"


# ============================================================
# _check_duplicate missing timestamp fallback (line 176)
# ============================================================

class TestCheckDuplicateFallback:
    def test_missing_timestamp_fallback(self, patch_db):
        from mcp_adapter.server import _check_duplicate
        conn = patch_db
        # 不提供 datetime/timestamp，触发 fallback 到当前时间
        result = _check_duplicate(conn, 1, {"systolic_pressure": 120, "diastolic_pressure": 80})
        assert result is None  # 无重复


# ============================================================
# _calculate_and_set_bmi exception (lines 223-228)
# ============================================================

class TestCalculateAndSetBmiException:
    def test_import_error_returns_none(self, patch_db):
        from mcp_adapter.server import _calculate_and_set_bmi
        conn = patch_db
        # 让 settings.calculate_bmi 抛出异常
        with patch('settings.calculate_bmi', side_effect=ImportError("no module")):
            result = _calculate_and_set_bmi(conn, 1, 70.0)
        assert result is None


# ============================================================
# _update_profile_weight exception (lines 233-241)
# ============================================================

class TestUpdateProfileWeightException:
    def test_exception_silently_ignored(self, patch_db):
        from mcp_adapter.server import _update_profile_weight
        # 用一个 MagicMock 替代 conn，让 execute 抛出异常，覆盖 try/except 静默忽略分支
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.execute.side_effect = sqlite3.OperationalError("readonly")
        # 不应抛异常
        _update_profile_weight(mock_conn, 1, 70.0)
        mock_conn.cursor.return_value.execute.assert_called_once()


# ============================================================
# _insert_record missing timestamp (line 255) + bmi path (line 266)
# ============================================================

class TestInsertRecordEdgeCases:
    def test_missing_timestamp(self, patch_db):
        from mcp_adapter.server import _insert_record
        conn = patch_db
        rid = _insert_record(conn, {
            "user_id": 1, "type": "空腹", "value": 5.5,
            # 不提供 timestamp / datetime
        })
        assert rid > 0
        c = conn.cursor()
        c.execute("SELECT timestamp FROM records WHERE id = ?", (rid,))
        ts = c.fetchone()["timestamp"]
        assert ts is not None

    def test_bmi_calculation_path(self, patch_db):
        from mcp_adapter.server import _insert_record
        conn = patch_db
        # 提供 weight 但不提供 bmi，触发 _calculate_and_set_bmi
        with patch('settings.calculate_bmi', return_value=22.8) as mock_bmi:
            rid = _insert_record(conn, {
                "user_id": 1, "type": "体重记录", "value": 0, "weight": 70.0,
                "timestamp": "2026-05-02 07:00:00"
            })
        assert rid > 0
        mock_bmi.assert_called_once()
        c = conn.cursor()
        c.execute("SELECT bmi FROM records WHERE id = ?", (rid,))
        assert c.fetchone()["bmi"] == 22.8


# ============================================================
# _try_regex_parse edge cases (lines 305, 316, 326, 349-351, 355-357, 360-362, 376, 380, 390)
# ============================================================

class TestTryRegexParseEdgeCases:
    """覆盖 _try_regex_parse 中各种边缘分支。"""

    def test_empty_text_returns_none(self):
        from mcp_adapter.server import _try_regex_parse
        assert _try_regex_parse("") is None
        assert _try_regex_parse(None) is None

    def test_no_emoji_no_match_returns_none(self):
        from mcp_adapter.server import _try_regex_parse
        # 纯文本无 emoji，应返回 None
        assert _try_regex_parse("120/80 70 65.5") is None

    def test_unknown_emoji_skipped(self):
        from mcp_adapter.server import _try_regex_parse
        # 使用 settings 中不存在的 emoji
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🦊120/80")
        # 🦊 不在 EMOJI_USER_MAP 中，segment 被跳过，无记录 → 整体返回 None
        assert result is None

    def test_emoji_user_id_none_skipped(self):
        from mcp_adapter.server import _try_regex_parse
        # 当 EMOJI_USER_MAP 包含该 emoji 但值为 None 时触发 line 326
        with patch('settings.EMOJI_USER_MAP', {'🐰': None}):
            result = _try_regex_parse("🐰120/80")
        # user_id is None → continue → 无记录 → 返回 None
        assert result is None

    def test_pulse_in_range(self):
        from mcp_adapter.server import _try_regex_parse
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🐰120/80、72")
        assert result is not None
        assert any(r.get("pulse_rate") == 72 for r in result)

    def test_weight_decimal_priority(self):
        from mcp_adapter.server import _try_regex_parse
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🐰120/80、72，65.50")
        assert result is not None
        weight_records = [r for r in result if r.get("weight")]
        assert len(weight_records) == 1
        assert weight_records[0]["weight"] == 65.5

    def test_weight_fallback_to_float(self):
        from mcp_adapter.server import _try_regex_parse
        # 不带小数点但符合体重范围，且不等于脉搏
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🐰120/80、72，80")
        assert result is not None
        weight_records = [r for r in result if r.get("weight")]
        assert len(weight_records) == 1
        assert weight_records[0]["weight"] == 80.0

    def test_no_records_for_user_returns_none(self):
        from mcp_adapter.server import _try_regex_parse
        # emoji 存在但 segment 中没有可识别的数字
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🐰abc")
        assert result is None

    def test_bp_with_pulse_record(self):
        from mcp_adapter.server import _try_regex_parse
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🐰120/80、72")
        assert result is not None
        bp_records = [r for r in result if r.get("systolic_pressure")]
        assert len(bp_records) == 1
        assert bp_records[0].get("pulse_rate") == 72

    def test_weight_only_record(self):
        from mcp_adapter.server import _try_regex_parse
        with patch('settings.EMOJI_USER_MAP', {'🐰': 1}):
            result = _try_regex_parse("🐰65.50")
        assert result is not None
        weight_records = [r for r in result if r.get("weight")]
        assert len(weight_records) == 1


# ============================================================
# _inline_batch_insert missing timestamp (line 412), ask (425-426), bmi (443-445), update profile (454)
# ============================================================

class TestInlineBatchInsertEdgeCases:
    def test_missing_timestamp_fallback(self, patch_db):
        from mcp_adapter.server import _inline_batch_insert
        conn = patch_db
        result = _inline_batch_insert(conn, [
            {"user_id": 1, "type": "空腹", "value": 5.5},
        ], conflict_resolution="overwrite")
        assert len(result["inserted_ids"]) == 1

    def test_conflict_resolution_ask(self, patch_db):
        from mcp_adapter.server import _inline_batch_insert
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, value, type, timestamp) "
                  "VALUES (1, 5.5, '空腹', '2026-05-02 07:00:00')")
        conn.commit()
        result = _inline_batch_insert(conn, [
            {"user_id": 1, "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"},
        ], conflict_resolution="ask")
        assert len(result["inserted_ids"]) == 0
        assert len(result["duplicates_skipped"]) == 1

    def test_bmi_calculation_in_batch(self, patch_db):
        from mcp_adapter.server import _inline_batch_insert
        conn = patch_db
        with patch('settings.calculate_bmi', return_value=23.1):
            result = _inline_batch_insert(conn, [
                {"user_id": 1, "type": "体重记录", "value": 0, "weight": 72.0,
                 "datetime": "2026-05-02 07:00:00"},
            ], conflict_resolution="overwrite")
        assert len(result["inserted_ids"]) == 1
        c = conn.cursor()
        c.execute("SELECT bmi FROM records WHERE id = ?", (result["inserted_ids"][0],))
        assert c.fetchone()["bmi"] == 23.1

    def test_update_profile_weight_in_batch(self, patch_db):
        from mcp_adapter.server import _inline_batch_insert
        conn = patch_db
        with patch('mcp_adapter.server._update_profile_weight') as mock_upd:
            result = _inline_batch_insert(conn, [
                {"user_id": 1, "type": "体重记录", "value": 0, "weight": 72.0,
                 "datetime": "2026-05-02 07:00:00"},
            ], conflict_resolution="overwrite")
        assert len(result["inserted_ids"]) == 1
        mock_upd.assert_called_once_with(conn, 1, 72.0)


# ============================================================
# record_blood_pressure notes (line 549)
# ============================================================

class TestRecordBloodPressureNotes:
    @pytest.mark.asyncio
    async def test_with_notes(self, mock_api):
        from mcp_adapter.server import record_blood_pressure
        result = await record_blood_pressure(1, systolic=120, diastolic=80, notes="晨起测量")
        assert "✅" in result


# ============================================================
# record_weight notes (line 584)
# ============================================================

class TestRecordWeightNotes:
    @pytest.mark.asyncio
    async def test_with_notes(self, mock_api):
        from mcp_adapter.server import record_weight
        result = await record_weight(1, weight=70.0, notes="空腹")
        assert "✅" in result


# ============================================================
# record_glucose notes (line 620)
# ============================================================

class TestRecordGlucoseNotes:
    @pytest.mark.asyncio
    async def test_with_notes(self, mock_api):
        from mcp_adapter.server import record_glucose
        result = await record_glucose(1, value=5.5, record_type="空腹", notes="早餐前")
        assert "✅" in result


# ============================================================
# record_exercise notes (line 673) + duplicate (line 678)
# ============================================================

class TestRecordExerciseEdgeCases:
    @pytest.mark.asyncio
    async def test_with_notes(self, mock_api):
        from mcp_adapter.server import record_exercise
        result = await record_exercise(1, exercise_type="跑步", distance=5.0, notes="公园跑")
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_duplicate(self, mock_api):
        mock_api.return_value = {"status": "error", "error_type": "duplicate", "message": "重复运动"}
        from mcp_adapter.server import record_exercise
        result = await record_exercise(1, exercise_type="跑步", distance=5.0)
        assert "⚠️" in result


# ============================================================
# _has_numeric_data branches (line 710)
# ============================================================

class TestHasNumericDataBranches:
    """补充覆盖 _has_numeric_data 剩余分支。"""

    def test_plain_text_no_numbers(self):
        from mcp_adapter.server import _has_numeric_data
        assert _has_numeric_data("今天天气不错") is False

    def test_numeric_sequence_branch(self):
        from mcp_adapter.server import _has_numeric_data
        # 覆盖 \d{2,3}\s*[，,、]\s*\d{2,3} 分支，且不满足前面的血压/单位分支
        assert _has_numeric_data("103、64") is True

    def test_bp_format_branch(self):
        from mcp_adapter.server import _has_numeric_data
        # 覆盖 \d{2,3}\s*/\s*\d{2,3} 分支
        assert _has_numeric_data("血压 120/80") is True

    def test_unit_branch(self):
        from mcp_adapter.server import _has_numeric_data
        # 覆盖 \d+\.?\d*\s*(mmol/L|mg/dL|kg|公斤|kcal|km) 分支
        assert _has_numeric_data("血糖 5.5 mmol/L") is True


# ============================================================
# _format_parsed_preview medication_name (line 731)
# ============================================================

class TestFormatParsedPreviewMedication:
    def test_medication_name(self):
        from mcp_adapter.server import _format_parsed_preview
        records = [{
            "type": "用药记录", "medication_name": "二甲双胍", "datetime": "2026-05-02 08:00:00"
        }]
        lines = _format_parsed_preview(records)
        assert "用药 二甲双胍" in lines[0]


# ============================================================
# parse_and_record duplicate (line 770) + warnings (line 775)
# ============================================================

class TestParseAndRecordEdgeCases:
    @pytest.mark.asyncio
    async def test_duplicate_response(self, patch_db):
        from mcp_adapter.server import parse_and_record
        records_data = [
            {"type": "空腹", "value": 5.5, "unit": "mmol/L", "datetime": "2026-05-02 07:00"}
        ]
        with patch('mcp_adapter.server._api_post', new_callable=AsyncMock) as mock:
            mock.side_effect = [
                records_data,
                {"status": "error", "error_type": "duplicate", "message": "重复啦"}
            ]
            result = await parse_and_record(1, "爸爸空腹血糖 5.5")
        assert "⚠️" in result
        assert "重复啦" in result

    @pytest.mark.asyncio
    async def test_warnings_in_response(self, patch_db):
        from mcp_adapter.server import parse_and_record
        records_data = [
            {"type": "空腹", "value": 5.5, "unit": "mmol/L", "datetime": "2026-05-02 07:00"}
        ]
        with patch('mcp_adapter.server._api_post', new_callable=AsyncMock) as mock:
            mock.side_effect = [
                records_data,
                {"status": "success", "data": {"warnings": ["血糖值偏低"]}}
            ]
            result = await parse_and_record(1, "爸爸空腹血糖 5.5")
        assert "⚠️ 数据警告" in result
        assert "血糖值偏低" in result


# ============================================================
# batch_record param validation: heart rate (815-817), glucose (822), weight (825-827)
# ============================================================

class TestBatchRecordParamValidation:
    @pytest.mark.asyncio
    async def test_heart_rate_validation(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "血压测量", "systolic_pressure": 120, "diastolic_pressure": 80, "pulse_rate": 250}
        ])
        assert "❌" in result
        assert "心率" in result

    @pytest.mark.asyncio
    async def test_glucose_validation(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "空腹", "value": 35.0}
        ])
        assert "❌" in result
        assert "血糖值" in result

    @pytest.mark.asyncio
    async def test_weight_validation(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "体重记录", "weight": 350.0}
        ])
        assert "❌" in result
        assert "体重" in result


# ============================================================
# batch_record timestamp cleanup (line 837)
# ============================================================

class TestBatchRecordTimestampCleanup:
    @pytest.mark.asyncio
    async def test_timestamp_deleted(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "空腹", "value": 5.5, "timestamp": "2026-05-02 07:00", "datetime": "2026-05-02 07:00"}
        ])
        assert "✅" in result


# ============================================================
# batch_record duplicate/warning output (lines 852, 854-856)
# ============================================================

class TestBatchRecordWarningOutput:
    """覆盖 batch_record 中 skip/warning 输出分支（lines 852, 854-856）。"""

    @pytest.mark.asyncio
    async def test_skip_output(self, patch_db):
        from mcp_adapter.server import batch_record
        with patch('mcp_adapter.server._inline_batch_insert') as mock_inline:
            mock_inline.return_value = {
                "inserted_ids": [1],
                "warnings": [],
                "duplicates_skipped": ["重复"]
            }
            result = await batch_record(1, records=[
                {"type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"}
            ])
        assert "⚠️" in result
        assert "跳过" in result

    @pytest.mark.asyncio
    async def test_warning_output(self, patch_db):
        from mcp_adapter.server import batch_record
        with patch('mcp_adapter.server._inline_batch_insert') as mock_inline:
            mock_inline.return_value = {
                "inserted_ids": [1],
                "warnings": ["血糖值偏低"],
                "duplicates_skipped": []
            }
            result = await batch_record(1, records=[
                {"type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"}
            ])
        assert "⚠️ 数据警告" in result
        assert "血糖值偏低" in result


# ============================================================
# batch_record detail formatting branches (lines 870, 872-875, 882)
# ============================================================

class TestBatchRecordDetailFormatting:
    @pytest.mark.asyncio
    async def test_bp_with_pulse_detail(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "血压测量", "systolic_pressure": 120, "diastolic_pressure": 80,
             "pulse_rate": 72, "datetime": "2026-05-02 07:00"}
        ])
        assert "脉搏 72bpm" in result

    @pytest.mark.asyncio
    async def test_weight_with_bmi_detail(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "体重记录", "weight": 70.0, "bmi": 22.5, "datetime": "2026-05-02 07:00"}
        ])
        assert "BMI 22.5" in result

    @pytest.mark.asyncio
    async def test_generic_record_detail(self, patch_db):
        from mcp_adapter.server import batch_record
        result = await batch_record(1, records=[
            {"type": "运动", "value": 0, "distance": 5.0, "datetime": "2026-05-02 07:00"}
        ])
        assert "运动" in result


# ============================================================
# batch_parse_and_record AI fallback error (lines 919-922)
# ============================================================

class TestBatchParseAndRecordAiFallbackError:
    @pytest.mark.asyncio
    async def test_ai_fallback_error(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=None), \
             patch('mcp_adapter.server._api_post', new_callable=AsyncMock) as mock_api, \
             patch('settings.EMOJI_USER_MAP', {}):
            mock_api.return_value = {"status": "error", "message": "AI 解析失败"}
            result = await batch_parse_and_record("爸爸空腹血糖 5.5")
        assert "❌" in result
        assert "AI 解析失败" in result


# ============================================================
# batch_parse_and_record no records with numeric hint (lines 925-928)
# ============================================================

class TestBatchParseAndRecordNoRecordsHint:
    @pytest.mark.asyncio
    async def test_no_records_with_numeric_hint(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=None), \
             patch('mcp_adapter.server._api_post', new_callable=AsyncMock) as mock_api, \
             patch('settings.EMOJI_USER_MAP', {}):
            mock_api.return_value = []
            result = await batch_parse_and_record("120/80")
        assert "未解析到有效记录" in result
        assert "💡" in result


# ============================================================
# batch_parse_and_record timestamp cleanup (line 942)
# ============================================================

class TestBatchParseAndRecordTimestampCleanup:
    @pytest.mark.asyncio
    async def test_timestamp_cleanup(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "空腹", "value": 5.5, "timestamp": "2026-05-02 07:00"}
        ]), patch('settings.EMOJI_USER_MAP', {}):
            result = await batch_parse_and_record("空腹 5.5")
        assert "✅" in result


# ============================================================
# batch_parse_and_record write exception (lines 950-953)
# ============================================================

class TestBatchParseAndRecordWriteException:
    @pytest.mark.asyncio
    async def test_write_exception(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "空腹", "value": 5.5}
        ]), \
             patch('mcp_adapter.server._inline_batch_insert', side_effect=Exception("db crash")), \
             patch('settings.EMOJI_USER_MAP', {}):
            result = await batch_parse_and_record("空腹 5.5")
        assert "❌" in result
        assert "db crash" in result


# ============================================================
# batch_parse_and_record skip/warning output (lines 967, 971-973)
# ============================================================

class TestBatchParseAndRecordSkipWarningOutput:
    @pytest.mark.asyncio
    async def test_skip_output(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"}
        ]), \
             patch('mcp_adapter.server._inline_batch_insert') as mock_inline, \
             patch('settings.EMOJI_USER_MAP', {}):
            mock_inline.return_value = {
                "inserted_ids": [],
                "warnings": [],
                "duplicates_skipped": ["重复"]
            }
            result = await batch_parse_and_record("空腹 5.5")
        assert "⚠️" in result
        assert "跳过" in result

    @pytest.mark.asyncio
    async def test_warning_output(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"}
        ]), \
             patch('mcp_adapter.server._inline_batch_insert') as mock_inline, \
             patch('settings.EMOJI_USER_MAP', {}):
            mock_inline.return_value = {
                "inserted_ids": [1],
                "warnings": ["血糖值偏低"],
                "duplicates_skipped": []
            }
            result = await batch_parse_and_record("空腹 5.5")
        assert "⚠️ 数据警告" in result
        assert "血糖值偏低" in result


# ============================================================
# batch_parse_and_record spo2 branches (lines 988, 990) + weight (993) + glucose (995-998)
# ============================================================

class TestBatchParseAndRecordPreviewBranches:
    @pytest.mark.asyncio
    async def test_spo2_branch(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "血压测量", "systolic_pressure": 120, "diastolic_pressure": 80,
             "pulse_rate": 72, "spo2": 98, "datetime": "2026-05-02 07:00:00"}
        ]), patch('settings.EMOJI_USER_MAP', {}):
            result = await batch_parse_and_record("🐰120/80、72，血氧98")
        assert "血氧 98%" in result

    @pytest.mark.asyncio
    async def test_weight_branch(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "体重记录", "weight": 70.0, "datetime": "2026-05-02 07:00:00"}
        ]), patch('settings.EMOJI_USER_MAP', {}):
            result = await batch_parse_and_record("体重 70.0")
        assert "体重 70.0kg" in result

    @pytest.mark.asyncio
    async def test_glucose_branch(self, patch_db):
        from mcp_adapter.server import batch_parse_and_record
        with patch('mcp_adapter.server._try_regex_parse', return_value=[
            {"user_id": 1, "type": "空腹", "value": 5.5, "datetime": "2026-05-02 07:00:00"}
        ]), patch('settings.EMOJI_USER_MAP', {}):
            result = await batch_parse_and_record("空腹 5.5")
        assert "血糖 5.5" in result
        assert "达标" in result or "未达标" in result


# ============================================================
# undo_last_record spo2 (1041), distance (1045), notes (1049)
# ============================================================

class TestUndoLastRecordBranches:
    def test_bp_with_spo2(self, patch_db):
        from mcp_adapter.server import undo_last_record
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, type, value, systolic_pressure, "
                  "diastolic_pressure, pulse_rate, spo2, timestamp) "
                  "VALUES (1, '血压测量', 0, 120, 80, 72, 98, '2026-05-02 06:30:00')")
        conn.commit()
        result = asyncio.run(undo_last_record(1))
        assert "血氧98%" in result

    def test_exercise_with_distance(self, patch_db):
        from mcp_adapter.server import undo_last_record
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, type, value, distance, timestamp) "
                  "VALUES (1, '跑步', 0, 5.0, '2026-05-02 06:30:00')")
        conn.commit()
        result = asyncio.run(undo_last_record(1))
        assert "距离 5.0km" in result

    def test_record_with_notes(self, patch_db):
        from mcp_adapter.server import undo_last_record
        conn = patch_db
        c = conn.cursor()
        c.execute("INSERT INTO records (user_id, type, value, notes, timestamp) "
                  "VALUES (1, '空腹', 5.5, '早餐前', '2026-05-02 07:00:00')")
        conn.commit()
        result = asyncio.run(undo_last_record(1))
        assert "备注: 早餐前" in result


# ============================================================
# today_summary bp branch (1087-1092), bmi (1094-1095), else (1099)
# ============================================================

class TestTodaySummaryBranches:
    def test_bp_branch(self, patch_db):
        from mcp_adapter.server import today_summary
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, value, systolic_pressure, "
                  "diastolic_pressure, pulse_rate, spo2, timestamp) "
                  "VALUES (1, '血压测量', 0, 120, 80, 72, 98, ?)", (today,))
        conn.commit()
        result = asyncio.run(today_summary(1))
        assert "血压 120/80" in result
        assert "脉搏 72bpm" in result
        assert "血氧 98%" in result

    def test_bmi_branch(self, patch_db):
        from mcp_adapter.server import today_summary
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, weight, bmi, timestamp) "
                  "VALUES (1, '体重记录', 70.0, 22.5, ?)", (today,))
        conn.commit()
        result = asyncio.run(today_summary(1))
        assert "体重 70.0kg" in result
        assert "BMI 22.5" in result

    def test_else_branch(self, patch_db):
        from mcp_adapter.server import today_summary
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, value, timestamp) "
                  "VALUES (1, '运动', 0, ?)", (today,))
        conn.commit()
        result = asyncio.run(today_summary(1))
        assert "运动" in result


# ============================================================
# list_today_records bp (1134-1139), weight (1141), notes (1145)
# ============================================================

class TestListTodayRecordsBranches:
    def test_bp_branch(self, patch_db):
        from mcp_adapter.server import list_today_records
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, value, systolic_pressure, "
                  "diastolic_pressure, pulse_rate, spo2, timestamp) "
                  "VALUES (1, '血压测量', 0, 120, 80, 72, 98, ?)", (today,))
        conn.commit()
        result = asyncio.run(list_today_records(1))
        assert "血压 120/80" in result
        assert "脉搏72" in result
        assert "血氧98%" in result

    def test_weight_branch(self, patch_db):
        from mcp_adapter.server import list_today_records
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, weight, timestamp) "
                  "VALUES (1, '体重记录', 70.0, ?)", (today,))
        conn.commit()
        result = asyncio.run(list_today_records(1))
        assert "体重 70.0kg" in result

    def test_notes_branch(self, patch_db):
        from mcp_adapter.server import list_today_records
        conn = patch_db
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO records (user_id, type, value, notes, timestamp) "
                  "VALUES (1, '空腹', 5.5, '早餐前', ?)", (today,))
        conn.commit()
        result = asyncio.run(list_today_records(1))
        assert "备注: 早餐前" in result


# ============================================================
# main entry (lines 1184-1192, 1196)
# ============================================================

class TestMainEntry:
    def test_main_stdio(self):
        from mcp_adapter.server import main
        with patch('mcp_adapter.server.mcp') as mock_mcp:
            with patch('sys.argv', ['mcp_server.py']):
                main()
            mock_mcp.run.assert_called_once_with(transport="stdio")

    def test_main_sse(self):
        from mcp_adapter.server import main
        with patch('mcp_adapter.server.mcp') as mock_mcp:
            with patch('sys.argv', ['mcp_server.py', '--transport', 'sse']):
                main()
            mock_mcp.run.assert_called_once_with(transport="sse")
