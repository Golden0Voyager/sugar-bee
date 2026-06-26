"""
services/ —— 遗漏分支补全测试

覆盖目标（基于现有全量测试后的剩余缺口）:
  - dashboard_service.py: L210-211 (except+rh), L220-228 (slot matched), L251-252 (CGM except)
  - prediction_service.py: L25-26 (record_hour except), L49 (generic post without hour),
    L143-144/161-162 (carbs except), L152-153 (dinner detection),
    L309 (post_exercise default target_date), L367-369 (backfill outer except),
    L450 (slot_time missing continue)
  - timeline_service.py: L88-91 (medication_plans), L95-96 (today BMR adj)
  - garmin_service.py: L168-170 (_map_activity except/continue)
"""
from unittest.mock import MagicMock, patch

# ============================================================
# dashboard_service.py — 剩余 9 行
#   L210-211: except Exception -> rh = -1  (时间格式异常)
#   L220-228: matched = True (各 slot 匹配分支)
#   L251-252: except (ValueError, IndexError) -> continue (CGM 时间解析异常)
# ============================================================

def _dash_fetchone_sequence(mock_c, has_glucose_maxmin=True, has_bp_stats=True,
                              has_weight=True, has_analysis=True, has_old_weight=True):
    """
    get_dashboard_stats 中 fetchone 的调用顺序和默认值。
    返回一个列表，用于 mock_c.fetchone.side_effect。
    """
    seq = [
        (10,),                           # 1. total_records
        (6.0, 7.5, 9.0 if has_glucose_maxmin else None, 4.5 if has_glucose_maxmin else None),  # 2. glucose_stats
    ]
    if has_glucose_maxmin:
        seq.append(('2024-06-01 08:00:00', '空腹'))  # 3. max detail
        seq.append(('2024-06-01 07:00:00', '空腹'))  # 4. min detail
    seq.append((10.0, 350, 145, 3))      # 5. exercise_stats
    seq.append(None)                     # 6. vo2max
    if has_bp_stats:
        seq.append((120.0, 80.0, 5, 135, 85, 110, 75))  # 7. bp_stats
        seq.append(('2024-06-01 08:00:00',))  # 8. bp_max timestamp
        seq.append(('2024-06-01 22:00:00',))  # 9. bp_min timestamp
    else:
        seq.append((None,)*7)            # 7. bp_stats (all None)
    if has_weight:
        seq.append((70.0, 22.0, '2024-06-15 07:00:00'))  # 10. latest_weight
        seq.append((None,))              # 11. avg_weight
        if has_old_weight:
            seq.append((68.0,))          # 12. old_weight
    else:
        seq.append(None)                 # 10. latest_weight
        seq.append((None,))              # 11. avg_weight
    if has_analysis:
        seq.append(None)                 # health_analyses
    else:
        seq.append(None)
    return seq


class TestDashboardSlotMatching:
    """覆盖 today_overview 中缺失的 slot 匹配分支"""

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_slot_all_matches(self, mock_settings, mock_um):
        """所有 7 个时间槽都有实测匹配记录，覆盖 L220-228"""
        from services.dashboard_service import get_dashboard_stats
        mock_um_instance = mock_um.return_value
        mock_um_instance.get_user_config.return_value = {'name': '测试', 'height': 170}

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = _dash_fetchone_sequence(
            mock_c, has_glucose_maxmin=False, has_bp_stats=False, has_weight=True
        )

        # compliance 需要 tuple 格式 (value, type)
        # today_records 需要 dict 格式（因为代码用 record['type']）
        today_records = [
            {'value': 5.5, 'type': '空腹',     'timestamp': '2024-06-11 07:15:00', 'is_predicted': 0},
            {'value': 7.0, 'type': '运动后',    'timestamp': '2024-06-11 08:45:00', 'is_predicted': 0},
            {'value': 8.5, 'type': '早餐后2小时','timestamp': '2024-06-11 11:00:00', 'is_predicted': 0},
            {'value': 7.8, 'type': '午餐后2小时','timestamp': '2024-06-11 14:30:00', 'is_predicted': 0},
            {'value': 6.0, 'type': '晚饭前',    'timestamp': '2024-06-11 17:30:00', 'is_predicted': 0},
            {'value': 8.0, 'type': '晚餐后2小时','timestamp': '2024-06-11 20:00:00', 'is_predicted': 0},
            {'value': 6.8, 'type': '睡前',     'timestamp': '2024-06-11 22:00:00', 'is_predicted': 0},
        ]
        mock_c.fetchall.side_effect = [
            [(70.0, 22.5, '2024-06-11 07:00:00')],  # today_weight (tuple: weight, bmi, timestamp)
            [(7.0, '餐后2小时'), (5.5, '空腹')],      # compliance (tuple: value, type)
            today_records,                            # today_overview
            [], [], [], [], [], [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        overview = {s['key']: s for s in result['today_overview']}
        assert overview['fasting']['value'] == 5.5
        assert overview['post_exercise']['value'] == 7.0
        assert overview['post_breakfast']['value'] == 8.5
        assert overview['post_lunch']['value'] == 7.8
        assert overview['pre_dinner']['value'] == 6.0
        assert overview['post_dinner']['value'] == 8.0
        assert overview['bedtime']['value'] == 6.8

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_invalid_time_format_exception(self, mock_settings, mock_um):
        """L210-211: except Exception -> rh = -1（时间部分包含':'但小时非数字）"""
        from services.dashboard_service import get_dashboard_stats
        mock_um_instance = mock_um.return_value
        mock_um_instance.get_user_config.return_value = {'name': '测试', 'height': 170}

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = _dash_fetchone_sequence(
            mock_c, has_glucose_maxmin=False, has_bp_stats=False, has_weight=True
        )

        # timestamp 包含 ':' 但小时部分无法解析为 int
        bad_record = {'value': 5.5, 'type': '空腹',
                      'timestamp': '2024-06-11 bad:00', 'is_predicted': 0}
        mock_c.fetchall.side_effect = [
            [],
            [(7.0, '餐后2小时')],
            [bad_record],   # today_records
            [], [], [], [], [], [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting['value'] == 5.5

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_cgm_time_parse_exception(self, mock_settings, mock_um):
        """L251-252: except (ValueError, IndexError) -> continue（CGM 时间解析异常）"""
        from services.dashboard_service import get_dashboard_stats
        mock_um_instance = mock_um.return_value
        mock_um_instance.get_user_config.return_value = {'name': '测试', 'height': 170}

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = _dash_fetchone_sequence(
            mock_c, has_glucose_maxmin=False, has_bp_stats=False, has_weight=True
        )

        # CGM record 时间部分包含 ':' 但无法解析为 int
        cgm_bad = {'value': 6.0, 'type': 'CGM',
                   'timestamp': '2024-06-11 ab:cd', 'is_predicted': 0}
        normal_cgm = {'value': 6.5, 'type': 'CGM',
                      'timestamp': '2024-06-11 07:15:00', 'is_predicted': 0}
        mock_c.fetchall.side_effect = [
            [],
            [(7.0, '餐后2小时')],
            [cgm_bad, normal_cgm],  # today_records: bad CGM first, then normal CGM
            [], [], [], [], [], [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        fasting = [s for s in result['today_overview'] if s['key'] == 'fasting'][0]
        assert fasting['value'] == 6.5
        assert fasting.get('cgm') is True


# ============================================================
# prediction_service.py — 剩余 14 行
#   L25-26:  record_hour except (ValueError, IndexError)
#   L49:     '餐后' type without record_hour
#   L143-144: total_carbs except (ValueError, TypeError) in records loop
#   L152-153: has_dinner = True in predict_morning_fpg
#   L161-162: default_meals carbs except
#   L309:     predict_post_exercise_glucose default target_date
#   L367-369: backfill outer except
#   L450:     predict_remaining_glucose_slots slot_time continue
# ============================================================

class TestPredictionLinkExceptions:
    """link_prediction_to_real_record 遗漏分支"""

    def test_record_hour_parse_exception(self):
        """L25-26: record_timestamp 有空格但时间部分无法解析"""
        from services.prediction_service import link_prediction_to_real_record
        mock_c = MagicMock()
        mock_c.fetchone.return_value = (10, 7.0)  # found prediction
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = link_prediction_to_real_record(
            mock_db, real_record_id=5, user_id=1,
            record_date='2024-06-01', record_type='餐后2小时',
            real_value=8.0, record_timestamp='2024-06-01 abc'
        )
        assert result is not None

    def test_generic_post_without_hour(self):
        """L49: '餐后' type 且 record_hour=None（无 record_timestamp）"""
        from services.prediction_service import link_prediction_to_real_record
        mock_c = MagicMock()
        mock_c.fetchone.return_value = (10, 7.0)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = link_prediction_to_real_record(
            mock_db, real_record_id=5, user_id=1,
            record_date='2024-06-01', record_type='餐后测试',
            real_value=8.0, record_timestamp=None
        )
        assert result is not None

    def test_dinner_type(self):
        """L152-153: '晚餐' in record_type -> has_dinner = True (via type_condition)"""
        from services.prediction_service import link_prediction_to_real_record
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None  # no prediction found
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = link_prediction_to_real_record(
            mock_db, real_record_id=5, user_id=1,
            record_date='2024-06-01', record_type='晚餐后2小时',
            real_value=8.0, record_timestamp='2024-06-01 20:00:00'
        )
        assert result is None  # no matching prediction


class TestPredictionMorningFpg:
    """predict_morning_fpg 中遗漏分支"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    @patch('services.prediction_service.settings.get_ai_system_prompt')
    def test_carbs_exception_in_records(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        """L143-144: carbs_grams 无法转为 float"""
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_prompt.return_value = ''
        mock_ai.return_value = '{"predicted_value": 6.2, "reasoning": "test"}'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [
            [(6.5, '空腹', '2024-06-01 07:15:00')],  # yesterday_glucose
            [('午餐', 500, '2024-06-01 12:00:00', 'bad_carbs', 70)],  # carbs_grams = 'bad_carbs'
            [(6.0, '2024-06-01 07:15:00')],          # recent_fpg
            [],                                       # prediction_history
            [],                                       # medications
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        predict_morning_fpg(mock_db, user_id=1)
        mock_db.commit.assert_called_once()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    @patch('services.prediction_service.settings.get_ai_system_prompt')
    def test_carbs_exception_in_default_meals(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        """L161-162: default_meals 中 carbs_grams 无法转为 float"""
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {
            'default_meals': {
                'breakfast': {'enabled': True, 'calories': 300,
                              'carbs_grams': 'bad_value', 'gi_value': 65},
                'lunch': {'enabled': False},
                'dinner': {'enabled': False},
            }
        }
        mock_prompt.return_value = ''
        mock_ai.return_value = '{"predicted_value": 6.0, "reasoning": "test"}'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [
            [(7.0, '餐后2小时', '2024-06-01 13:30:00')],
            [],  # no calories -> default_meals used
            [(6.0, '2024-06-01 07:15:00')],
            [],
            [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        predict_morning_fpg(mock_db, user_id=1)
        mock_db.commit.assert_called_once()

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    @patch('services.prediction_service.settings.get_ai_system_prompt')
    def test_dinner_detection_by_hour(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        """L152-153: '晚餐' in record_type -> has_dinner = True"""
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_prompt.return_value = ''
        mock_ai.return_value = '{"predicted_value": 6.0, "reasoning": "test"}'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [
            [(7.0, '餐后2小时', '2024-06-01 13:30:00')],  # yesterday_glucose
            [('晚餐', 700, '2024-06-01 18:00:00', 50, 65)],  # dinner type
            [(6.0, '2024-06-01 07:15:00')],
            [],
            [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        predict_morning_fpg(mock_db, user_id=1)
        mock_db.commit.assert_called_once()


class TestPredictionPostExerciseDefaultDate:
    """predict_post_exercise_glucose 默认 target_date"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.is_valid_prediction')
    def test_default_target_date(self, mock_valid, mock_ai):
        """L309: target_date=None -> datetime.now()"""
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = '{"predicted_value": 5.5, "reasoning": "test"}'
        mock_valid.return_value = True

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [None, (6.0,), (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00')]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = predict_post_exercise_glucose(mock_db, user_id=1)
        assert result == 5.5


class TestPredictionBackfillOuterExcept:
    """backfill_post_exercise_predictions 外层 except"""

    def test_outer_exception_caught(self):
        """L367-369: datetime.now 引发异常 -> 外层 except 捕获"""
        from services.prediction_service import backfill_post_exercise_predictions
        mock_db = MagicMock()
        with patch('services.prediction_service.datetime.datetime') as mock_dt:
            mock_dt.now.side_effect = Exception("time failure")
            result = backfill_post_exercise_predictions(mock_db, user_id=1, days=3)
        assert result['success'] == 0
        assert result['error'] == 0


class TestPredictionRemainingSlotsContinue:
    """predict_remaining_glucose_slots slot_time missing"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_pred_type_no_matching_slot(self, mock_ai):
        """L450: AI 返回的类型与任何 slot 不匹配 -> continue"""
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.return_value = '[{"type": "未知类型", "value": 6.0, "reasoning": "test"}]'
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [(6.5, '空腹', '2024-06-11 07:15:00')],  # measured records
            [('空腹',)],                               # measured types
        ]
        mock_c.fetchone.return_value = None
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = predict_remaining_glucose_slots(mock_db, user_id=1, force_update=True)
        assert result == []


# ============================================================
# timeline_service.py — 剩余 6 行
#   L88-91:  medication_plans 循环体
#   L95-96:  今日 BMR 按比例调整
# ============================================================

class TestTimelineMedicationPlans:
    """build_timeline 中 medication_plans 匹配和今日 BMR"""

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_medication_plan_matched_and_today_bmr(self, mock_config, mock_bmr):
        """L88-91: medication_plan 加入 data; L95-96: 今日 BMR 按比例调整"""
        from services.timeline_service import build_timeline
        from utils.timezone import now as app_now
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}

        now = app_now()
        today_str = now.strftime('%Y-%m-%d')

        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': f'{today_str} 07:15:00',
                 'is_predicted': 0, 'is_verified': 0, 'calories': None, 'distance': None,
                 'systolic_pressure': None},
            ],
            [
                {'id': 1, 'medication_name': '二甲双胍', 'is_active': 1,
                 'start_date': today_str, 'end_date': None},
            ],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)

        assert len(sorted_dates) == 1
        data = sorted_dates[0]['data']
        assert len(data['medication_plans']) == 1
        assert data['medication_plans'][0]['medication_name'] == '二甲双胍'

        current_minutes = now.hour * 60 + now.minute
        expected_bmr = int(1600 * (current_minutes / 1440))
        assert data['stats']['cal_out_bmr'] == expected_bmr


# ============================================================
# garmin_service.py — 剩余 3 行
#   L168-170: _map_activity 异常 -> except / traceback / continue
# ============================================================

class TestGarminMapActivityException:
    """sync_activities -> _map_activity 异常处理"""

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service._map_activity')
    @patch('services.garmin_service.get_raw_conn')
    def test_map_activity_exception_continues(self, mock_get_raw_conn, mock_map, mock_get_client):
        """L168-170: _map_activity 抛异常 -> except/traceback/continue"""
        from services.garmin_service import sync_activities
        mock_map.side_effect = Exception("mapping failed")

        mock_client = MagicMock()
        mock_client.get_activities_by_date.return_value = [{'activityId': 1001}]
        mock_get_client.return_value = mock_client

        mock_conn = MagicMock()
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_c
        mock_get_raw_conn.return_value = mock_conn

        result = sync_activities(user_id=1, days=30)
        assert result['inserted'] == 0
        assert result['skipped'] == 0
        assert result['total'] == 1
