"""扩展 services 和 routes 测试 — 冲击 80%+"""
import json
import datetime
import pytest
from unittest.mock import patch, MagicMock

from tests.helpers import (
    mock_dashboard_service_settings as _setup_dashboard_mocks,
    make_dashboard_stats_fetchone as _make_dashboard_fetchone,
    make_dashboard_stats_fetchall as _make_dashboard_fetchall,
)


# ============================================================
# prediction_service 扩展 (73% → 88%)
# ============================================================

class TestLinkPredictionEdgeCases:
    """link_prediction_to_real_record 更多边界"""

    def _make_cursor(self, prediction_row=None):
        mock_c = MagicMock()
        mock_c.fetchone.return_value = prediction_row
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        return mock_db, mock_c

    def test_postmeal_generic_matches_dinner_by_hour(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(100, 9.0))
        # 餐后 at 19:00 → dinner time range (17-23)
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后', 10.0, '2024-01-01 19:00:00')
        assert result is not None

    def test_postmeal_outside_hours_fallback(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(110, 7.0))
        # 餐后 at 06:00 → outside breakfast/lunch/dinner ranges
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后', 7.5, '2024-01-01 06:00:00')
        assert result is not None

    def test_fasting_with_bp_skipped(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, _ = self._make_cursor()
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹血压', 120, '2024-01-01 07:00:00')
        assert result is None

    def test_prediction_with_no_timestamp(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(120, 6.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', 6.0)
        assert result is not None

    def test_prediction_error_calculation(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(130, 4.5))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', 7.0, '2024-01-01 07:15:00')
        assert result is not None
        assert result['error'] == 2.5
        assert result['predicted_value'] == 4.5


class TestPredictMorningFpgExtended:
    """predict_morning_fpg 更多场景"""

    @patch('services.prediction_service.AI_AVAILABLE', False)
    def test_returns_none_when_ai_unavailable(self):
        from services.prediction_service import predict_morning_fpg
        result = predict_morning_fpg(MagicMock(), user_id=1)
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    @patch('services.prediction_service.settings.get_ai_system_prompt')
    def test_with_glucose_and_calories(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_prompt.return_value = '用户档案'
        mock_ai.return_value = '{"predicted_value": 6.0, "reasoning": "稳定"}'

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            None,           # no existing prediction
            (6.5, '空腹', '2024-01-01 07:15:00'),  # yesterday glucose (fetchall, not fetchone)
        ]
        # Use fetchall for glucose/cals queries
        mock_c.fetchall.side_effect = [
            [(6.5, '空腹', '2024-01-01 07:15:00')],  # yesterday glucose: (value, type, timestamp)
            [('午餐', 500, '2024-01-01 11:30:00', 45, 70)],  # yesterday_calories: (type, calories, timestamp, carbs, gi)
            [],   # recent_fpg
            [],   # prediction_history
            [],   # medications
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        predict_morning_fpg(mock_db, user_id=1)
        mock_db.commit.assert_called()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    @patch('services.prediction_service.settings.get_ai_system_prompt')
    def test_no_json_match_returns_early(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_prompt.return_value = ''
        mock_ai.return_value = 'not json at all'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.return_value = []
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        predict_morning_fpg(mock_db, user_id=1)
        # Should not commit (returned early due to no JSON match)
        mock_db.commit.assert_not_called()


class TestPredictPostExerciseExtended:
    """predict_post_exercise_glucose 更多场景"""

    @patch('services.prediction_service.AI_AVAILABLE', False)
    def test_returns_none_ai_unavailable(self):
        from services.prediction_service import predict_post_exercise_glucose
        result = predict_post_exercise_glucose(MagicMock(), user_id=1)
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_invalid_prediction_value(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = '{"predicted_value": 99.0, "reasoning": "invalid"}'
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            None,
            (6.0,),
            (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00'),
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_no_json_match(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = 'not json'
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            None,
            (6.0,),
            (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00'),
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_rate_limit_429_re_raises(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.side_effect = Exception("429 RESOURCE_EXHAUSTED retry in 60")
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            None,
            (6.0,),
            (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00'),
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        with pytest.raises(Exception, match='429'):
            predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')


class TestBackfillExtended:
    """backfill_post_exercise_predictions 更多场景"""

    @patch('services.prediction_service.predict_post_exercise_glucose')
    def test_backfill_with_429_re_raise(self, mock_predict):
        from services.prediction_service import backfill_post_exercise_predictions
        mock_predict.side_effect = [5.0, Exception("429 limit"), None, None, None]
        mock_db = MagicMock()
        result = backfill_post_exercise_predictions(mock_db, user_id=1, days=5)
        assert result['success'] == 1
        assert result['error'] == 1
        assert result['skipped'] == 3

    @patch('services.prediction_service.predict_post_exercise_glucose')
    def test_backfill_outer_exception(self, mock_predict):
        from services.prediction_service import backfill_post_exercise_predictions
        mock_predict.side_effect = Exception("fatal")
        mock_db = MagicMock()
        result = backfill_post_exercise_predictions(mock_db, user_id=1, days=3)
        # Outer try/except catches everything
        assert 'success' in result
        assert result['error'] == 3


class TestPredictRemainingSlotsExtended:
    """predict_remaining_glucose_slots 更多场景"""

    @patch('services.prediction_service.AI_AVAILABLE', False)
    def test_returns_empty_when_ai_unavailable(self):
        from services.prediction_service import predict_remaining_glucose_slots
        result = predict_remaining_glucose_slots(MagicMock(), user_id=1)
        assert result == []

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_invalid_prediction_values_filtered(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.return_value = json.dumps([
            {"type": "午餐后2小时", "value": 999.0, "reasoning": "无效"},
            {"type": "晚饭前", "value": 6.0, "reasoning": "有效"},
        ])
        records_3col = [(6.5, '空腹', '2024-06-01 07:15:00')]
        types_1col = [('空腹',)]
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [records_3col, types_1col]
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert len(result) == 1
        assert result[0]['value'] == 6.0

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_no_json_array_match(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.return_value = 'not json'
        records_3col = [(6.5, '空腹', '2024-06-01 07:15:00')]
        types_1col = [('空腹',)]
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [records_3col, types_1col]
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert result == []

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_mismatched_pred_type_skipped(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.return_value = json.dumps([
            {"type": "不存在的类型", "value": 6.0, "reasoning": "test"},
        ])
        records_3col = [(6.5, '空腹', '2024-06-01 07:15:00')]
        types_1col = [('空腹',)]
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [records_3col, types_1col]
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert result == []

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_force_update_skips_existing_check(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.return_value = json.dumps([
            {"type": "睡前", "value": 6.0, "reasoning": "预测"},
        ])
        # All slots measured, but force_update=True should override
        records_3col = [
            (6.5, '空腹', '2024-06-01 07:15:00'),
            (8.0, '早餐后2小时', '2024-06-01 11:00:00'),
            (7.5, '午餐后2小时', '2024-06-01 14:30:00'),
            (6.0, '晚饭前', '2024-06-01 17:30:00'),
            (8.5, '晚餐后2小时', '2024-06-01 20:00:00'),
            (6.2, '睡前', '2024-06-01 22:00:00'),
        ]
        types_1col = [('空腹',), ('早餐后2小时',), ('午餐后2小时',), ('晚饭前',), ('晚餐后2小时',), ('睡前',)]
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [records_3col, types_1col]
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1, force_update=True)
        assert isinstance(result, list)
        assert len(result) >= 1


# ============================================================
# dashboard_service 扩展 (42% → 65%)
# ============================================================

class TestDashboardStatsExtended:
    """get_dashboard_stats 更多场景测试"""

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_with_glucose_data(self, mock_settings, mock_um):
        from services.dashboard_service import get_dashboard_stats
        _setup_dashboard_mocks(mock_settings, mock_um)

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = _make_dashboard_fetchone(has_bp=True, has_weight=True)
        mock_c.fetchall.side_effect = _make_dashboard_fetchall()

        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert 'compliance' in result
        assert 'today_overview' in result
        assert len(result['today_overview']) == 7

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_with_health_analysis(self, mock_settings, mock_um):
        from services.dashboard_service import get_dashboard_stats
        _setup_dashboard_mocks(mock_settings, mock_um)

        mock_c = MagicMock()
        side_effect = _make_dashboard_fetchone(has_bp=True, has_weight=True)
        # Replace last entry (health_analyses) with a dict-like mock that survives dict()
        health_mock = MagicMock()
        health_mock.keys.return_value = ['id', 'health_score', 'recommendations', 'days', 'created_at']
        health_mock.__getitem__ = lambda s, k: {'id': 1, 'health_score': 85, 'recommendations': None, 'days': 7, 'created_at': '2024-06-01'}.get(k, None)
        side_effect[-1] = health_mock

        mock_c.fetchone.side_effect = side_effect
        mock_c.fetchall.side_effect = _make_dashboard_fetchall()

        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis'] is not None

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_with_vo2max_and_bp_dates(self, mock_settings, mock_um):
        from services.dashboard_service import get_dashboard_stats
        _setup_dashboard_mocks(mock_settings, mock_um)

        mock_c = MagicMock()
        # Provide truthy values for glucose_stats[2],[3], bp_stats[3],[5], vo2max
        fetchone_vals = [
            (100,),           # total_records
            (6.0, 7.5, 9.0, 4.5),  # glucose_stats (4-tuple, all truthy)
            ('2024-06-01 20:00:00', '餐后2小时'),  # max_glucose_detail
            ('2024-06-01 07:15:00', '空腹'),       # min_glucose_detail
            (10.0, 350, 145, 3),    # exercise_stats (4-tuple)
            (45.0, '2024-06-01 07:00:00'),  # vo2max
            (42.0,),               # prev_vo2max
            (120.0, 80.0, 5, 135, 85, 110, 75),  # bp_stats (7-tuple)
            ('2024-06-01 08:00:00',),  # bp_max_date
            ('2024-06-01 22:00:00',),  # bp_min_date
            (70.0, 23.5, '2024-06-01 07:00:00'),  # latest_weight (3-tuple)
            (70.0,),               # avg_weight
            (70.0,),               # old_weight
            None,                   # health_analyses
        ]
        mock_c.fetchone.side_effect = fetchone_vals
        mock_c.fetchall.side_effect = _make_dashboard_fetchall()

        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_vo2max'] == 45.0
        assert result['prev_vo2max'] == 42.0
        assert result['bp_max_sys'] == 135
        assert result['bp_min_sys'] == 110
        assert result['latest_weight'] == 70.0



# ============================================================
# api_records 路由测试 (23% → 35%)
# ============================================================

class TestRecordsRoutes:
    """api_records 路由测试 — client_authenticated + mock get_db"""

    def test_get_stats(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db:
            mock_c = MagicMock()
            # fetchone call order in api_health_stats (gs[2]=None[3]=None skip, bs[3]=None skip, lw=None skip, vo2row=None skip):
            mock_c.fetchone.side_effect = [
                (6.0, 7.5, None, None),  # 1. gs: avg_fasting, avg_post2h, max, min
                (None,)*4,               # 2. es: total_distance, total_cal, avg_hr, count
                (None,)*7,               # 3. bs: avg_sys, avg_dia, count, max_sys, max_dia, min_sys, min_dia
                None,                    # 4. lw: latest_weight
                (None,),                 # 5. aw: avg_weight
                None,                    # 6. vo2row: latest vo2max
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db
            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200

    def test_delete_record(self, client_authenticated):
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/delete/999',
                headers={'X-Requested-With': 'XMLHttpRequest'})
            assert result.status_code == 200

    def test_get_record_not_found(self, client_authenticated):
        with patch('routes.api_records.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchone.return_value = None
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/record/99999')
            assert result.status_code == 404

    def test_export_csv(self, client_authenticated):
        result = client_authenticated.get('/export')
        assert result.status_code == 200

    def test_preview_import_no_file(self, client_authenticated):
        result = client_authenticated.post('/preview_import')
        assert result.status_code in (400, 500)  # 400 for no file


# ============================================================
# api_prediction 路由测试 (35% → 50%)
# ============================================================

class TestPredictionRoutesExtended:
    """api_prediction 更多路由测试"""

    def test_trigger_prediction_all(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg') as mock_fpg, \
             patch('routes.api_prediction.predict_post_exercise_glucose') as mock_ex, \
             patch('routes.api_prediction.predict_remaining_glucose_slots') as mock_slots:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            mock_ex.return_value = 5.5
            mock_slots.return_value = [{'type': '午餐后2小时', 'value': 7.0}]

            result = client_authenticated.post('/trigger_prediction', json={'type': 'all'})
            assert result.status_code == 200
            assert mock_fpg.called

    def test_trigger_prediction_fpg_only(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg') as mock_fpg:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/trigger_prediction', json={'type': '空腹'})
            assert result.status_code == 200

    def test_trigger_prediction_rate_limited(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.predict_morning_fpg') as mock_fpg:
            mock_fpg.side_effect = Exception("429 RESOURCE_EXHAUSTED retry in 30")
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db

            result = client_authenticated.post('/trigger_prediction', json={'type': '空腹'})
            assert result.status_code == 429

    def test_prediction_comparison_with_type(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db:
            mock_c = MagicMock()
            mock_c.fetchall.return_value = [
                ('空腹', '2024-06-01', 6.0, 6.5, -0.5)
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/prediction_comparison?days=30&type=空腹')
            assert result.status_code == 200

    def test_backfill_predictions(self, client_authenticated):
        with patch('routes.api_prediction.get_db') as mock_get_db, \
             patch('routes.api_prediction.backfill_post_exercise_predictions') as mock_backfill:
            mock_db = MagicMock()
            mock_get_db.return_value = mock_db
            mock_backfill.return_value = {'success': 5, 'skipped': 25, 'error': 0}

            result = client_authenticated.post('/backfill_predictions', json={'days': 7})
            assert result.status_code == 200
            assert result.json['data']['success'] == 5
