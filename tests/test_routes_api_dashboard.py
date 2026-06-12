"""routes/api_dashboard.py 全覆盖测试 — api_health_stats + api_day_overview 完整 fetchone/fetchall 追踪"""
import pytest
from unittest.mock import patch, MagicMock

from tests.helpers import mock_health_settings, mock_day_settings


# ============================================================
# api_health_stats — 完整 fetchone/fetchall 序列追踪
# ============================================================

class TestHealthStatsMinimal:
    """最小数据路径 — 所有条件分支跳过 (None/空)"""

    def test_minimal_all_none(self, client_authenticated):
        """所有 fetchone 返回 None → 无额外查询，gs[2]=gs[3]=None, bs[3]=bs[5]=None, lw=None, vo2row=None"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            # fetchone: gs(4), es(4), bs(7), lw, aw, vo2row
            mock_c.fetchone.side_effect = [
                (None,)*4,     # gs (gs[2]=gs[3]=None → skip)
                (None,)*4,     # es
                (None,)*7,     # bs (bs[3]=bs[5]=None → skip)
                None,          # lw (None → skip ow)
                (None,),       # aw
                None,          # vo2row (None → skip pv)
            ]
            mock_c.fetchall.return_value = []  # compliance empty
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200

    def test_days_all_no_cutoff(self, client_authenticated):
        """days=all → cutoff = '2000-01-01 00:00:00' → 查询全部历史"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7, None, (None,), None
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=all')
            assert result.status_code == 200

    def test_days_null_no_cutoff(self, client_authenticated):
        """days=null → 同 all, cutoff='2000-01-01'"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7, None, (None,), None
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=null')
            assert result.status_code == 200


class TestHealthStatsGlucoseDetails:
    """gs[2]/gs[3] truthy → 触发 max/min glucose detail 查询"""

    def test_max_glucose_detail(self, client_authenticated):
        """gs[2]=10.0 → 查询 max_detail"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            # fetchone: gs(4), max_detail, es(4), bs(7), lw, aw, vo2row
            mock_c.fetchone.side_effect = [
                (6.0, 8.0, 10.0, 3.5),   # gs (gs[2]=10.0 truthy, gs[3]=3.5 truthy)
                ('2024-06-01 20:00:00', '晚餐后2小时'),  # max_detail
                ('2024-06-01 07:15:00', '空腹'),        # min_detail
                (None,)*4,      # es
                (None,)*7,      # bs
                None,           # lw
                (None,),        # aw
                None,           # vo2row
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=30')
            assert result.status_code == 200
            data = result.json
            assert data['glucose']['max'] == 10.0
            assert data['glucose']['max_detail']['type'] == '晚餐后2小时'

    def test_min_glucose_detail(self, client_authenticated):
        """gs[3]=3.5 → 查询 min_detail"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (6.0, 8.0, 10.0, 3.5),
                ('2024-06-01 20:00:00', '晚餐后2小时'),
                ('2024-06-01 07:00:00', '空腹'),
                (None,)*4, (None,)*7, None, (None,), None,
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=30')
            assert result.status_code == 200
            data = result.json
            assert data['glucose']['min_detail']['type'] == '空腹'

    def test_detail_row_none_safe(self, client_authenticated):
        """详情查询返回 None → 使用默认空值"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (6.0, 8.0, 10.0, 3.5),
                None,   # max_detail row = None → 保持默认
                None,   # min_detail row = None
                (None,)*4, (None,)*7, None, (None,), None,
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=30')
            assert result.status_code == 200


class TestHealthStatsBPDetails:
    """bs[3]/bs[5] truthy → 触发 bp_max_date/bp_min_date 查询"""

    def test_bp_max_date(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            # fetchone: gs, es, bs(7), bp_max, bp_min, lw, aw, vo2row
            mock_c.fetchone.side_effect = [
                (None,)*4,      # gs
                (None,)*4,      # es
                (120, 80, 5, 135, 85, 110, 75),  # bs (bs[3]=135, bs[5]=110)
                ('2024-06-05 08:00:00',),  # bp_max_date
                ('2024-06-03 20:00:00',),  # bp_min_date
                None, (None,), None,
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200
            data = result.json
            assert data['bp']['max_date'] == '2024-06-05'

    def test_bp_detail_row_none(self, client_authenticated):
        """bp detail 查询返回 None → date 保持 '-'"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4,
                (None,)*4,
                (120, 80, 5, 135, 85, 110, 75),
                None,  # bp_max_detail = None
                None,  # bp_min_detail = None
                None, (None,), None,
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200


class TestHealthStatsWeight:
    """体重段：lw truthy → old_weight 查询，weight_change 计算"""

    def test_weight_change(self, client_authenticated):
        """lw 存在 → 查询 ow → 计算 change"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            # fetchone: gs, es, bs, lw, aw, ow, vo2row
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7,
                (70.0, 22.5, '2024-06-05 07:00:00'),  # lw
                (69.5,),   # aw
                (68.0,),   # ow (old weight, consumed AFTER aw)
                None,      # vo2row
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=7')
            assert result.status_code == 200
            data = result.json
            assert data['weight']['change'] == 2.0

    def test_weight_no_change_no_ow(self, client_authenticated):
        """lw 存在但 ow=None → weight_change=None"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7,
                (70.0, 22.5, '2024-06-05 07:00:00'),
                (69.5,),   # aw
                None,      # ow=None → weight_change stays None
                None,      # vo2row
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=7')
            assert result.status_code == 200
            data = result.json
            assert data['weight']['change'] is None

    def test_weight_change_all_days(self, client_authenticated):
        """days=all → 查询最早体重计算变化"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7,
                (70.0, 22.5, '2024-06-05 07:00:00'),
                (69.5,),   # aw
                (65.0,),   # ow (ASC LIMIT 1 → earliest weight)
                None,      # vo2row
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=all')  # else→all history
            assert result.status_code == 200
            data = result.json
            assert data['weight']['change'] == 5.0  # 70 - 65


class TestHealthStatsVO2Max:
    """VO2max 段：vo2row truthy → prev_vo2max 查询"""

    def test_vo2max_with_prev(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            # fetchone: gs, es, bs, lw, aw, vo2row, pv
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7, None, (None,),
                (42.5, '2024-06-05 07:00:00'),  # vo2row
                (40.0,),  # prev_vo2max
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200
            data = result.json
            assert data['exercise']['latest_vo2max'] == 42.5
            assert data['exercise']['prev_vo2max'] == 40.0

    def test_vo2max_none(self, client_authenticated):
        """vo2row=None → latest_vo2max=None, prev_vo2max=None"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7, None, (None,), None
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200
            data = result.json
            assert data['exercise']['latest_vo2max'] is None


class TestHealthStatsCompliance:
    """达标率计算：fetchall 合规数据"""

    def test_compliance_calculated(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7, None, (None,), None
            ]
            # compliance fetchall: dict-like rows for r['value'], r['type']
            mock_c.fetchall.return_value = [
                {'value': 6.0, 'type': '空腹'},
                {'value': 7.5, 'type': '餐后2小时'},
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=7')
            assert result.status_code == 200
            data = result.json
            assert data['glucose']['compliance'] == 100  # all compliant

    def test_compliance_no_data(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchone.side_effect = [
                (None,)*4, (None,)*4, (None,)*7, None, (None,), None
            ]
            mock_c.fetchall.return_value = []
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats')
            assert result.status_code == 200
            data = result.json
            assert data['glucose']['compliance'] == 0


class TestHealthStatsFullPath:
    """完整数据路径：所有条件分支 truthy"""

    def test_full_data_all_paths(self, client_authenticated):
        """gs[2][3], bs[3][5], lw, vo2row 全部 truthy → 覆盖所有条件分支"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_health_settings(mock_settings)
            mock_c = MagicMock()
            # fetchone: gs, max_detail, min_detail, es, bs, bp_max, bp_min, lw, aw, ow, vo2row, pv
            mock_c.fetchone.side_effect = [
                (6.0, 8.0, 9.0, 4.5),              # gs (both truthy)
                ('2024-06-05 20:00:00', '晚餐后2小时'),  # max_detail
                ('2024-06-03 07:00:00', '空腹'),        # min_detail
                (5.0, 300, None, 2),                # es
                (120, 80, 5, 135, 85, 110, 75),     # bs (bs[3], bs[5] truthy)
                ('2024-06-05 08:00:00',),           # bp_max
                ('2024-06-03 20:00:00',),           # bp_min
                (70.0, 22.5, '2024-06-05 07:00:00'), # lw
                (69.5,),                            # aw
                (68.0,),                            # ow
                (42.5, '2024-06-05 07:00:00'),      # vo2row
                (40.0,),                            # prev_vo2max
            ]
            mock_c.fetchall.return_value = [
                {'value': 6.0, 'type': '空腹'}, {'value': 7.0, 'type': '餐后2小时'}
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/health_stats?days=7')
            assert result.status_code == 200
            data = result.json
            assert data['glucose']['max_detail']['timestamp'] != ''
            assert data['glucose']['min_detail']['timestamp'] != ''
            assert data['bp']['max_date'] != '-'
            assert data['bp']['min_date'] != '-'
            assert data['weight']['change'] == 2.0
            assert data['exercise']['prev_vo2max'] == 40.0


# ============================================================
# api_day_overview — 血糖匹配、运动、血压、用药全覆盖
# ============================================================

class TestDayOverviewGlucoseMatching:
    """血糖槽位匹配逻辑"""

    def test_fasting_matched(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            overview = result.json['overview']
            fasting = [s for s in overview if s['key'] == 'fasting'][0]
            assert fasting['value'] == 6.5
            assert fasting['status'] == 'measured'

    def test_post_breakfast_by_type(self, client_authenticated):
        """'早餐后2小时' 类型精确匹配"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 8.2, 'type': '早餐后2小时', 'timestamp': '2024-06-01 10:55:00', 'is_predicted': 0}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            pb = [s for s in result.json['overview'] if s['key'] == 'post_breakfast'][0]
            assert pb['value'] == 8.2

    def test_generic_post_matched_by_hour(self, client_authenticated):
        """通用'餐后' → hour=14 → 匹配 post_lunch slot (13-17)"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 7.5, 'type': '餐后', 'timestamp': '2024-06-01 14:30:00', 'is_predicted': 0}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            pl = [s for s in result.json['overview'] if s['key'] == 'post_lunch'][0]
            assert pl['value'] == 7.5

    def test_generic_pre_matched_by_hour(self, client_authenticated):
        """通用'餐前' → hour=17 → 匹配 pre_dinner (16-19)"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 5.8, 'type': '餐前', 'timestamp': '2024-06-01 17:30:00', 'is_predicted': 0}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            pd_slot = [s for s in result.json['overview'] if s['key'] == 'pre_dinner'][0]
            assert pd_slot['value'] == 5.8

    def test_predicted_preferred_when_no_measured(self, client_authenticated):
        """只有预测值无实测 → status='predicted'"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 6.2, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 1}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            fasting = [s for s in result.json['overview'] if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'predicted'

    def test_cgm_matches(self, client_authenticated):
        """CGM 记录距槽位 ≤ 30 分钟 → 优先使用 CGM"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [{'value': 5.8, 'type': 'CGM', 'timestamp': '2024-06-01 07:20:00', 'is_predicted': 0}],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            fasting = [s for s in result.json['overview'] if s['key'] == 'fasting'][0]
            assert fasting['cgm'] is True
            assert fasting['value'] == 5.8

    def test_pending_slot(self, client_authenticated):
        """无匹配记录 → status='pending'"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            fasting = [s for s in result.json['overview'] if s['key'] == 'fasting'][0]
            assert fasting['status'] == 'pending'
            assert fasting['value'] is None


class TestDayOverviewExercises:
    """运动记录"""

    def test_exercises_added(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [],
                [{'type': '跑步', 'distance': 5.0, 'calories': 300, 'duration': 30,
                  'heart_rate': 145, 'pace': None, 'max_pace': None, 'cadence': None,
                  'vo2max': 42.0, 'max_heart_rate': 160, 'steps': None,
                  'timestamp': '2024-06-01 17:00:00'}],
                [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert len(result.json['exercises']) == 1
            assert result.json['exercises'][0]['type'] == '跑步'


class TestDayOverviewBP:
    """血压记录"""

    def test_bp_added(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [],  # records, exercises empty
                [{'systolic_pressure': 120, 'diastolic_pressure': 80,
                  'pulse_rate': 72, 'spo2': 98, 'timestamp': '2024-06-01 08:00:00'}],
                [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert len(result.json['bps']) == 1
            assert result.json['bps'][0]['systolic'] == 120


class TestDayOverviewWeight:
    """体重记录"""

    def test_weight_added(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [],  # records, exercises, bp
                [{'weight': 70.0, 'bmi': 22.5, 'timestamp': '2024-06-01 07:00:00'}],
                [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert len(result.json['weights']) == 1
            assert result.json['weights'][0]['weight'] == 70.0


class TestDayOverviewMeds:
    """用药方案 + 服药日志"""

    def test_med_plan_daily(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [],  # empty records/exercises/bp/weight
                [{'id': 1, 'medication_name': '二甲双胍', 'dosage': '500mg',
                  'dose_quantity': '1', 'dose_unit': '片', 'times_per_day': 2,
                  'timing_notes': '餐前', 'frequency': 'daily', 'frequency_detail': '',
                  'start_date': None, 'category': 'long_term', 'med_type': 'oral'}],
                [{'plan_id': 1, 'count': 2}],
                []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            meds = result.json['med_status']['plans']
            assert len(meds) == 1
            assert meds[0]['name'] == '二甲双胍'

    def test_temp_medications(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [], [],
                [{'plan_id': 1, 'count': 1}],
                [{'medication_name': '布洛芬', 'notes': '头疼', 'timestamp': '2024-06-01 14:00:00'}]
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert len(result.json['med_status']['temp_medications']) == 1

    def test_default_date(self, client_authenticated):
        """不传 date 参数 → 使用今天"""
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [], [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview')
            assert result.status_code == 200


class TestDayOverviewCompliance:
    """日达标率计算"""

    def test_day_compliance(self, client_authenticated):
        with patch('routes.api_dashboard.get_db') as mock_get_db, \
             patch('routes.api_dashboard.settings') as mock_settings:
            mock_day_settings(mock_settings)
            mock_c = MagicMock()
            mock_c.fetchall.side_effect = [
                [
                    {'value': 6.5, 'type': '空腹', 'timestamp': '2024-06-01 07:15:00', 'is_predicted': 0},
                    {'value': 8.0, 'type': '早餐后2小时', 'timestamp': '2024-06-01 11:00:00', 'is_predicted': 0},
                ],
                [], [], [], [], [{'plan_id': 1, 'count': 1}], []
            ]
            mock_db = MagicMock()
            mock_db.cursor.return_value = mock_c
            mock_get_db.return_value = mock_db

            result = client_authenticated.get('/api/day_overview?date=2024-06-01')
            assert result.status_code == 200
            assert 'compliance' in result.json
"""
api_records.py + api_dashboard.py 深层未覆盖分支测试

api_records.py 目标 (12 行):
  L184-185:  form-data user_id int() 解析异常 -> except pass
  L191-192:  form-data weight float() 解析异常 (BMI 计算) -> except pass
  L200-201:  form-data weight float() 解析异常 (用户资料更新) -> except pass
  L284-285:  link_prediction_to_real_record 异常 -> except pass
  L310-312:  parse_ai 带图片 -> for img_b64 in images_b64 循环体
  L589:     preview_import .xlsx 文件 -> pd.read_excel

api_dashboard.py 目标 (10 行):
  L217-219: api_health_stats 外层 except -> 500
  L279:     day_overview post_exercise slot '运动后' -> matched
  L287:     day_overview post_dinner slot '晚餐后' -> matched
  L306:     day_overview CGM 记录不含时间 -> continue
  L404-405: day_overview every_n_days freq_detail 解析异常 -> except pass
  L419-420: day_overview monthly freq_detail 解析异常 -> except pass
"""
import io
import base64



# ============================================================
# api_records.py — form-data 分支
# ============================================================

class TestRecordsFormDataBranches:
    """api_records.py form-data 分支（L184-185, L191-192, L200-201）

    注：form-data 的 value 字段是字符串，_validate_record_data 中
    value > 0 比较会触发 TypeError（str vs int）。此外部异常不影响
    我们测试的内部 except 覆盖：L184-185 / L191-192 / L200-201 在
    外层 except 之前已执行完毕。
    """

    def test_add_form_user_id_parse_error(self, client_authenticated):
        """L184-185: form-data POST, user_id='abc' -> int('abc') ValueError -> except pass"""
        resp = client_authenticated.post('/add', data={
            'value': '7.5',
            'type': '空腹',
            'unit': 'mmol/L',
            'user_id': 'abc',  # non-numeric -> int() raises ValueError
            'timestamp': '2024-06-01 07:15:00',
        })
        # 外层 except 捕获 _validate_record_data 的 str vs int 比较异常 -> 500
        # 但 L184-185 已执行 (int('abc') 失败后 except pass)
        assert resp.status_code == 500

    def test_add_form_weight_parse_error(self, client_authenticated):
        """L191-192, L200-201: weight='abc' -> float('abc') 在两个 except 中触发"""
        resp = client_authenticated.post('/add', data={
            'value': '7.5',
            'type': '体重记录',
            'user_id': '1',
            'weight': 'abc',      # non-numeric -> BMI calc + profile update raise
            'timestamp': '2024-06-01 07:15:00',
        })
        assert resp.status_code == 500


# ============================================================
# api_records.py — link_prediction 异常
# ============================================================

class TestRecordsLinkPrediction:
    """api_records.py L284-285: link_prediction_to_real_record 异常"""

    def test_add_link_prediction_exception(self, isolate_db, client_authenticated):
        """L284-285: link_prediction 抛出 ValueError -> 被 except 捕获"""
        with patch('routes.api_records.link_prediction_to_real_record',
                   side_effect=ValueError("simulated error")):
            resp = client_authenticated.post('/add', json={
                'value': 6.5,
                'type': '空腹',
                'timestamp': '2024-06-01 07:15:00',
                'user_id': 1,
            })
        assert resp.status_code == 200, f"预期 200, 得到 {resp.status_code}: {resp.data}"


# ============================================================
# api_records.py — parse_ai 带图片
# ============================================================

class TestRecordsParseAIImages:
    """api_records.py L310-312: parse_ai 带图片"""

    @patch('routes.api_records.parse_glucose_input', return_value=[])
    def test_parse_ai_with_images(self, mock_parse, client_authenticated):
        """L310-312: 发送 base64 图片 -> for 循环体执行"""
        small_b64 = base64.b64encode(b'fake-image-data').decode()
        resp = client_authenticated.post('/parse_ai', json={
            'text': '空腹 6.5',
            'images': [f'data:image/jpeg;base64,{small_b64}'],
            'mime_type': 'image/jpeg',
        })
        assert resp.status_code == 200, f"预期 200, 得到 {resp.status_code}: {resp.data}"
        mock_parse.assert_called_once()


# ============================================================
# api_records.py — preview_import xlsx
# ============================================================

class TestRecordsPreviewImport:
    """api_records.py L589: preview_import .xlsx 文件"""

    def test_preview_import_xlsx(self, client_authenticated):
        """L589: 上传 .xlsx 文件 -> pd.read_excel 被调用"""
        with patch('routes.api_records.pd.read_excel') as mock_read_excel:
            mock_read_excel.return_value = __import__('pandas').DataFrame(
                {'col1': ['a'], 'col2': ['b']}
            )
            data = {'file': (io.BytesIO(b'dummy-excel-content'), 'test.xlsx')}
            resp = client_authenticated.post(
                '/preview_import',
                data=data,
                content_type='multipart/form-data',
            )
        assert resp.status_code == 200, f"预期 200, 得到 {resp.status_code}: {resp.data}"
        mock_read_excel.assert_called_once()
        _, kwargs = mock_read_excel.call_args
        assert kwargs.get('nrows') == 10, f"应传 nrows=10, 得到 {kwargs}"


# ============================================================
# api_dashboard.py — health_stats exception
# ============================================================

class TestDashboardHealthStatsExcept:
    """api_dashboard.py L217-219: api_health_stats 异常处理"""

    def test_health_stats_exception(self, client_authenticated):
        """L217-219: days=badparam -> int('badparam') ValueError -> 500"""
        resp = client_authenticated.get('/api/health_stats?days=badparam')
        assert resp.status_code == 500, f"预期 500, 得到 {resp.status_code}: {resp.data}"
        data = resp.get_json()
        assert data is not None
        assert 'error' in data


# ============================================================
# api_dashboard.py — day_overview slot matching
# ============================================================

class TestDashboardDayOverviewSlotMatching:
    """api_dashboard.py L279, L287, L306: day_overview slot 匹配

    使用 isolate_db 确保各测试独立数据库，避免数据冲突。
    """

    @pytest.mark.usefixtures("isolate_db")
    def test_day_overview_post_exercise(self, client_authenticated, app):
        """L279: type='运动后' 于 08:45 -> post_exercise slot matched"""
        with app.app_context():
            resp = client_authenticated.post('/add', json={
                'value': 6.0, 'type': '运动后',
                'timestamp': '2024-06-01 08:45:00', 'user_id': 1,
            })
            assert resp.status_code == 200

        overview_resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
        assert overview_resp.status_code == 200
        slots = {s['key']: s for s in overview_resp.json['overview']}
        assert slots['post_exercise']['value'] == 6.0, \
            f"应匹配运动后, 得到 {slots['post_exercise']}"
        assert slots['post_exercise']['status'] == 'measured'

    @pytest.mark.usefixtures("isolate_db")
    def test_day_overview_post_dinner(self, client_authenticated, app):
        """L287: type='晚餐后' 于 20:00 -> post_dinner slot matched"""
        with app.app_context():
            resp = client_authenticated.post('/add', json={
                'value': 7.5, 'type': '晚餐后2小时',
                'timestamp': '2024-06-01 20:00:00', 'user_id': 1,
            })
            assert resp.status_code == 200

        overview_resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
        assert overview_resp.status_code == 200
        slots = {s['key']: s for s in overview_resp.json['overview']}
        assert slots['post_dinner']['value'] == 7.5, \
            f"应匹配晚餐后, 得到 {slots['post_dinner']}"
        assert slots['post_dinner']['status'] == 'measured'

    @pytest.mark.usefixtures("isolate_db")
    def test_day_overview_cgm_bad_timestamp(self, client_authenticated, app):
        """L306: CGM 记录 timestamp='2024-06-01 080000' (时间无冒号) -> continue"""
        with app.app_context():
            from utils.db import get_db
            db = get_db()
            c = db.cursor()
            # timestamp 在 BETWEEN 范围内，但时间部分无冒号
            # -> rt_time='08000', ':' not in rt_time -> continue (L306)
            c.execute("""INSERT INTO records
                (user_id, value, type, timestamp, is_predicted)
                VALUES (?, ?, ?, ?, ?)""",
                (1, 5.8, 'CGM', '2024-06-01 080000', 0))
            db.commit()

        overview_resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
        assert overview_resp.status_code == 200
        slots = {s['key']: s for s in overview_resp.json['overview']}
        # 所有槽位应为 pending（CGM 因时间无冒号被 continue 跳过）
        pending = {k: v for k, v in slots.items() if v['status'] == 'pending'}
        assert len(pending) == 7, \
            f"所有槽位应为 pending, 得到 {[(k, v['status']) for k, v in slots.items()]}"


# ============================================================
# api_dashboard.py — medication frequency branches
# ============================================================

class TestDashboardMedicationFreqBranches:
    """api_dashboard.py L404-405, L419-420: medication frequency 异常处理"""

    @pytest.mark.usefixtures("isolate_db")
    def test_day_overview_med_every_n_days_exception(self, client_authenticated, app):
        """L404-405: every_n_days + 无效 freq_detail -> except include=True"""
        with app.app_context():
            resp = client_authenticated.post('/add_medication_plan', json={
                'medication_name': '测试药品A',
                'dosage': '500mg',
                'times_per_day': 1,
                'timing_notes': '早餐后',
                'start_date': '2024-01-01',
                'frequency': 'every_n_days',
                'frequency_detail': 'abc',  # non-numeric -> int('abc') ValueError
            })
            assert resp.status_code == 200, f"创建用药方案失败: {resp.data}"

        overview_resp = client_authenticated.get('/api/day_overview?date=2024-06-01')
        assert overview_resp.status_code == 200
        plans = overview_resp.json['med_status']['plans']
        # except include=True → 被包含在列表中
        assert any(p['name'] == '测试药品A' for p in plans), \
            f"预期找到测试药品A, 得到 {[p['name'] for p in plans]}"

    @pytest.mark.usefixtures("isolate_db")
    def test_day_overview_med_monthly_exception(self, client_authenticated, app):
        """L419-420: monthly + 无效 freq_detail -> except include = day_of_month == 1"""
        with app.app_context():
            resp = client_authenticated.post('/add_medication_plan', json={
                'medication_name': '测试药品B',
                'dosage': '10mg',
                'times_per_day': 1,
                'timing_notes': '早餐后',
                'start_date': '2024-01-01',
                'frequency': 'monthly',
                'frequency_detail': 'not-a-number-list',  # 解析失败
            })
            assert resp.status_code == 200, f"创建用药方案失败: {resp.data}"

        # 解析失败 -> int('not-a-number-list') fails
        # -> except (ValueError, AttributeError): include = day_of_month == 1
        # 6月15日 != 1 -> include = False
        overview_resp = client_authenticated.get('/api/day_overview?date=2024-06-15')
        assert overview_resp.status_code == 200
        plans = overview_resp.json['med_status']['plans']
        assert not any(p['name'] == '测试药品B' for p in plans), \
            f"不应包含测试药品B (日期非 1 号), 得到 {[p['name'] for p in plans]}"

        # 6月1日 == 1 -> include = True
        overview_resp2 = client_authenticated.get('/api/day_overview?date=2024-06-01')
        assert overview_resp2.status_code == 200
        plans2 = overview_resp2.json['med_status']['plans']
        assert any(p['name'] == '测试药品B' for p in plans2), \
            f"6月1日应包含测试药品B, 得到 {[p['name'] for p in plans2]}"
