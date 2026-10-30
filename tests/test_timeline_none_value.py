"""build_timeline 对 value 为 NULL 的记录必须容错。

背景（真实隐患）：趋势计算的循环里直接做 `r['value'] > 0`，而 value 为 NULL
的记录（只记了热量的餐食、体成分等）r['value'] 是 None——只要同 key（ fasting /
post / other）前面有一条 value > 0 的记录，`None > 0` 就是 TypeError，
整个时间线接口 500。
"""
import os
import sqlite3

from services.timeline_service import build_timeline
from utils.timezone import now as app_now

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _insert(db_path, user_id, r_type, value, calories, ts_offset_minutes):
    from datetime import timedelta

    conn = sqlite3.connect(db_path)
    ts = (app_now() - timedelta(minutes=ts_offset_minutes)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        'INSERT INTO records (user_id, type, timestamp, value, calories) VALUES (?, ?, ?, ?, ?)',
        (user_id, r_type, ts, value, calories),
    )
    conn.commit()
    conn.close()
    return ts


def test_timeline_tolerates_null_value_after_valued_record(isolate_db, db_info):
    """同 key 已有前值时，后面一条 value 为 NULL 的记录不崩、趋势照常。"""
    db_path = db_info['path']
    user_id = 201
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO app_users (id, username, display_name, is_active) VALUES (?, ?, ?, 1)',
        (user_id, 'null_val', 'NULL值测试'),
    )
    conn.execute('INSERT INTO user_profiles (user_id, name) VALUES (?, ?)', (user_id, 'nv'))
    conn.commit()
    conn.close()

    # 先有带值记录（key=other），再有 value 为 NULL 的餐食记录
    _insert(db_path, user_id, '血氧', 98.0, 0, 1)
    _insert(db_path, user_id, '晚餐', None, 500, 0)

    from utils.db import get_raw_conn

    db = get_raw_conn()
    try:
        days, _ = build_timeline(db.cursor(), user_id)
    finally:
        db.close()

    day = days[0]
    assert day['data']['stats']['cal_in'] == 500
    # 带值记录的趋势正常算，NULL 值记录静默跳过
    entries = day['data']['entries']
    valued = next(e for e in entries if e['type'] == '血氧')
    assert valued['trend'] == 0 and valued['trend_dir'] == 'flat'
