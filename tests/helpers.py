"""测试共享 mock 辅助函数 — 减少各测试文件中的重复代码"""

import datetime
from unittest.mock import MagicMock

# ── settings mock ──

def mock_health_settings(mock_settings, target_weight=None):
    """配置 api_dashboard.health_stats 的 mock settings 返回值"""
    mock_settings.load_config.return_value = {'target_weight': target_weight}
    mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
    mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
    mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
    mock_settings.GLUCOSE_TARGETS = {}
    mock_settings.BADGE_SYSTEM = {}


def mock_day_settings(mock_settings):
    """配置 api_dashboard.day_overview 的 mock settings 返回值"""
    mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
    mock_settings.get_badge_for_rate.return_value = {'key': 'good', 'icon': '👍'}
    mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}


def mock_dashboard_service_settings(mock_settings, mock_um):
    """配置 dashboard_service.get_dashboard_stats 的 mock settings 返回值"""
    mock_settings.get_bmi_category.return_value = {'label': '正常', 'color': '#4CAF50'}
    mock_settings.get_badge_for_rate.return_value = {'key': 'encourage', 'icon': '💪'}
    mock_settings.GLUCOSE_TARGETS = {}
    mock_settings.BADGE_SYSTEM = {}
    mock_settings.check_glucose_compliance.return_value = {'is_compliant': True, 'level': 'optimal'}
    mock_settings.calculate_bmi.return_value = 23.0
    mock_um_instance = MagicMock()
    mock_um_instance.get_user_config.return_value = {'name': '测试', 'height': 170, 'target_weight': None}
    mock_um.return_value = mock_um_instance


# ── mock DB 工厂 ──

def make_mock_db(cursor=None):
    """创建 mock DB 对象（带可选的 mock cursor）"""
    db = MagicMock()
    if cursor is not None:
        db.cursor.return_value = cursor
    return db


def make_mock_cursor(fetchone_side_effect=None, fetchall_side_effect=None, fetchone_return_value=None):
    """创建 mock cursor，支持 fetchone/fetchall 配置"""
    c = MagicMock()
    if fetchone_side_effect is not None:
        c.fetchone.side_effect = fetchone_side_effect
    if fetchone_return_value is not None:
        c.fetchone.return_value = fetchone_return_value
    if fetchall_side_effect is not None:
        c.fetchall.side_effect = fetchall_side_effect
    else:
        c.fetchall.return_value = []
    return c


# ── 用药方案 factory ──

MED_BASE = {
    'id': 1, 'medication_name': '二甲双胍', 'dosage': '500mg',
    'dose_quantity': '1', 'dose_unit': '片', 'times_per_day': 2,
    'timing_notes': '餐前', 'start_date': None, 'category': 'long_term', 'med_type': 'oral',
    'frequency': 'daily', 'frequency_detail': '', 'end_date': None,
}


def med(**kwargs):
    """创建用药方案 dict（合并 MED_BASE 与自定义参数）"""
    return dict(MED_BASE, **kwargs)


# ── 日期冻结 ──

def freeze_date(mock_dt, date_value):
    """冻结 datetime.datetime.now() 和 .today() 到指定日期"""
    mock_now = datetime.datetime.combine(date_value, datetime.time(10, 0, 0))
    mock_dt.datetime.now.return_value = mock_now
    mock_dt.datetime.today.return_value = mock_now
    mock_dt.datetime.combine = datetime.datetime.combine
    mock_dt.timedelta = datetime.timedelta
    mock_dt.datetime.timedelta = datetime.timedelta
    mock_dt.datetime.strptime = datetime.datetime.strptime
    mock_dt.date.today.return_value = date_value


# ── dashboard_service 最小 cursor ──

def make_minimal_cursor(
    all_meds=None,
    today_records=None,
    today_exercises=None,
    today_bps=None,
    today_weights=None,
    taken_logs=None,
    temp_meds=None,
    compliance_glucose=None,
    health_analyses=None,
):
    """构建最小 mock cursor。fetchall 按 get_dashboard_stats 调用顺序排列。"""
    mock_c = MagicMock()

    # fetchone 序列: total_records, glucose_stats(4), exercise_stats(4), vo2max,
    #                bp_stats(7), latest_weight, avg_weight, health_analyses
    mock_c.fetchone.side_effect = [
        (0,),                              # 1. total_records
        (None, None, None, None),          # 2. glucose_stats
        (None, None, None, None),          # 3. exercise_stats
        None,                              # 4. vo2max
        (None,)*7,                         # 5. bp_stats
        None,                              # 6. latest_weight
        (None,),                           # 7. avg_weight
        health_analyses,                   # 8. health_analyses
    ]

    # fetchall 实际调用顺序 (get_dashboard_stats):
    # 1.today_weights  2.compliance_glucose  3.today_records
    # 4.today_exercises  5.today_bps  6.all_meds  7.taken_logs  8.temp_meds
    mock_c.fetchall.side_effect = [
        today_weights or [],
        compliance_glucose or [],
        today_records or [],
        today_exercises or [],
        today_bps or [],
        all_meds or [],
        taken_logs or [],
        temp_meds or [],
    ]

    return mock_c


# ── dashboard_service stats fetchone/fetchall factories ──

def make_dashboard_stats_fetchone(has_bp=False, has_weight=False):
    """构建 fetchone side_effect 列表，匹配 get_dashboard_stats 中的实际调用顺序。"""
    base = [
        (100,),              # 1. total_records
        (6.0, 7.5, None, None),  # 2. glucose_stats
    ]
    # glucose_stats[2] is None → skip max detail query
    # glucose_stats[3] is None → skip min detail query
    base += [
        (None,)*4,           # 3. exercise_stats
        None,                # 4. vo2max_row
    ]
    if has_bp:
        base += [
            (120.0, 80.0, 5, 135, 85, 110, 75),  # 5. bp_stats
            ('2024-06-01 08:00:00',),  # 6. bp_max_date
            ('2024-06-01 22:00:00',),  # 7. bp_min_date
        ]
    else:
        base += [(None,)*7]  # 5. bp_stats (all None → no extra queries)
    if has_weight:
        base += [
            (70.0, 23.5, '2024-06-01 07:00:00'),  # latest_weight (3-tuple)
            (70.0,),                               # old_weight fetchone
        ]
    else:
        base += [None]       # latest_weight
    base += [
        (70.0,),             # avg_weight_7d
        None,                # health_analyses
    ]
    return base


def make_dashboard_stats_fetchall():
    """构建空数据的 fetchall 列表"""
    return [
        [],  # compliance glucose
        [],  # today_records
        [],  # today_weight
        [],  # today_exercises
        [],  # today_bps
        [],  # all_meds
        [],  # taken_logs
        [],  # temp_meds
    ]
