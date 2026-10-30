"""体成分 BMR(Katch-McArdle)测试。

背景：用户提出——同样 75 kg，体脂率从一周前的较高值降到 14% 时，去脂体重变大，
静态消耗（基础代谢）理应变高，能量缺口也应跟着变大。而旧的 calculate_bmr 只用
Mifflin-St Jeor（体重/身高/年龄/性别），完全感知不到体成分变化。

现在的口径：
- 30 天内有体脂秤读数（records.body_fat_rate）→ Katch-McArdle：
  BMR = 370 + 21.6 × 去脂体重kg（去脂体重 = 体重 × (1 − 体脂率/100)）
- 读数缺失 / 超过 30 天 / 算出来离谱（不在 800–3000 内）→ 退回 Mifflin-St Jeor
- 读库出任何异常也退回 Mifflin（不能因为体脂秤数据把基础代谢算崩）
"""
import os
import sqlite3

import settings
from utils.timezone import now as app_now

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 测试用户的档案兜底值（user_profiles 缺字段时 load_config 的 DEFAULT_PROFILE）：
# male / 1964 年生（2026 年 62 岁）/ 170 cm / 75 kg
MIFFLIN_BMR = int(10 * 75 + 6.25 * 170 - 5 * (app_now().year - 1964) + 5)  # = 1507


def _make_user(db_path, user_id):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT OR REPLACE INTO app_users (id, username, display_name, is_active) VALUES (?, ?, ?, 1)',
        (user_id, f'user{user_id}', f'用户{user_id}'),
    )
    conn.execute(
        'INSERT OR REPLACE INTO user_profiles (user_id, name) VALUES (?, ?)',
        (user_id, f'u{user_id}'),
    )
    conn.commit()
    conn.close()


def _insert_body_comp(db_path, user_id, body_fat_rate, weight, timestamp):
    conn = sqlite3.connect(db_path)
    conn.execute(
        'INSERT INTO records (user_id, type, timestamp, body_fat_rate, weight) VALUES (?, ?, ?, ?, ?)',
        (user_id, 'body_comp', timestamp, body_fat_rate, weight),
    )
    conn.commit()
    conn.close()


def _ts_days_ago(days):
    from datetime import timedelta

    return (app_now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')


def test_bmr_uses_katch_mcardle_with_recent_body_fat(isolate_db, db_info):
    """30 天内的体脂读数：走 Katch-McArdle，且与 Mifflin 结果不同。"""
    _make_user(db_info['path'], 101)
    _insert_body_comp(db_info['path'], 101, 14.0, 75.0, _ts_days_ago(7))

    bmr = settings.calculate_bmr(101)

    expected = int(370 + 21.6 * 75 * 0.86)  # 1763
    assert bmr == expected
    assert bmr != MIFFLIN_BMR


def test_bmr_falls_back_when_body_fat_too_old(isolate_db, db_info):
    """超过 30 天的读数视为过旧，退回 Mifflin。"""
    _make_user(db_info['path'], 102)
    _insert_body_comp(db_info['path'], 102, 14.0, 75.0, _ts_days_ago(40))

    assert settings.calculate_bmr(102) == MIFFLIN_BMR


def test_bmr_falls_back_without_body_fat_record(isolate_db, db_info):
    """没有任何体脂记录时，就是原来的 Mifflin 结果。"""
    _make_user(db_info['path'], 103)

    assert settings.calculate_bmr(103) == MIFFLIN_BMR


def test_bmr_uses_profile_weight_when_record_weight_missing(isolate_db, db_info):
    """体脂记录没记体重时，用档案体重 75 kg 算去脂体重。"""
    _make_user(db_info['path'], 104)
    _insert_body_comp(db_info['path'], 104, 20.0, None, _ts_days_ago(3))

    bmr = settings.calculate_bmr(104)

    assert bmr == int(370 + 21.6 * 75 * 0.80)


def test_bmr_falls_back_when_db_read_raises(isolate_db, db_info, monkeypatch):
    """读库抛异常（连接断、表不存在等）不能炸，退回 Mifflin。"""
    _make_user(db_info['path'], 105)

    def _boom():
        raise RuntimeError('db is gone')

    monkeypatch.setattr('utils.db.get_raw_conn', _boom)
    assert settings.calculate_bmr(105) == MIFFLIN_BMR


def test_bmr_falls_back_when_katch_result_implausible(isolate_db, db_info):
    """体脂读数离谱导致 Katch 结果超出 800–3000 时，不采用，退回 Mifflin。"""
    _make_user(db_info['path'], 106)
    # 体重 500 kg → Katch 远超 3000
    _insert_body_comp(db_info['path'], 106, 10.0, 500.0, _ts_days_ago(2))

    assert settings.calculate_bmr(106) == MIFFLIN_BMR


def test_bmr_ignores_zero_body_fat_records(isolate_db, db_info):
    """体脂率 0 的记录（SQL 层排除）不参与计算，退回 Mifflin。"""
    _make_user(db_info['path'], 107)
    _insert_body_comp(db_info['path'], 107, 0.0, 75.0, _ts_days_ago(1))

    assert settings.calculate_bmr(107) == MIFFLIN_BMR


def test_bmr_prefers_latest_record(isolate_db, db_info):
    """同一天秤多次时，用最新一条。"""
    _make_user(db_info['path'], 108)
    _insert_body_comp(db_info['path'], 108, 30.0, 75.0, _ts_days_ago(10))
    _insert_body_comp(db_info['path'], 108, 14.0, 75.0, _ts_days_ago(1))

    bmr = settings.calculate_bmr(108)

    assert bmr == int(370 + 21.6 * 75 * 0.86)
