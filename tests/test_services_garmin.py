"""garmin_service.py 完整测试 + 其他 service 遗漏分支

覆盖目标:
  - garmin_service.py: 0% → ~85% (所有函数/异常/边界)
  - health_service.py: 95% → 100% (174-176 except 块)
  - timeline_service.py: 89% → ~95% (days=None, avg_glucose, net_cal)
  - prediction_service.py: 85% → ~90% (各遗漏分支)
"""
import os
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# garmin_service.py
# ============================================================

class TestGarminNoProxy:
    """_no_proxy() context manager"""

    def test_clears_proxy_env_vars(self):
        from services.garmin_service import _no_proxy
        os.environ['http_proxy'] = 'http://proxy:8080'
        os.environ['https_proxy'] = 'https://proxy:8080'
        with _no_proxy():
            assert 'http_proxy' not in os.environ
            assert 'https_proxy' not in os.environ
        # Restored after exit
        assert os.environ['http_proxy'] == 'http://proxy:8080'
        assert os.environ['https_proxy'] == 'https://proxy:8080'
        del os.environ['http_proxy']
        del os.environ['https_proxy']

    def test_no_proxy_vars_does_nothing(self):
        from services.garmin_service import _no_proxy
        # Ensure no proxy vars present
        for k in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
            os.environ.pop(k, None)
        with _no_proxy():
            pass  # Should not raise

    def test_handles_exception_gracefully(self):
        from services.garmin_service import _no_proxy
        os.environ['http_proxy'] = 'http://proxy:8080'
        try:
            with _no_proxy():
                assert 'http_proxy' not in os.environ
                raise ValueError("test error")
        except ValueError:
            pass
        # Still restored after exception
        assert os.environ['http_proxy'] == 'http://proxy:8080'
        del os.environ['http_proxy']


class TestGarminTypeMap:
    """TYPE_MAP 常量验证"""

    def test_running_types(self):
        from services.garmin_service import TYPE_MAP
        assert TYPE_MAP['running'] == '跑步'
        assert TYPE_MAP['treadmill_running'] == '跑步'
        assert TYPE_MAP['trail_running'] == '跑步'

    def test_walking_types(self):
        from services.garmin_service import TYPE_MAP
        assert TYPE_MAP['walking'] == '走路'
        assert TYPE_MAP['hiking'] == '走路'

    def test_cycling_types(self):
        from services.garmin_service import TYPE_MAP
        assert TYPE_MAP['cycling'] == '骑行'
        assert TYPE_MAP['road_biking'] == '骑行'
        assert TYPE_MAP['indoor_cycling'] == '骑行'

    def test_swimming_types(self):
        from services.garmin_service import TYPE_MAP
        assert TYPE_MAP['swimming'] == '游泳'
        assert TYPE_MAP['lap_swimming'] == '游泳'

    def test_fitness_types(self):
        from services.garmin_service import TYPE_MAP
        assert TYPE_MAP['strength_training'] == '健身'
        assert TYPE_MAP['yoga'] == '健身'

    def test_unknown_type_falls_to_generic(self):
        from services.garmin_service import TYPE_MAP
        assert 'skateboarding' not in TYPE_MAP

    def test_missing_activity_type_falls_back(self):
        from services.garmin_service import _map_activity
        result = _map_activity({'startTimeLocal': '2024-06-01 10:00:00'}, user_id=1)
        # type defaults to '运动' for unknown/missing activityType
        assert result['type'] == '运动'


class TestGarminMapActivity:
    """_map_activity() 完整测试"""

    def _make_act(self, **overrides):
        base = {
            'activityId': 12345,
            'activityType': {'typeKey': 'running'},
            'startTimeLocal': '2024-06-01 07:00:00',
            'duration': 1800,           # 30 min
            'distance': 5000,            # 5 km
            'averageSpeed': 2.77778,     # ~10 km/h → 6:00/km pace
            'maxSpeed': 5.0,             # 5 m/s → 3:20/km max pace
            'averageHR': 145,
            'maxHR': 175,
            'averageRunningCadenceInStepsPerMinute': 160,
            'calories': 350,
            'vO2MaxValue': 42.0,
            'steps': 5000,
            'activityName': '晨跑',
        }
        base.update(overrides)
        return base

    def test_basic_mapping(self):
        from services.garmin_service import _map_activity
        result = self._make_act()
        r = _map_activity(result, user_id=1)
        assert r['user_id'] == 1
        assert r['type'] == '跑步'
        assert r['timestamp'] == '2024-06-01 07:00:00'
        assert r['distance'] == 5.0
        assert r['duration'] == '30min'
        assert r['heart_rate'] == 145
        assert r['max_heart_rate'] == 175
        assert r['cadence'] == 160
        assert r['calories'] == 350
        assert r['vo2max'] == 42.0
        assert r['steps'] == 5000
        assert r['external_id'] == '12345'
        assert r['source'] == 'garmin'
        assert r['notes'] == '晨跑'

    def test_pace_calculation(self):
        from services.garmin_service import _map_activity
        # 1000/4 = 250 sec/km = 4:10 pace
        r = _map_activity(self._make_act(averageSpeed=4.0), user_id=1)
        assert r['pace'] == '04:10'

    def test_max_pace_calculation(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(maxSpeed=5.0), user_id=1)
        # 1000 / 5.0 = 200 sec/km = 3:20
        assert r['max_pace'] == '03:20'

    def test_zero_duration_returns_none(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(duration=0), user_id=1)
        assert r['duration'] is None

    def test_zero_distance_returns_none(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(distance=0), user_id=1)
        assert r['distance'] is None

    def test_zero_avg_speed_returns_none_pace(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(averageSpeed=0), user_id=1)
        assert r['pace'] is None

    def test_zero_max_speed_returns_none_max_pace(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(maxSpeed=0), user_id=1)
        assert r['max_pace'] is None

    def test_no_heart_rate_returns_none(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(averageHR=None), user_id=1)
        assert r['heart_rate'] is None

    def test_no_calories_returns_zero(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(calories=None), user_id=1)
        assert r['calories'] == 0

    def test_walking_type(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(activityType={'typeKey': 'walking'}), user_id=1)
        assert r['type'] == '走路'

    def test_no_activity_name(self):
        from services.garmin_service import _map_activity
        r = _map_activity(self._make_act(activityName=None), user_id=1)
        assert r['notes'] == ''


class TestGarminGetClient:
    """_get_client() — token 登录"""

    @patch('services.garmin_service.os.path.isfile')
    def test_no_token_file_raises(self, mock_isfile):
        from services.garmin_service import _get_client
        mock_isfile.return_value = False
        with pytest.raises(RuntimeError, match='未找到 Garmin token'):
            _get_client()

    @patch('services.garmin_service.os.path.isfile')
    @patch('services.garmin_service.Garmin')
    def test_login_success(self, mock_garmin_cls, mock_isfile):
        from services.garmin_service import _get_client
        mock_isfile.return_value = True
        mock_client = MagicMock()
        mock_garmin_cls.return_value = mock_client

        result = _get_client()
        assert result == mock_client
        mock_client.login.assert_called_once()

    @patch('services.garmin_service.os.path.isfile')
    @patch('services.garmin_service.Garmin')
    def test_login_failure_raises(self, mock_garmin_cls, mock_isfile):
        from services.garmin_service import _get_client
        mock_isfile.return_value = True
        mock_client = MagicMock()
        mock_client.login.side_effect = Exception("token expired")
        mock_garmin_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match='Garmin token 失效'):
            _get_client()

    def test_is_cn_flag(self):
        with patch('services.garmin_service.os.path.isfile', return_value=True), \
             patch('services.garmin_service.Garmin') as mock_garmin_cls:
            from services.garmin_service import _get_client
            mock_client = MagicMock()
            mock_garmin_cls.return_value = mock_client
            import os as os_mod
            # Clear GARMIN_EMAIL + set GARMIN_IS_CN
            saved_mail = os_mod.environ.pop('GARMIN_EMAIL', None)
            os_mod.environ['GARMIN_IS_CN'] = '1'
            try:
                _get_client()
                mock_garmin_cls.assert_called_with(email=None, is_cn=True)
            finally:
                os_mod.environ.pop('GARMIN_IS_CN', None)
                if saved_mail is not None:
                    os_mod.environ['GARMIN_EMAIL'] = saved_mail

    def test_passes_email(self):
        with patch('services.garmin_service.os.path.isfile', return_value=True), \
             patch('services.garmin_service.Garmin') as mock_garmin_cls, \
             patch.dict('os.environ', {'GARMIN_EMAIL': 'test@example.com'}):
            from services.garmin_service import _get_client
            mock_client = MagicMock()
            mock_garmin_cls.return_value = mock_client
            _get_client()
            mock_garmin_cls.assert_called_with(email='test@example.com', is_cn=False)


class TestGarminSyncActivities:
    """sync_activities() 完整测试"""

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    def test_no_activities(self, mock_get_raw_conn, mock_get_client):
        from services.garmin_service import sync_activities
        mock_get_raw_conn.return_value = MagicMock()
        mock_client = MagicMock()
        mock_client.get_activities_by_date.return_value = []
        mock_get_client.return_value = mock_client

        result = sync_activities(user_id=1, days=30)
        assert result == {'inserted': 0, 'skipped': 0, 'total': 0}

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    def test_inserts_new_activities(self, mock_get_raw_conn, mock_get_client):
        from services.garmin_service import sync_activities
        mock_client = MagicMock()
        mock_act = {
            'activityId': 1001,
            'activityType': {'typeKey': 'running'},
            'startTimeLocal': '2024-06-01 07:00:00',
            'duration': 1800,
            'distance': 5000,
            'averageSpeed': 4.0,
            'maxSpeed': 5.0,
            'averageHR': 145,
            'maxHR': 175,
            'averageRunningCadenceInStepsPerMinute': 160,
            'calories': 350,
            'vO2MaxValue': 42.0,
            'steps': 5000,
            'activityName': '晨跑',
        }
        mock_client.get_activities_by_date.return_value = [mock_act]
        mock_get_client.return_value = mock_client
        # Setup DB mock: no existing prediction → insert proceeds
        mock_conn = MagicMock()
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_c
        mock_get_raw_conn.return_value = mock_conn

        result = sync_activities(user_id=1, days=30)
        assert result['inserted'] == 1
        assert result['skipped'] == 0
        assert result['total'] == 1

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    def test_skips_duplicates(self, mock_get_raw_conn, mock_get_client):
        from services.garmin_service import sync_activities
        mock_client = MagicMock()
        mock_act = {
            'activityId': 1001,
            'activityType': {'typeKey': 'running'},
            'startTimeLocal': '2024-06-01 07:00:00',
            'duration': 1800, 'distance': 5000,
            'averageSpeed': 2.77778, 'averageHR': 145, 'calories': 350,
        }
        mock_client.get_activities_by_date.return_value = [mock_act, mock_act]
        mock_get_client.return_value = mock_client
        # First activity: no existing → insert; Second: existing → skip
        mock_conn = MagicMock()
        mock_c = MagicMock()
        # fetchone returns None first time (no existing), then (1,) second time (exists)
        mock_c.fetchone.side_effect = [None, (1,)]
        mock_conn.cursor.return_value = mock_c
        mock_get_raw_conn.return_value = mock_conn

        result = sync_activities(user_id=1, days=30)
        assert result['inserted'] == 1
        assert result['skipped'] == 1
        assert result['total'] == 2

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    def test_skips_activity_without_id(self, mock_get_raw_conn, mock_get_client):
        from services.garmin_service import sync_activities
        mock_get_raw_conn.return_value = MagicMock()
        mock_client = MagicMock()
        mock_act = {
            'activityType': {'typeKey': 'running'},
            'startTimeLocal': '2024-06-01 07:00:00',
            'duration': 1800, 'distance': 5000,
        }
        mock_client.get_activities_by_date.return_value = [mock_act]
        mock_get_client.return_value = mock_client

        result = sync_activities(user_id=1, days=30)
        assert result['inserted'] == 0
        assert result['skipped'] == 0
        assert result['total'] == 1

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    def test_too_many_requests(self, mock_get_raw_conn, mock_get_client):
        from services.garmin_service import (
            sync_activities,
            GarminConnectTooManyRequestsError,
        )
        mock_get_raw_conn.return_value = MagicMock()
        mock_client = MagicMock()
        mock_client.get_activities_by_date.side_effect = (
            GarminConnectTooManyRequestsError("rate limit")
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match='请求过于频繁'):
            sync_activities(user_id=1, days=30)

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    def test_connection_error(self, mock_get_raw_conn, mock_get_client):
        from services.garmin_service import (
            sync_activities,
            GarminConnectConnectionError,
        )
        mock_get_raw_conn.return_value = MagicMock()
        mock_client = MagicMock()
        mock_client.get_activities_by_date.side_effect = (
            GarminConnectConnectionError("network error")
        )
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match='Garmin 连接超时'):
            sync_activities(user_id=1, days=30)

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    @patch('services.garmin_service.time.sleep')
    def test_connection_error_retries_then_succeeds(self, mock_sleep, mock_get_raw_conn, mock_get_client):
        """网络瞬态错误应重试 3 次后成功"""
        from services.garmin_service import (
            sync_activities,
            GarminConnectConnectionError,
        )
        mock_get_raw_conn.return_value = MagicMock()
        mock_client = MagicMock()
        mock_client.get_activities_by_date.side_effect = [
            GarminConnectConnectionError("timeout 1"),
            GarminConnectConnectionError("timeout 2"),
            [],
        ]
        mock_get_client.return_value = mock_client

        result = sync_activities(user_id=1, days=30)
        assert result == {'inserted': 0, 'skipped': 0, 'total': 0}
        assert mock_client.get_activities_by_date.call_count == 3
        assert mock_sleep.call_count == 2

    @patch('services.garmin_service._get_client')
    @patch('services.garmin_service.get_raw_conn')
    @patch('services.garmin_service.time.sleep')
    def test_connection_error_retries_exhausted(self, mock_sleep, mock_get_raw_conn, mock_get_client):
        """网络瞬态错误重试耗尽后抛出 RuntimeError"""
        from services.garmin_service import (
            sync_activities,
            GarminConnectConnectionError,
        )
        mock_get_raw_conn.return_value = MagicMock()
        mock_client = MagicMock()
        mock_client.get_activities_by_date.side_effect = GarminConnectConnectionError("always fail")
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match='Garmin 连接超时'):
            sync_activities(user_id=1, days=30)
        assert mock_client.get_activities_by_date.call_count == 3
        assert mock_sleep.call_count == 2

    @patch('services.garmin_service.os.path.isfile')
    @patch('services.garmin_service.sync_file_from_gcs')
    @patch('services.garmin_service.Garmin')
    def test_get_client_restores_token_from_gcs(self, mock_garmin_cls, mock_sync_gcs, mock_isfile):
        """本地 token 缺失时应尝试从 GCS 恢复"""
        from services.garmin_service import _get_client, TOKEN_DIR
        token_file = os.path.join(TOKEN_DIR, 'garmin_tokens.json')

        calls = []

        def isfile_side_effect(path):
            calls.append(path)
            # 调用顺序: _get_client 检查(1) -> _ensure_token_from_gcs 检查(2) -> 恢复后检查(3)
            return len(calls) >= 3 and path == token_file

        mock_isfile.side_effect = isfile_side_effect
        mock_sync_gcs.return_value = None
        mock_client = MagicMock()
        mock_garmin_cls.return_value = mock_client
        result = _get_client()
        assert result == mock_client
        mock_sync_gcs.assert_called_once_with('garmin_tokens/garmin_tokens.json', token_file)

    @patch('services.garmin_service.os.path.isfile')
    @patch('services.garmin_service.sync_file_from_gcs')
    @patch('services.garmin_service.Garmin')
    def test_get_client_raises_when_gcs_restore_fails(self, mock_garmin_cls, mock_sync_gcs, mock_isfile):
        """GCS 恢复失败且本地无 token 时应抛出 RuntimeError"""
        from services.garmin_service import _get_client
        mock_isfile.return_value = False
        mock_sync_gcs.return_value = None
        with pytest.raises(RuntimeError, match='未找到 Garmin token'):
            _get_client()
        mock_garmin_cls.assert_not_called()


# ============================================================
# health_service.py — 补全: except Exception (lines 174-176)
# ============================================================

class TestHealthServiceExceptBranch:
    """generate_health_analysis 的 except 分支"""

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.call_ai')
    def test_exception_returns_error(self, mock_ai):
        from services.health_service import generate_health_analysis
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = Exception("DB crash")
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = generate_health_analysis(mock_db, user_id=1)
        assert result['success'] is False
        assert 'error' in result

    @patch('services.health_service.AI_AVAILABLE', True)
    @patch('services.health_service.call_ai')
    def test_exception_in_call_ai(self, mock_ai):
        from services.health_service import generate_health_analysis
        mock_ai.side_effect = Exception("API timeout")
        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [[], [], [], [], [], [], [], []]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = generate_health_analysis(mock_db, user_id=1)
        assert result['success'] is False


# ============================================================
# timeline_service.py — 补全: days=None, avg_glucose, net_cal
# ============================================================

class TestTimelineServiceDaysNone:
    """build_timeline days=None 分支"""

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_days_none_uses_all_records(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00',
                 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 7.0, 'type': '餐后2小时', 'timestamp': '2024-06-02 13:30:00',
                 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, records = build_timeline(mock_c, user_id=1, days=None)
        assert len(sorted_dates) == 2
        assert len(records) == 2

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_avg_glucose_rounded_correctly(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00',
                 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 7.0, 'type': '餐后2小时', 'timestamp': '2024-06-01 13:30:00',
                 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        stats = sorted_dates[0]['data']['stats']
        assert stats['glucose_count'] == 2
        assert stats['avg_glucose'] == 6.8  # (6.5+7.0)/2 = 6.75 → round → 6.8

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_net_calories_deficit(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00',
                 'is_predicted': 0, 'is_verified': 0},
                {'id': 2, 'value': 0, 'type': '午餐', 'timestamp': '2024-06-01 12:00:00',
                 'calories': 500, 'is_predicted': 0, 'is_verified': 0},
                {'id': 3, 'value': 0, 'type': '跑步', 'timestamp': '2024-06-01 07:00:00',
                 'calories': 300, 'distance': 5.0, 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        stats = sorted_dates[0]['data']['stats']
        # cal_in=500, cal_out_bmr=1600, cal_out_exercise=300, net=500-1900=-1400
        assert stats['is_deficit'] is True
        assert stats['net_calories'] == -1400

    @patch('services.timeline_service.settings.calculate_bmr')
    @patch('services.timeline_service.settings.load_config')
    def test_glucose_zero_count_no_division(self, mock_config, mock_bmr):
        from services.timeline_service import build_timeline
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [
            [
                {'id': 1, 'value': 0, 'type': '午餐', 'timestamp': '2024-06-01 12:00:00',
                 'calories': 500, 'is_predicted': 0, 'is_verified': 0},
            ],
            [],
        ]
        sorted_dates, _ = build_timeline(mock_c, user_id=1, days=7)
        stats = sorted_dates[0]['data']['stats']
        assert stats['glucose_count'] == 0
        assert stats['avg_glucose'] == 0


# ============================================================
# prediction_service.py — 补全剩余的 43 条遗漏分支
# ============================================================

class TestPredictionLinkEdgeCases:
    """link_prediction_to_real_record: 遗漏分支"""

    def _make_cursor(self, prediction_row=None):
        mock_c = MagicMock()
        mock_c.fetchone.return_value = prediction_row
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        return mock_db, mock_c

    def test_postmeal_1h_type_condition(self):
        """Cover line 49: '餐后1小时' type condition"""
        from services.prediction_service import link_prediction_to_real_record
        mock_db, mock_c = self._make_cursor(prediction_row=(50, 7.0))
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '餐后1小时', 8.0, '2024-01-01 12:00:00')
        assert result is not None

    def test_invalid_glucose_prints_warning(self):
        """Cover lines 25-26: invalid glucose skip + print"""
        from services.prediction_service import link_prediction_to_real_record
        mock_db = MagicMock()
        result = link_prediction_to_real_record(mock_db, 1, 1, '2024-01-01', '空腹', -5.0)
        assert result is None


class TestPredictionMorningFpgBranches:
    """predict_morning_fpg: 遗漏分支"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    @patch('services.prediction_service.settings.load_config')
    @patch('services.prediction_service.settings.calculate_bmr')
    @patch('services.prediction_service.settings.get_ai_system_prompt')
    def test_fpg_inserts_record(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        """Cover lines 157-164: DELETE existing + INSERT new prediction"""
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_prompt.return_value = ''
        mock_ai.return_value = '{"predicted_value": 6.2, "reasoning": "test"}'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None  # no existing prediction
        # fetchall sequence: yesterday_glucose, yesterday_calories, recent_fpg, prediction_history, medications
        mock_c.fetchall.side_effect = [
            [(6.5, '空腹', '2024-06-01 07:15:00'), (7.0, '餐后2小时', '2024-06-01 13:30:00')],  # yesterday_glucose
            [('午餐', 500, '2024-06-01 12:00:00', 45, 70)],  # yesterday_calories
            [(6.0, '2024-06-01 07:15:00'), (6.5, '2024-06-02 07:15:00')],  # recent_fpg
            [(5.8, 6.2, 0.4, '2024-06-01 07:20:00')],  # prediction_history
            [('二甲双胍', '500mg', '1', '片', 2, '餐前')],  # medications
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
    def test_fpg_with_default_meals(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        """Cover branch with default_meals having breakfast enabled"""
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {
            'default_meals': {
                'breakfast': {'enabled': True, 'calories': 400, 'carbs_grams': 50, 'gi_value': 65}
            }
        }
        mock_prompt.return_value = ''
        mock_ai.return_value = '{"predicted_value": 6.0, "reasoning": "test"}'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [
            [(7.0, '餐后2小时', '2024-06-01 13:30:00')],  # yesterday_glucose — no fasting data
            [],  # yesterday_calories — no calories at all → default_meals kicks in
            [],  # recent_fpg
            [],  # prediction_history
            [],  # medications
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
    def test_fpg_with_prediction_feedback(self, mock_prompt, mock_bmr, mock_config, mock_ai):
        """Cover lines 367-369: prediction_history used for feedback"""
        from services.prediction_service import predict_morning_fpg
        mock_bmr.return_value = 1600
        mock_config.return_value = {'default_meals': {}}
        mock_prompt.return_value = ''
        mock_ai.return_value = '{"predicted_value": 5.8, "reasoning": "test"}'

        mock_c = MagicMock()
        mock_c.fetchone.return_value = None
        mock_c.fetchall.side_effect = [
            [],  # yesterday_glucose
            [],  # yesterday_calories
            [],  # recent_fpg
            [(5.0, 5.5, 0.5, '2024-06-01 07:20:00'), (6.0, 5.8, -0.2, '2024-06-02 07:20:00')],  # predictions
            [],  # medications
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        predict_morning_fpg(mock_db, user_id=1)
        mock_db.commit.assert_called_once()


class TestPredictionPostExerciseBranches:
    """predict_post_exercise_glucose: 更完整的边界"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_no_json_match_returns_none(self, mock_ai):
        """Cover lines 210-213: JSON match failure"""
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = 'not json'
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [None, (6.0,), (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00')]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_invalid_pred_value(self, mock_ai):
        from services.prediction_service import predict_post_exercise_glucose
        mock_ai.return_value = '{"predicted_value": 88.0, "reasoning": "invalid"}'
        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [None, (6.0,), (5.0, '00:30:00', 145, 350, '2024-06-01 07:00:00')]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_post_exercise_glucose(mock_db, user_id=1, target_date='2024-06-01')
        assert result is None


class TestPredictionBackfillBranches:
    """backfill_post_exercise_predictions: 遗漏分支"""

    @patch('services.prediction_service.predict_post_exercise_glucose')
    def test_backfill_all_success(self, mock_predict):
        from services.prediction_service import backfill_post_exercise_predictions
        mock_predict.return_value = 5.5
        mock_db = MagicMock()
        result = backfill_post_exercise_predictions(mock_db, user_id=1, days=3)
        assert result['success'] == 3
        assert result['error'] == 0


class TestPredictionRemainingSlotsBranches:
    """predict_remaining_glucose_slots: 遗漏分支"""

    @patch('services.prediction_service.AI_AVAILABLE', True)
    @patch('services.prediction_service.call_ai')
    def test_exception_safety(self, mock_ai):
        """Cover try/except at lines 248-257"""
        from services.prediction_service import predict_remaining_glucose_slots
        mock_ai.side_effect = Exception("unexpected")
        mock_c = MagicMock()
        mock_c.fetchall.side_effect = [[(6.5, '空腹', '2024-06-01 07:15:00')], [('空腹',)]]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = predict_remaining_glucose_slots(mock_db, user_id=1)
        assert result == []


class TestCheckDailyDataCompleteBranches:
    """check_daily_data_complete: 遗漏分支"""

    @patch('services.prediction_service.datetime')
    def test_uses_target_date(self, mock_dt):
        from services.prediction_service import check_daily_data_complete
        mock_c = MagicMock()
        mock_c.fetchone.return_value = (0,)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c
        result = check_daily_data_complete(mock_db, user_id=1, target_date='2024-07-01')
        assert result['complete'] is False

    @patch('services.prediction_service.datetime')
    def test_exception_returns_false_dict(self, mock_dt):
        from services.prediction_service import check_daily_data_complete
        mock_db = MagicMock()
        mock_db.cursor.side_effect = Exception("error")
        result = check_daily_data_complete(mock_db, user_id=1)
        assert result['complete'] is False
        assert result['has_glucose'] is False


# ============================================================
# dashboard_service.py — 补全剩余遗漏分支
# ============================================================

class TestDashboardStatsBranches:
    """get_dashboard_stats: 遗漏分支"""

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_bp_max_min_dates(self, mock_settings, mock_um):
        """Cover bp_max_date / bp_min_date queries"""
        from services.dashboard_service import get_dashboard_stats
        from tests.helpers import mock_dashboard_service_settings as _setup
        _setup(mock_settings, mock_um)

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (100,),                          # total_records
            (6.0, 7.5, 9.0, 4.5),           # glucose_stats (all truthy → triggers detail queries)
            ('2024-06-01 08:00:00', '空腹'),  # max_detail
            ('2024-06-01 07:15:00', '空腹'),  # min_detail
            (10.0, 350, 145, 3),             # exercise_stats
            None,                            # vo2max
            (120.0, 80.0, 5, 135, 85, 110, 75),  # bp_stats
            ('2024-06-01 08:00:00',),        # bp_max_date
            ('2024-06-01 22:00:00',),        # bp_min_date
            None,                            # latest_weight
            (None,),                         # avg_weight
            None,                            # health_analyses
        ]
        mock_c.fetchall.side_effect = [
            [],  # today_weight
            [(6.5, '空腹'), (7.0, '餐后2小时')],  # compliance
            [], [], [], [], [], [],            # rest empty
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['bp_max_sys'] == 135
        assert result['bp_min_sys'] == 110

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_weight_change_calculated(self, mock_settings, mock_um):
        """Cover weight_change_default calculation"""
        from services.dashboard_service import get_dashboard_stats
        from tests.helpers import mock_dashboard_service_settings as _setup
        _setup(mock_settings, mock_um)
        mock_settings.calculate_bmi.return_value = 22.5

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (100,),                           # total_records
            (6.0, 7.5, None, None),           # glucose_stats
            (None,)*4,                        # exercise_stats
            None,                             # vo2max
            (None,)*7,                        # bp_stats
            (70.0, 22.0, '2024-06-15 07:00:00'),  # latest_weight
            (None,),                          # avg_weight
            (68.0,),                          # old_weight
            None,                             # health_analyses
        ]
        mock_c.fetchall.side_effect = [
            [(70.0, 22.0, '2024-06-15 07:00:00'), (69.0, 21.5, '2024-06-15 18:00:00')],  # today_weights
            [(7.0, '空腹')],  # compliance
            [], [], [], [], [], [],  # rest
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['weight_change_default'] == 2.0  # 70.0 - 68.0

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_bmi_calculation_with_height(self, mock_settings, mock_um):
        """Cover bmi calculation from weight + height config"""
        from services.dashboard_service import get_dashboard_stats
        from tests.helpers import mock_dashboard_service_settings as _setup
        _setup(mock_settings, mock_um)
        mock_settings.calculate_bmi.return_value = 23.5
        mock_um_instance = mock_um.return_value
        mock_um_instance.get_user_config.return_value = {'name': '测试', 'height': 170, 'target_weight': None}

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (100,),                           # total_records
            (6.0, 7.5, None, None),           # glucose_stats
            (None,)*4,                        # exercise_stats
            None,                             # vo2max
            (None,)*7,                        # bp_stats
            (70.0, None, '2024-06-15 07:00:00'),  # latest_weight (bmi is None → calculate)
            (None,),                          # avg_weight
            (68.0,),                          # old_weight
            None,                             # health_analyses
        ]
        mock_c.fetchall.side_effect = [[] for _ in range(8)]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_bmi'] == 23.5
        mock_settings.calculate_bmi.assert_called_with(70.0, 170)

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_score_average_70_range(self, mock_settings, mock_um):
        """Cover '一般' score label (score 70-79)"""
        from services.dashboard_service import get_dashboard_stats
        from tests.helpers import mock_dashboard_service_settings as _setup, make_minimal_cursor as _make
        _setup(mock_settings, mock_um)

        health_mock = MagicMock()
        health_mock.keys.return_value = ['id', 'health_score', 'recommendations', 'days', 'created_at']
        health_mock.__getitem__ = lambda s, k: {'id': 1, 'health_score': 75, 'recommendations': None, 'days': 7, 'created_at': '2024-06-01'}.get(k, None)

        mock_c = _make(health_analyses=health_mock)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['score_label'] == '一般'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_score_improve_60_range(self, mock_settings, mock_um):
        """Cover '需改善' score label (score 60-69)"""
        from services.dashboard_service import get_dashboard_stats
        from tests.helpers import mock_dashboard_service_settings as _setup, make_minimal_cursor as _make
        _setup(mock_settings, mock_um)

        health_mock = MagicMock()
        health_mock.keys.return_value = ['id', 'health_score', 'recommendations', 'days', 'created_at']
        health_mock.__getitem__ = lambda s, k: {'id': 1, 'health_score': 65, 'recommendations': None, 'days': 7, 'created_at': '2024-06-01'}.get(k, None)

        mock_c = _make(health_analyses=health_mock)
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        assert result['latest_analysis']['score_label'] == '需改善'

    @patch('services.dashboard_service.UserManager')
    @patch('services.dashboard_service.settings')
    def test_today_overview_predicted_slot(self, mock_settings, mock_um):
        """Cover today_overview with predicted (not measured) records"""
        from services.dashboard_service import get_dashboard_stats
        from tests.helpers import mock_dashboard_service_settings as _setup
        _setup(mock_settings, mock_um)

        mock_c = MagicMock()
        mock_c.fetchone.side_effect = [
            (100,),                           # total_records
            (6.0, 7.5, None, None),           # glucose_stats
            (None,)*4,                        # exercise_stats
            None,                             # vo2max
            (None,)*7,                        # bp_stats
            None,                             # latest_weight
            (None,),                          # avg_weight
            None,                             # health_analyses
        ]
        # today_records with is_predicted=1
        mock_c.fetchall.side_effect = [
            [],
            [(7.0, '空腹')],                   # compliance
            [{'value': 6.2, 'type': '睡前', 'timestamp': '2024-06-01 22:00:00', 'is_predicted': 1}],
            [], [], [], [], [], [],
        ]
        mock_db = MagicMock()
        mock_db.cursor.return_value = mock_c

        result = get_dashboard_stats(mock_db, user_id=1)
        bedtime = [s for s in result['today_overview'] if s['key'] == 'bedtime'][0]
        assert bedtime['is_predicted'] is True
        assert bedtime['status'] == 'predicted'
