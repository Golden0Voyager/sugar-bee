"""
Service 层 mock 测试 — 覆盖 prediction_service, timeline_service, health_service, dashboard_service
"""
import json
from unittest.mock import patch, MagicMock


# ============================================================
# prediction_service 测试
# ============================================================

class TestLinkPredictionToRealRecord:
    """类型匹配逻辑测试"""

    def _make_cursor(self, prediction_row=None):
        mock_c = MagicMock()
        mock_c.fetchone.return_value = prediction_row
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        return mock_db, mock_c

    def test_invalid_glucose_value_skips(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, _ = self._make_cursor()
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', 50.0)
        assert result is None

    def test_links_fasting_prediction(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(10, 5.5))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', 6.0, '2024-01-01 07:15:00')
        assert result is not None
        assert result['predicted_value'] == 5.5
        assert abs(result['error'] - 0.5) < 0.01

    def test_links_postmeal_2h(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(20, 7.2))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后2小时', 8.0, '2024-01-01 13:30:00')
        assert result is not None
        assert abs(result['error'] - 0.8) < 0.01

    def test_links_bedtime(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(30, 6.8))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '睡前', 6.5, '2024-01-01 22:00:00')
        assert result is not None

    def test_links_premeal(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(40, 5.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '晚饭前', 5.5, '2024-01-01 17:30:00')
        assert result is not None

    def test_links_breakfast_post(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(50, 8.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '早餐后', 8.5, '2024-01-01 11:00:00')
        assert result is not None

    def test_links_lunch_post(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(60, 7.5))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '午餐后', 8.0, '2024-01-01 14:30:00')
        assert result is not None

    def test_links_dinner_post(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(70, 9.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '晚餐后', 9.5, '2024-01-01 20:00:00')
        assert result is not None

    def test_no_matching_prediction(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, _ = self._make_cursor(prediction_row=None)
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', 6.0, '2024-01-01 07:15:00')
        assert result is None

    def test_generic_postmeal_matches_breakfast_by_hour(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(80, 7.8))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后', 8.5, '2024-01-01 11:30:00')
        assert result is not None

    def test_generic_postmeal_matches_lunch_by_hour(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(90, 8.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后', 8.2, '2024-01-01 14:00:00')
        assert result is not None

    def test_postmeal_1h(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(100, 10.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后1小时', 10.5, '2024-01-01 12:00:00')
        assert result is not None

    def test_blood_pressure_not_matched(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db, _ = self._make_cursor()
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹血压', 120, '2024-01-01 07:15:00')
        assert result is None

    def test_exception_returns_none(self):
        from services.prediction_service import link_prediction_to_real_record
        mock_db = MagicMock()
        mock_db.cursor.side_effect = Exception("DB error")
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', 6.0)
        assert result is None


class TestCheckDailyDataComplete:
    """数据完备性检查测试"""

    def test_all_complete(self):
        from services.prediction_service import check_daily_data_complete
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [(5,), (2,), (1,)]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = check_daily_data_complete(mock_db, user_id=1)
        assert result['complete'] is True
        assert result['has_glucose'] is True
        assert result['has_blood_pressure'] is True
        assert result['has_exercise'] is True

    def test_nothing_complete(self):
        from services.prediction_service import check_daily_data_complete
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [(0,), (0,), (0,)]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = check_daily_data_complete(mock_db, user_id=1)
        assert result['complete'] is False

    def test_only_glucose(self):
        from services.prediction_service import check_daily_data_complete
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [(3,), (0,), (0,)]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = check_daily_data_complete(mock_db, user_id=1)
        assert result['has_glucose'] is True
        assert result['has_blood_pressure'] is False

    def test_exception_returns_false(self):
        from services.prediction_service import check_daily_data_complete
        mock_db = MagicMock()
        mock_db.cursor.side_effect = Exception("DB error")
        result = check_daily_data_complete(mock_db, user_id=1)
        assert result['complete'] is False


class TestPredictMorningFPG:
    """空腹血糖预测测试"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    def test_predicts_when_no_existing(self, mock_bmr, mock_config, mock_ai):
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_ai.return_value = '{"predicted_value": 6.2, "reasoning": "趋势预测"}'
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.return_value = []
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        predict_morning_fpg(mock_db, user_id=1)
        mock_db.commit.assert_called()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    def test_skips_when_already_predicted(self, mock_bmr, mock_config, mock_ai):
        from services.prediction_service import predict_morning_fpg
        mock_c = MagicMock()
        mock_c.fetchone.return_value = (1,)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        predict_morning_fpg(mock_db, user_id=1)
        mock_ai.assert_not_called()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    def test_skips_invalid_prediction(self, mock_bmr, mock_config, mock_ai):
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_ai.return_value = '{"predicted_value": 99.0, "reasoning": "不合理"}'
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.return_value = []
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        predict_morning_fpg(mock_db, user_id=1)
        exec_calls = [str(c) for c in mock_c.execute.call_args_list]
        insert_calls = [c for c in exec_calls if 'INSERT' in c]
        assert len(insert_calls) == 0


class TestPredictPostExerciseGlucose:
    """运动后血糖预测测试"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_returns_none_when_no_fpg(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [None, None]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_returns_none_when_no_exercise(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [None, (6.0,)]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_predicts_successfully(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = '{"predicted_value": 5.2, "reasoning": "正常下降"}'
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            None,
            (6.2,),
            (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00'),
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result == 5.2
        mock_db.commit.assert_called()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_skips_existing_measured(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_c = MagicMock()
        mock_c.fetchone.return_value = (1, False)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None
        mock_ai.assert_not_called()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_updates_existing_prediction(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = '{"predicted_value": 5.5, "reasoning": "修正"}'
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (10, True),
            (6.5,),
            (3.0, '00:25:00', 135, 250, '2024-06-01 06:30:00'),
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01', force_update=True)
        assert result == 5.5


class TestPredictRemainingSlots:
    """剩余时间槽预测测试"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_no_measured_returns_flag(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        mock_c = MagicMock()
        mock_c.fetchall.return_value = []  # 两次 fetchall 都返回空
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert result == 'no_measured'

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_all_measured_returns_flag(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        # 第一次 fetchall：全量记录 (value, type, timestamp)
        records_3col = [
            (6.5, '空腹', '2024-06-01 07:15:00'),
            (8.0, '早餐后2小时', '2024-06-01 11:00:00'),
            (7.5, '午餐后2小时', '2024-06-01 14:30:00'),
            (6.0, '晚饭前', '2024-06-01 17:30:00'),
            (8.5, '晚餐后2小时', '2024-06-01 20:00:00'),
            (6.2, '睡前', '2024-06-01 22:00:00'),
        ]
        # 第二次 fetchall：仅 type 列 (type,) — row[0] 是类型字符串
        types_1col = [('空腹',), ('早餐后2小时',), ('午餐后2小时',), ('晚饭前',), ('晚餐后2小时',), ('睡前',)]
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [records_3col, types_1col]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert result == 'all_measured'

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_predicts_missing_slots(self, mock_ai):
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.return_value = json.dumps([
            {"type": "午餐后2小时", "value": 7.5, "reasoning": "预测"},
            {"type": "晚饭前", "value": 5.8, "reasoning": "预计"},
        ])
        partial_3col = [(6.5, '空腹', '2024-06-01 07:15:00'), (7.2, '早餐后2小时', '2024-06-01 11:00:00')]
        partial_1col = [('空腹',), ('早餐后2小时',)]
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [partial_3col, partial_1col]
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert isinstance(result, list)
        assert len(result) == 2
        mock_db.commit.assert_called()


class TestBackfillPredictions:
    """回填预测测试"""

    @patch('services.prediction_service.predict_post_exercise_glucose')
    def test_backfill_counts_stats(self, mock_predict):
        from services.prediction_service import backfill_post_exercise_predictions
        mock_predict.side_effect = [5.0, None, 5.2, None, None]
        mock_db = MagicMock()
        result = backfill_post_exercise_predictions(mock_db, user_id=1, days=5)
        assert result['success'] == 2
        assert result['skipped'] == 3


# ============================================================
# timeline_service 测试
# ============================================================

class TestBuildTimeline:
    """时间线构建测试"""

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_empty_records(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.return_value = []
        sorted_dates, records = build_timeline(mock_c, user_id=1, days=7)
        assert sorted_dates == []
        assert records == []

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_basic_timeline(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 8.0, 'type': '餐后2小时', 'timestamp': '2024-06-01 13:30:00', 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        assert len(sorted_dates) == 1
        assert sorted_dates[0]['data']['stats']['glucose_count'] == 2

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_calorie_grouping(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 0, 'type': '午餐', 'timestamp': '2024-06-01 11:30:00', 'calories': 500, 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 0, 'type': '跑步', 'timestamp': '2024-06-01 07:00:00', 'calories': 350, 'distance': 5.0, 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        stats = sorted_dates[0]['data']['stats']
        assert stats['cal_in'] == 500
        assert stats['cal_out_exercise'] == 350

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_trend_calculation(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        # Trend processes in original order (chronological), then sorts reverse for return.
        # Process: 6-01(6.0)->flat, 6-02(6.5)->up(vs 6.0), 6-03(5.8)->down(vs 6.5)
        # After sort reverse: fasting[0]=6-03(down), fasting[1]=6-02(up), fasting[2]=6-01(flat)
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.0, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-02 07:15:00', 'is_predicted': 0, 'is_verified': 0},
                {'id': 3, 'value': 5.8, 'type': '空腹', 'timestamp': '2024-06-03 07:15:00', 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, records = build_timeline(mock_c, user_id=1, days=7)
        fasting = [r for r in records if r['type'] == '空腹']
        assert len(fasting) == 3
        # After reverse sort: [6-03(down), 6-02(up), 6-01(flat)]
        assert fasting[0]['trend_dir'] == 'down'
        assert abs(fasting[0]['trend'] - 0.7) < 0.01
        assert fasting[1]['trend_dir'] == 'up'
        assert abs(fasting[1]['trend'] - 0.5) < 0.01
        assert fasting[2]['trend_dir'] == 'flat'

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_multiple_dates(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 6.3, 'type': '空腹', 'timestamp': '2024-06-02 07:15:00', 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        assert len(sorted_dates) == 2

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_skips_non_glucose_for_avg(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 0, 'type': '午餐', 'timestamp': '2024-06-01 11:30:00', 'calories': 500, 'is_predicted': 0, 'is_verified': 0},
                {'id': 3, 'value': 120, 'type': '血压测量', 'timestamp': '2024-06-01 08:00:00', 'systolic_pressure': 120, 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        stats = sorted_dates[0]['data']['stats']
        assert stats['glucose_count'] == 1
        assert stats['avg_glucose'] == 6.5


# ============================================================
# health_service 测试
# ============================================================

class TestHealthAnalysis:
    """健康分析测试"""

    @patch('services.health_service.AI_AVAILABLE', False)
    def test_returns_error_when_no_ai(self):
        from services.health_service import generate_health_analysis
        result = generate_health_analysis(MagicMock(), user_id=1)
        assert result == {"success": False, "error": "未配置 AI API Key", "error_type": "ai_unavailable"}

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.call_ai')
    def test_skips_duplicate_auto_analysis(self, mock_ai):
        from services.health_service import generate_health_analysis
        mock_c = MagicMock()
        mock_c.fetchone.return_value = (1,)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = generate_health_analysis(mock_db, user_id=1, is_auto=True)
        assert result == {"skipped": True, "message": "今日已生成分析"}

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.call_ai')
    def test_generates_with_data(self, mock_ai):
        from services.health_service import generate_health_analysis
        mock_ai.return_value = "## 分析\n血糖控制良好。\n综合健康得分: 85"
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [None]
        mock_c.fetchall.side_effect = [
            [(6.5, '空腹', '2024-06-01 07:15:00', None, None)],
            [(120, 80, 72, '2024-06-01 08:00:00', 98)],
            [],
            [(300, 45.0, 65, '', '', '早餐', '2024-06-01 09:00:00')],
            [('二甲双胍', '500mg', '1', '片', 2, '餐前', 'long_term', '2024-01-01', '')],
            [], [], [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = generate_health_analysis(mock_db, user_id=1, is_auto=False)
        assert result['success'] is True
        assert result['score'] == 85

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.call_ai')
    def test_default_score_when_no_match(self, mock_ai):
        from services.health_service import generate_health_analysis
        mock_ai.return_value = "一切正常"
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [[], [], [], [], [], [], [], []]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = generate_health_analysis(mock_db, user_id=1)
        assert result['score'] == 80

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.call_ai')
    def test_auto_trigger(self, mock_ai):
        from services.health_service import auto_trigger_health_analysis
        mock_ai.return_value = "综合健康得分: 90"
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [[], [], [], [], [], [], [], []]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = auto_trigger_health_analysis(mock_db, user_id=1)
        assert result['success'] is True
        assert result['score'] == 90


# ============================================================
# dashboard_service 测试
# ============================================================

class TestDashboardStats:
    """仪表盘统计测试"""

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_returns_empty_for_new_user(self, mock_settings, mock_um):
        from services.dashboard_service import get_dashboard_stats
        mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
        mock_settings.get_badge_for_rate.return_value = {'key': 'encourage', 'icon': '💪'}
        mock_settings.GLUCOSE_TARGETS = {}
        mock_settings.BADGE_SYSTEM = {}
        mock_settings.check_glucose_compliance.return_value = {'is_compliant': True}
        mock_settings.calculate_bmi.return_value = None
        mock_um_instance = MagicMock()
        mock_um_instance.get_user_config.return_value = {'name': '测试'}
        mock_um.return_value = mock_um_instance

        mock_c = MagicMock()
        # fetchone 调用序列（跳过因 None 而被 bypass 的 if 块）：
        # 1.total_records  2.glucose_stats  (skip 2:glucose_stats[2],[3] are None)
        # 3.exercise_stats  4.vo2max  (skip 1:vo2max is None)
        # 5.bp_stats  (skip 2:bp_stats[3],[5] are None)
        # 6.latest_weight  7.avg_weight  (skip 1:latest_weight is None)
        # 8.health_analyses
        mock_c.fetchone.side_effect = [
            (None,),           # 1: total_records
            (None,)*4,         # 2: glucose_stats
            (None,)*4,         # 3: exercise_stats
            None,              # 4: vo2max
            (None,)*7,         # 5: bp_stats
            None,              # 6: latest_weight
            (None,),           # 7: avg_weight
            None,              # 8: health_analyses
        ]
        mock_c.fetchall.return_value = []
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['compliance'] == 0

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_compliance_and_badge(self, mock_settings, mock_um):
        from services.dashboard_service import get_dashboard_stats
        mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
        mock_settings.get_badge_for_rate.return_value = {'key': 'gold', 'icon': '🥇'}
        mock_settings.GLUCOSE_TARGETS = {}
        mock_settings.BADGE_SYSTEM = {}
        mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
        mock_settings.calculate_bmi.return_value = None
        mock_um_instance = MagicMock()
        mock_um_instance.get_user_config.return_value = {'name': '用户'}
        mock_um.return_value = mock_um_instance

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (None,),           # 1: total_records
            (None,)*4,         # 2: glucose_stats
            (None,)*4,         # 3: exercise_stats
            None,              # 4: vo2max
            (None,)*7,         # 5: bp_stats
            None,              # 6: latest_weight
            (None,),           # 7: avg_weight
            None,              # 8: health_analyses
        ]
        # fetchall calls: 1.today_weight  2.compliance  3.today_overview
        # 4.today_exercises  5.today_bps  6.all_meds  7.taken_logs  8.temp_meds
        mock_c.fetchall.side_effect = [
            [],                                              # 1: today_weight
            [(7.0, '空腹'), (6.5, '餐后2小时')],               # 2: compliance
            [], [], [], [], [], [],                          # 3-8: rest empty
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['compliance'] == 100
        assert result['compliance_badge']['key'] == 'gold'
