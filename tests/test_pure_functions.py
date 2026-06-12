"""纯函数测试 — _validate_record_data, _safe_float, get_user_stats 等"""
from unittest.mock import MagicMock


class TestValidateRecordData:
    """_validate_record_data() 数据校验测试"""

    def test_valid_glucose_record_no_warnings(self):
        from routes.api_records import _validate_record_data
        r = {'type': '空腹', 'value': 6.0}
        warnings = _validate_record_data(r)
        assert warnings == []

    def test_invalid_systolic_too_high(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 300, 'diastolic_pressure': 80}
        warnings = _validate_record_data(r)
        assert any('收缩压' in w for w in warnings)

    def test_invalid_diastolic_too_low(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 30}
        warnings = _validate_record_data(r)
        assert any('舒张压' in w for w in warnings)

    def test_systolic_not_greater_than_diastolic(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 80, 'diastolic_pressure': 90}
        warnings = _validate_record_data(r)
        assert any('不应小于等于' in w for w in warnings)

    def test_invalid_spo2_too_low(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80, 'spo2': 85}
        warnings = _validate_record_data(r)
        assert any('血氧' in w for w in warnings)

    def test_invalid_spo2_too_high(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80, 'spo2': 105}
        warnings = _validate_record_data(r)
        assert any('血氧' in w for w in warnings)

    def test_invalid_pulse_too_low(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80, 'pulse_rate': 20}
        warnings = _validate_record_data(r)
        assert any('脉搏' in w for w in warnings)

    def test_invalid_pulse_too_high(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80, 'pulse_rate': 250}
        warnings = _validate_record_data(r)
        assert any('脉搏' in w for w in warnings)

    def test_invalid_glucose_too_high(self):
        from routes.api_records import _validate_record_data
        r = {'type': '空腹', 'value': 40.0}
        warnings = _validate_record_data(r)
        assert any('血糖' in w for w in warnings)

    def test_invalid_glucose_too_low(self):
        from routes.api_records import _validate_record_data
        r = {'type': '空腹', 'value': 0.5}
        warnings = _validate_record_data(r)
        assert any('血糖' in w for w in warnings)

    def test_invalid_weight_too_low(self):
        from routes.api_records import _validate_record_data
        r = {'type': '体重记录', 'weight': 10.0}
        warnings = _validate_record_data(r)
        assert any('体重' in w for w in warnings)

    def test_invalid_weight_too_high(self):
        from routes.api_records import _validate_record_data
        r = {'type': '体重记录', 'weight': 350.0}
        warnings = _validate_record_data(r)
        assert any('体重' in w for w in warnings)

    def test_valid_weight_no_warnings(self):
        from routes.api_records import _validate_record_data
        r = {'type': '体重记录', 'weight': 70.0}
        warnings = _validate_record_data(r)
        assert warnings == []

    def test_valid_bp_no_warnings(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 80}
        warnings = _validate_record_data(r)
        assert warnings == []

    def test_glucose_with_zero_value_skipped(self):
        from routes.api_records import _validate_record_data
        r = {'type': '空腹', 'value': 0}
        warnings = _validate_record_data(r)
        assert warnings == []

    def test_systolic_equals_diastolic(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 120, 'diastolic_pressure': 120}
        warnings = _validate_record_data(r)
        assert any('不应小于等于' in w for w in warnings)

    def test_multiple_warnings(self):
        from routes.api_records import _validate_record_data
        r = {'type': '血压测量', 'systolic_pressure': 300, 'diastolic_pressure': 200,
             'pulse_rate': 250, 'spo2': 85}
        warnings = _validate_record_data(r)
        assert len(warnings) >= 3


class TestSafeFloat:
    """_safe_float() 安全浮点数转换测试"""

    def test_normal_float(self):
        from routes.api_chat import _safe_float
        assert _safe_float('6.5') == 6.5

    def test_integer_string(self):
        from routes.api_chat import _safe_float
        assert _safe_float('120') == 120.0

    def test_none_returns_none(self):
        from routes.api_chat import _safe_float
        assert _safe_float(None) is None

    def test_empty_string_returns_none(self):
        from routes.api_chat import _safe_float
        assert _safe_float('') is None

    def test_invalid_string_returns_none(self):
        from routes.api_chat import _safe_float
        assert _safe_float('abc') is None

    def test_float_value_passthrough(self):
        from routes.api_chat import _safe_float
        assert _safe_float(6.5) == 6.5

    def test_zero_value(self):
        from routes.api_chat import _safe_float
        assert _safe_float('0') == 0.0

    def test_negative_value(self):
        from routes.api_chat import _safe_float
        assert _safe_float('-5.5') == -5.5


class TestGetUserStats:
    """get_user_stats() 使用 mock DB 测试"""

    def test_empty_stats_for_new_user(self):
        from routes.api_records import get_user_stats
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        stats = get_user_stats(mock_db, user_id=1)
        assert stats['avg_fasting'] == '未知'
        assert stats['avg_postmeal'] == '未知'
        assert stats['last_value'] == '未知'

    def test_stats_with_data(self):
        from routes.api_records import get_user_stats
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [(6.2,), (7.5,), (6.0, '空腹')]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        stats = get_user_stats(mock_db, user_id=1)
        assert stats['avg_fasting'] == 6.2
        assert stats['avg_postmeal'] == 7.5
        assert stats['last_value'] == 6.0
        assert stats['last_type'] == '空腹'

    def test_stats_handles_db_error(self):
        from routes.api_records import get_user_stats
        mock_db = MagicMock()
        mock_db.cursor.side_effect = Exception("DB error")

        stats = get_user_stats(mock_db, user_id=1)
        # On DB error, returns empty dict {} (stats is initialized empty, try/except returns it)
        assert stats == {}
