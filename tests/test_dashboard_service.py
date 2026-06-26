"""冲刺 dashboard_service 覆盖率 (56% → 85%): 用药频率判断 + CGM 匹配 + 边界"""
import datetime
from unittest.mock import MagicMock, patch

from tests.helpers import (
    make_minimal_cursor as _make_minimal_cursor,
)
from tests.helpers import (
    med as _med,
)
from tests.helpers import (
    mock_dashboard_service_settings as _setup_mocks,
)


def _freeze_date(mock_app_now, date_value):
    mock_app_now.return_value = datetime.datetime.combine(date_value, datetime.time(10, 0, 0))


# ============================================================
# 频率: daily
# ============================================================

class TestMedFrequencyDaily:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_daily_included(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='daily')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        names = [m['name'] for m in result['active_medications']]
        assert '二甲双胍' in names

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_frequency_none_defaults_to_daily(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency=None)])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        names = [m['name'] for m in result['active_medications']]
        assert '二甲双胍' in names


# ============================================================
# 频率: every_n_days
# ============================================================

class TestMedFrequencyEveryNDays:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_every_n_days_day_zero_included(self, mock_dt, mock_settings, mock_um):
        today = datetime.date(2024, 6, 15)
        _freeze_date(mock_dt, today)
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='every_n_days', frequency_detail='3', start_date='2024-06-15')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_every_n_days_not_day(self, mock_dt, mock_settings, mock_um):
        today = datetime.date(2024, 6, 15)
        _freeze_date(mock_dt, today)
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='every_n_days', frequency_detail='3', start_date='2024-06-14')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_every_n_days_match_3_days(self, mock_dt, mock_settings, mock_um):
        today = datetime.date(2024, 6, 15)
        _freeze_date(mock_dt, today)
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='every_n_days', frequency_detail='3', start_date='2024-06-12')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_every_n_days_bad_detail_fallback(self, mock_dt, mock_settings, mock_um):
        today = datetime.date(2024, 6, 15)
        _freeze_date(mock_dt, today)
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='every_n_days', frequency_detail='abc', start_date='2024-06-14')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_every_n_days_no_start_date(self, mock_dt, mock_settings, mock_um):
        today = datetime.date(2024, 6, 15)
        _freeze_date(mock_dt, today)
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='every_n_days', frequency_detail='7', start_date=None)])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]


# ============================================================
# 频率: weekdays
# ============================================================

class TestMedFrequencyWeekdays:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_weekday_monday(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='weekdays')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_weekend_saturday_excluded(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 15))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='weekdays')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_weekend_sunday_excluded(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 16))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='weekdays')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]


# ============================================================
# 频率: weekly
# ============================================================

class TestMedFrequencyWeekly:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_weekly_matches(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))  # Monday
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='weekly', frequency_detail='Monday')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_weekly_not_today(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='weekly', frequency_detail='Wednesday')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_weekly_no_detail_defaults_monday(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='weekly', frequency_detail='')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]


# ============================================================
# 频率: biweekly
# ============================================================

class TestMedFrequencyBiweekly:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_biweekly_week0_included(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='biweekly', frequency_detail='Monday', start_date='2024-06-17')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_biweekly_off_week_excluded(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='biweekly', frequency_detail='Monday', start_date='2024-06-10')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_biweekly_week2_included(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='biweekly', frequency_detail='Monday', start_date='2024-06-03')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_biweekly_wrong_day_excluded(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 17))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='biweekly', frequency_detail='Friday', start_date='2024-06-03')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]


# ============================================================
# 频率: monthly
# ============================================================

class TestMedFrequencyMonthly:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_monthly_single_day_match(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 15))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='monthly', frequency_detail='15')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_monthly_no_match(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 15))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='monthly', frequency_detail='1, 30')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_monthly_multi_day(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 15))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='monthly', frequency_detail='1, 15, 30')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    @patch('services.dashboard_service.app_now')
    def test_monthly_bad_detail_defaults_day1(self, mock_dt, mock_settings, mock_um):
        _freeze_date(mock_dt, datetime.date(2024, 6, 15))
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='monthly', frequency_detail='bad')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' not in [m['name'] for m in result['active_medications']]


# ============================================================
# 频率: fallback (unknown)
# ============================================================

class TestMedFrequencyFallback:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_unknown_frequency_included(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(id=1, frequency='unknown_freq')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert '二甲双胍' in [m['name'] for m in result['active_medications']]


# ============================================================
# CGM 匹配逻辑
# ============================================================

class TestCGMMatching:
    def _rec(self, value, rec_type, timestamp, is_predicted=0):
        return {'value': value, 'type': rec_type, 'timestamp': timestamp, 'is_predicted': is_predicted}

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_within_30_min_matches(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_records=[self._rec(5.8, 'CGM', '2024-06-01 07:20:00')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting['cgm'] is True
        assert fasting['value'] == 5.8

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_beyond_30_min_ignored(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_records=[
            self._rec(5.8, 'CGM', '2024-06-01 06:30:00'),
            self._rec(6.5, '空腹', '2024-06-01 07:15:00'),
        ])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting.get('cgm') is not True
        assert fasting['value'] == 6.5

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_exact_slot_time(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_records=[self._rec(5.8, 'CGM', '2024-06-01 14:30:00')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        post_lunch = [s for s in result['today_overview'] if s['key'] == 'post_lunch'][0]
        assert post_lunch['cgm'] is True

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_multiple_picks_closest(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_records=[
            self._rec(5.9, 'CGM', '2024-06-01 07:10:00'),
            self._rec(5.5, 'CGM', '2024-06-01 07:12:00'),
            self._rec(5.3, 'CGM', '2024-06-01 07:25:00'),
        ])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting['value'] == 5.5

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_no_timestamp_ignored(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_records=[
            self._rec(5.8, 'CGM', 'bad-time'),
            self._rec(6.5, '空腹', '2024-06-01 07:15:00'),
        ])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting.get('cgm') is not True

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_overrides_measured(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_records=[
            self._rec(5.8, 'CGM', '2024-06-01 07:18:00'),
            self._rec(6.5, '空腹', '2024-06-01 07:15:00'),
        ])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting['cgm'] is True
        assert fasting['value'] == 5.8


# ============================================================
# 今日运动/血压/体重列表
# ============================================================

class TestDashboardTodayLists:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_today_exercises_list(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_exercises=[{
            'type': '跑步', 'distance': 5.0, 'calories': 300, 'duration': 30,
            'heart_rate': 145, 'pace': '5:30', 'max_pace': '4:30', 'cadence': 160,
            'vo2max': 42.0, 'max_heart_rate': 160, 'steps': 5000,
            'timestamp': '2024-06-01 17:00:00',
        }])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['today_exercise']['type'] == '跑步'
        assert result['today_exercise']['time'] == '17:00'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_today_bps_list(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_bps=[{
            'systolic_pressure': 120, 'diastolic_pressure': 80,
            'pulse_rate': 72, 'spo2': 98, 'timestamp': '2024-06-01 08:00:00',
        }])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['today_bp']['systolic'] == 120

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_today_weights_list(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(today_weights=[
            (70.0, 22.5, '2024-06-01 07:00:00'),
            (69.5, 22.0, '2024-06-01 18:00:00'),
        ])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert len(result['today_weights']) == 2
        assert result['today_weight']['weight'] == 70.0


# ============================================================
# 健康分析
# ============================================================

class TestDashboardHealthAnalysis:
    def _make_health_mock(self, score, recommendations=None, days=7):
        health = MagicMock()
        health.keys.return_value = ['id', 'health_score', 'recommendations', 'days', 'created_at']
        data = {'id': 1, 'health_score': score, 'recommendations': recommendations,
                'days': days, 'created_at': '2024-06-01'}
        health.__getitem__ = lambda s, k, d=data: d.get(k, None)
        return health

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_score_excellent(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(health_analyses=self._make_health_mock(92))
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['score_label'] == '优秀'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_score_good(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(health_analyses=self._make_health_mock(85))
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['score_label'] == '良好'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_score_attention(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(health_analyses=self._make_health_mock(45))
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['score_label'] == '需关注'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_recommendations_parsed(self, mock_settings, mock_um):
        import json
        _setup_mocks(mock_settings, mock_um)
        recs = ['多运动', '控制饮食']
        mock_c = _make_minimal_cursor(health_analyses=self._make_health_mock(80, json.dumps(recs)))
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['recommendations'] == recs

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_recommendations_bad_json(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(health_analyses=self._make_health_mock(75, 'not-json{{{'))
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['recommendations'] == []

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_days_label(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(health_analyses=self._make_health_mock(78, days=30))
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['days_label'] == '近30天'


# ============================================================
# 用药状态 + 达标率
# ============================================================

class TestDashboardMedStatus:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_taken_count(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(
            all_meds=[_med(id=1, times_per_day=2), _med(id=2, times_per_day=3, medication_name='格列美脲')],
            taken_logs=[{'plan_id': 1, 'count': 2}, {'plan_id': 2, 'count': 1}],
        )
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['today_med_status']['taken_count'] == 3
        assert result['today_med_status']['total_required'] == 5

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_temp_medications(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(
            all_meds=[],
            temp_meds=[{'medication_name': '布洛芬', 'notes': '头疼', 'timestamp': '2024-06-01 14:00:00'}],
        )
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['today_med_status']['temp_medications'][0]['name'] == '布洛芬'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_dosage_display_multi(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(dose_quantity='2', dose_unit='片')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['active_medications'][0]['dosage'] == '500mg ×2片'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_dosage_display_single(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(all_meds=[_med(dose_quantity='1')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['active_medications'][0]['dosage'] == '500mg'


class TestDashboardCompliance:
    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_compliance_100(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(compliance_glucose=[(6.0, '空腹'), (7.0, '餐后2小时')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['compliance'] == 100

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_compliance_mixed(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        def side(val, typ):
            return {'is_compliant': val != 10.0, 'level': 'optimal' if val != 10.0 else 'high'}
        mock_settings.check_glucose_compliance.side_effect = side
        mock_c = _make_minimal_cursor(compliance_glucose=[(6.0, '空腹'), (7.0, '餐后2小时'), (10.0, '餐后2小时')])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['compliance'] == 66

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_compliance_empty(self, mock_settings, mock_um):
        _setup_mocks(mock_settings, mock_um)
        mock_c = _make_minimal_cursor(compliance_glucose=[])
        from services.dashboard_service import get_dashboard_stats
        mock_db = MagicMock(); mock_db.cursor.return_value = mock_c
        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['compliance'] == 0
