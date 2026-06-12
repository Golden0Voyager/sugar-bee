"""单元测试: tests/helpers.py — 11 个共享 mock 辅助函数"""
import datetime
from unittest.mock import MagicMock

from tests.helpers import (
    mock_health_settings,
    mock_day_settings,
    mock_dashboard_service_settings,
    make_mock_db,
    make_mock_cursor,
    MED_BASE,
    med,
    freeze_date,
    make_minimal_cursor,
    make_dashboard_stats_fetchone,
    make_dashboard_stats_fetchall,
)


# ============================================================
# mock_health_settings — 只设 return_value，不调用方法
# ============================================================

class TestMockHealthSettings:
    def test_sets_return_values(self):
        s = MagicMock()
        mock_health_settings(s)
        # 验证 return_value 被正确设置了（不 assert_called_once，因为 helper 不调用方法）
        assert s.load_config.return_value == {'target_weight': None}
        assert s.check_glucose_compliance.return_value == {'is_compliant': True, 'level': 'optimal'}
        assert s.get_bmi_category.return_value == {'label': '正常', 'color': '#4CAF50'}
        assert s.get_badge_for_rate.return_value == {'key': 'good', 'icon': '👍'}
        assert s.GLUCOSE_TARGETS == {}
        assert s.BADGE_SYSTEM == {}

    def test_passes_target_weight(self):
        s = MagicMock()
        mock_health_settings(s, target_weight=65.0)
        assert s.load_config.return_value == {'target_weight': 65.0}

    def test_passes_none_target_weight(self):
        s = MagicMock()
        mock_health_settings(s, target_weight=None)
        assert s.load_config.return_value == {'target_weight': None}


# ============================================================
# mock_day_settings
# ============================================================

class TestMockDaySettings:
    def test_sets_return_values(self):
        s = MagicMock()
        mock_day_settings(s)
        assert s.check_glucose_compliance.return_value == {'is_compliant': True, 'level': 'optimal'}
        assert s.get_badge_for_rate.return_value == {'key': 'good', 'icon': '👍'}
        assert s.get_bmi_category.return_value == {'label': '正常', 'color': '#4CAF50'}


# ============================================================
# mock_dashboard_service_settings
# ============================================================

class TestMockDashboardServiceSettings:
    def test_sets_return_values(self):
        s = MagicMock()
        um = MagicMock()
        mock_dashboard_service_settings(s, um)
        assert s.get_bmi_category.return_value == {'label': '正常', 'color': '#4CAF50'}
        assert s.get_badge_for_rate.return_value == {'key': 'encourage', 'icon': '💪'}
        assert s.GLUCOSE_TARGETS == {}
        assert s.BADGE_SYSTEM == {}
        assert s.check_glucose_compliance.return_value == {'is_compliant': True, 'level': 'optimal'}
        assert s.calculate_bmi.return_value == 23.0

    def test_um_returns_config(self):
        s = MagicMock()
        um = MagicMock()
        mock_dashboard_service_settings(s, um)
        instance = um.return_value
        config = instance.get_user_config.return_value
        assert config['name'] == '测试'
        assert config['height'] == 170


# ============================================================
# make_mock_db
# ============================================================

class TestMakeMockDb:
    def test_returns_magic_mock(self):
        db = make_mock_db()
        assert isinstance(db, MagicMock)

    def test_without_cursor(self):
        db = make_mock_db()
        assert db.cursor() is not None

    def test_with_cursor(self):
        c = MagicMock()
        db = make_mock_db(cursor=c)
        # cursor.return_value is set by the helper — calling cursor() returns c
        assert db.cursor() is c


# ============================================================
# make_mock_cursor
# ============================================================

class TestMakeMockCursor:
    def test_defaults(self):
        c = make_mock_cursor()
        assert c.fetchall.return_value == []

    def test_fetchone_side_effect(self):
        c = make_mock_cursor(fetchone_side_effect=[(1,), (2,)])
        assert c.fetchone() == (1,)
        assert c.fetchone() == (2,)

    def test_fetchone_return_value(self):
        c = make_mock_cursor(fetchone_return_value=(42,))
        assert c.fetchone() == (42,)
        assert c.fetchone() == (42,)

    def test_fetchone_side_effect_overrides_return_value(self):
        c = make_mock_cursor(
            fetchone_side_effect=[(1,)],
            fetchone_return_value=(99,)
        )
        assert c.fetchone() == (1,)

    def test_fetchall_side_effect(self):
        c = make_mock_cursor(fetchall_side_effect=[[1, 2], [3, 4]])
        assert c.fetchall() == [1, 2]
        assert c.fetchall() == [3, 4]

    def test_no_args_returns_empty_fetchall(self):
        c = make_mock_cursor()
        assert c.fetchall() == []
        assert c.fetchall() == []

    def test_returns_magic_mock(self):
        c = make_mock_cursor()
        assert isinstance(c, MagicMock)


# ============================================================
# MED_BASE + med()
# ============================================================

class TestMedBase:
    def test_med_base_has_all_keys(self):
        required = [
            'id', 'medication_name', 'dosage', 'dose_quantity', 'dose_unit',
            'times_per_day', 'timing_notes', 'start_date', 'category', 'med_type',
            'frequency', 'frequency_detail', 'end_date',
        ]
        for key in required:
            assert key in MED_BASE, f"Missing key: {key}"

    def test_med_base_default_values(self):
        assert MED_BASE['medication_name'] == '二甲双胍'
        assert MED_BASE['dosage'] == '500mg'
        assert MED_BASE['frequency'] == 'daily'
        assert MED_BASE['dose_quantity'] == '1'
        assert MED_BASE['dose_unit'] == '片'


class TestMed:
    def test_med_no_overrides(self):
        result = med()
        assert result['medication_name'] == '二甲双胍'
        assert result['dosage'] == '500mg'
        assert result['id'] == 1

    def test_med_with_overrides(self):
        result = med(id=2, medication_name='格列美脲', dosage='2mg')
        assert result['id'] == 2
        assert result['medication_name'] == '格列美脲'
        assert result['dosage'] == '2mg'
        assert result['dose_quantity'] == '1'
        assert result['times_per_day'] == 2
        assert result['frequency'] == 'daily'

    def test_med_does_not_mutate_base(self):
        m1 = med(id=1)
        m2 = med(id=2)
        assert m1['id'] == 1
        assert m2['id'] == 2
        assert MED_BASE['id'] == 1

    def test_med_type_override(self):
        result = med(frequency='weekly', frequency_detail='Monday')
        assert result['frequency'] == 'weekly'
        assert result['frequency_detail'] == 'Monday'


# ============================================================
# freeze_date
# ============================================================

class TestFreezeDate:
    def test_now_returns_frozen_time(self):
        mock_dt = MagicMock()
        target_date = datetime.date(2024, 7, 15)
        freeze_date(mock_dt, target_date)
        # Helper sets return_value but doesn't call now() — verify the return_value
        now = mock_dt.datetime.now.return_value
        assert now.hour == 10
        assert now.minute == 0
        assert now.second == 0

    def test_today_returns_date(self):
        mock_dt = MagicMock()
        target_date = datetime.date(2024, 7, 15)
        freeze_date(mock_dt, target_date)
        # Helper sets date.today.return_value, not called yet
        assert mock_dt.date.today.return_value == target_date

    def test_preserves_combine(self):
        mock_dt = MagicMock()
        freeze_date(mock_dt, datetime.date(2024, 7, 15))
        combined = mock_dt.datetime.combine(datetime.date(2024, 1, 1), datetime.time(12, 0))
        assert combined == datetime.datetime(2024, 1, 1, 12, 0, 0)

    def test_preserves_strptime(self):
        mock_dt = MagicMock()
        freeze_date(mock_dt, datetime.date(2024, 7, 15))
        parsed = mock_dt.datetime.strptime('2024-06-01', '%Y-%m-%d')
        assert parsed == datetime.datetime(2024, 6, 1, 0, 0, 0)

    def test_preserves_timedelta(self):
        mock_dt = MagicMock()
        freeze_date(mock_dt, datetime.date(2024, 7, 15))
        td = mock_dt.timedelta(days=3)
        assert td.days == 3
        td2 = mock_dt.datetime.timedelta(hours=5)
        assert td2.seconds == 5 * 3600

    def test_with_other_date(self):
        mock_dt = MagicMock()
        freeze_date(mock_dt, datetime.date(2024, 12, 25))
        now = mock_dt.datetime.now.return_value
        assert now.month == 12
        assert now.day == 25

    def test_date_today_returns_correct_value(self):
        mock_dt = MagicMock()
        target = datetime.date(2024, 7, 15)
        freeze_date(mock_dt, target)
        assert mock_dt.date.today.return_value == target


# ============================================================
# make_minimal_cursor
# ============================================================

class TestMakeMinimalCursor:
    def test_defaults(self):
        c = make_minimal_cursor()
        assert c.fetchone() == (0,)  # total_records
        assert c.fetchall() == []

    def test_with_all_meds(self):
        c = make_minimal_cursor(all_meds=[{'id': 1, 'name': '药'}])
        for _ in range(8):
            c.fetchone()
        # fetchall sequence: weights, compliance, records, exercises, bps, meds, taken, temp
        c.fetchall()  # 1: weights
        c.fetchall()  # 2: compliance
        c.fetchall()  # 3: records
        c.fetchall()  # 4: exercises
        c.fetchall()  # 5: bps
        f6 = c.fetchall()  # 6: all_meds
        assert f6 == [{'id': 1, 'name': '药'}]

    def test_with_today_records(self):
        c = make_minimal_cursor(today_records=[{'value': 6.5, 'type': '空腹'}])
        for _ in range(8):
            c.fetchone()
        c.fetchall()  # 1: weights
        c.fetchall()  # 2: compliance
        f3 = c.fetchall()  # 3: today_records
        assert f3 == [{'value': 6.5, 'type': '空腹'}]

    def test_with_today_weights(self):
        c = make_minimal_cursor(today_weights=[(70.0, 22.5, '2024-06-01')])
        for _ in range(8):
            c.fetchone()
        f1 = c.fetchall()  # 1: today_weights
        assert f1 == [(70.0, 22.5, '2024-06-01')]

    def test_with_today_exercises(self):
        c = make_minimal_cursor(today_exercises=[{'type': '跑步', 'distance': 5.0}])
        for _ in range(8):
            c.fetchone()
        c.fetchall()  # 1: weights
        c.fetchall()  # 2: compliance
        c.fetchall()  # 3: records
        f4 = c.fetchall()  # 4: exercises
        assert f4 == [{'type': '跑步', 'distance': 5.0}]

    def test_with_today_bps(self):
        c = make_minimal_cursor(today_bps=[{'systolic_pressure': 120}])
        for _ in range(8):
            c.fetchone()
        c.fetchall()  # 1: weights
        c.fetchall()  # 2: compliance
        c.fetchall()  # 3: records
        c.fetchall()  # 4: exercises
        f6 = c.fetchall()  # 5: today_bps
        assert f6 == [{'systolic_pressure': 120}]

    def test_with_taken_logs(self):
        c = make_minimal_cursor(taken_logs=[{'plan_id': 1, 'count': 2}])
        for _ in range(8):
            c.fetchone()
        c.fetchall()  # 1: weights
        c.fetchall()  # 2: compliance
        c.fetchall()  # 3: records
        c.fetchall()  # 4: exercises
        c.fetchall()  # 5: bps
        c.fetchall()  # 6: meds
        f7 = c.fetchall()  # 7: taken_logs
        assert f7 == [{'plan_id': 1, 'count': 2}]

    def test_with_temp_meds(self):
        c = make_minimal_cursor(temp_meds=[{'medication_name': '布洛芬'}])
        for _ in range(8):
            c.fetchone()
        c.fetchall()  # 1: weights
        c.fetchall()  # 2: compliance
        c.fetchall()  # 3: records
        c.fetchall()  # 4: exercises
        c.fetchall()  # 5: bps
        c.fetchall()  # 6: meds
        c.fetchall()  # 7: taken
        f8 = c.fetchall()  # 8: temp_meds
        assert f8 == [{'medication_name': '布洛芬'}]

    def test_with_compliance_glucose(self):
        c = make_minimal_cursor(compliance_glucose=[(6.5, '空腹'), (7.0, '餐后')])
        for _ in range(8):
            c.fetchone()
        c.fetchall()  # 1: weights
        f2 = c.fetchall()  # 2: compliance
        assert f2 == [(6.5, '空腹'), (7.0, '餐后')]

    def test_with_health_analyses(self):
        c = make_minimal_cursor(health_analyses={'id': 1, 'score': 85})
        for _ in range(7):
            c.fetchone()
        f8 = c.fetchone()  # 8th fetchone = health_analyses
        assert f8 == {'id': 1, 'score': 85}

    def test_all_params_together(self):
        c = make_minimal_cursor(
            all_meds=[{'id': 1}],
            today_records=[{'value': 6.5}],
            today_exercises=[{'type': '跑步'}],
            today_bps=[{'sys': 120}],
            today_weights=[(70.0,)],
            taken_logs=[{'plan_id': 1}],
            temp_meds=[{'name': '药'}],
            compliance_glucose=[(6.0,)],
            health_analyses={'score': 80},
        )
        for _ in range(8):
            c.fetchone()
        assert c.fetchall() == [(70.0,)]   # 1: weights
        assert c.fetchall() == [(6.0,)]    # 2: compliance
        assert c.fetchall() == [{'value': 6.5}]  # 3: records
        assert c.fetchall() == [{'type': '跑步'}]  # 4: exercises
        assert c.fetchall() == [{'sys': 120}]  # 5: bps
        assert c.fetchall() == [{'id': 1}]  # 6: meds
        assert c.fetchall() == [{'plan_id': 1}]  # 7: taken
        assert c.fetchall() == [{'name': '药'}]  # 8: temp

    def test_all_params_empty(self):
        c = make_minimal_cursor()
        for _ in range(8):
            c.fetchone()
        for _ in range(8):
            assert c.fetchall() == []


# ============================================================
# make_dashboard_stats_fetchone
# ============================================================

class TestMakeDashboardStatsFetchone:
    def test_default_base_length(self):
        result = make_dashboard_stats_fetchone()
        assert len(result) == 8

    def test_default_base_order(self):
        result = make_dashboard_stats_fetchone()
        assert result[0] == (100,)                            # total_records
        assert result[1] == (6.0, 7.5, None, None)            # glucose_stats
        assert result[2] == (None, None, None, None)          # exercise_stats
        assert result[3] is None                               # vo2max

    def test_no_bp_no_weight_mid_section(self):
        result = make_dashboard_stats_fetchone()
        assert result[4] == (None, None, None, None, None, None, None)  # bp_stats
        assert result[5] is None                               # latest_weight
        assert result[6] == (70.0,)                            # avg_weight
        assert result[7] is None                               # health_analyses

    def test_with_bp(self):
        result = make_dashboard_stats_fetchone(has_bp=True)
        assert len(result) == 10
        assert result[4] == (120.0, 80.0, 5, 135, 85, 110, 75)  # bp_stats
        assert result[5] == ('2024-06-01 08:00:00',)           # bp_max_date
        assert result[6] == ('2024-06-01 22:00:00',)          # bp_min_date

    def test_with_weight(self):
        result = make_dashboard_stats_fetchone(has_weight=True)
        assert len(result) == 9
        assert result[5] == (70.0, 23.5, '2024-06-01 07:00:00')  # latest_weight

    def test_with_both_bp_and_weight(self):
        result = make_dashboard_stats_fetchone(has_bp=True, has_weight=True)
        assert len(result) == 11
        assert result[7] == (70.0, 23.5, '2024-06-01 07:00:00')  # latest_weight

    def test_all_none_glucose(self):
        result = make_dashboard_stats_fetchone()
        assert result[1][2] is None  # max
        assert result[1][3] is None  # min

    def test_avg_weight_returns_tuple(self):
        result = make_dashboard_stats_fetchone()
        assert isinstance(result[6], tuple)


# ============================================================
# make_dashboard_stats_fetchall
# ============================================================

class TestMakeDashboardStatsFetchall:
    def test_returns_list_of_8_empty_lists(self):
        result = make_dashboard_stats_fetchall()
        assert isinstance(result, list)
        assert len(result) == 8
        for item in result:
            assert item == []

    def test_each_element_is_list(self):
        result = make_dashboard_stats_fetchall()
        for item in result:
            assert isinstance(item, list)

    def test_immutable_per_call(self):
        r1 = make_dashboard_stats_fetchall()
        r2 = make_dashboard_stats_fetchall()
        assert r1 is not r2
        assert r1[0] is not r2[0]
