"""体重记录与用户个人档案体重同步测试。"""

from datetime import timedelta

import pytest

import settings
from routes.api_records import _sync_profile_weight_from_latest
from services.timeline_service import build_timeline
from user_manager import UserManager
from utils.timezone import timestamp_str as app_timestamp_str
from utils.timezone import today as app_today


def _uid(client):
    with client.session_transaction() as sess:
        return sess['current_user_id']


def _profile_weight(user_id):
    return UserManager().get_user_config(user_id)['weight']


def _add_weight(client, weight, timestamp):
    resp = client.post('/add', json={
        'value': 0, 'type': '体重记录', 'weight': weight, 'timestamp': timestamp,
    })
    assert resp.status_code == 200
    return resp.get_json()['data']['id']


def test_single_add_syncs_profile_to_latest_weight(client_authenticated):
    uid = _uid(client_authenticated)
    _add_weight(client_authenticated, 55.1, '2026-10-29 06:26:00')
    assert _profile_weight(uid) == pytest.approx(55.1)


def test_batch_add_syncs_profile_to_latest_weight(client_authenticated):
    uid = _uid(client_authenticated)
    resp = client_authenticated.post('/batch_add', json={'records': [{
        'value': 0, 'type': '体重记录', 'weight': 55.1,
        'datetime': '2026-10-29 06:26:00',
    }]})
    assert resp.status_code == 200
    assert _profile_weight(uid) == pytest.approx(55.1)


def test_backfilled_older_weight_does_not_overwrite_newer_profile(client_authenticated):
    uid = _uid(client_authenticated)
    _add_weight(client_authenticated, 55.1, '2026-10-29 06:26:00')
    _add_weight(client_authenticated, 70.45, '2026-10-27 06:51:00')
    assert _profile_weight(uid) == pytest.approx(55.1)


def test_batch_backfilled_older_weight_does_not_overwrite_newer_profile(client_authenticated):
    uid = _uid(client_authenticated)
    _add_weight(client_authenticated, 55.1, '2026-10-29 06:26:00')
    resp = client_authenticated.post('/batch_add', json={'records': [{
        'value': 0, 'type': '体重记录', 'weight': 70.45,
        'datetime': '2026-10-27 06:51:00',
    }]})
    assert resp.status_code == 200
    assert _profile_weight(uid) == pytest.approx(55.1)


def test_deleting_latest_weight_reverts_profile_to_previous_latest(client_authenticated):
    uid = _uid(client_authenticated)
    first_id = _add_weight(client_authenticated, 60.0, '2026-10-27 06:51:00')
    latest_id = _add_weight(client_authenticated, 55.1, '2026-10-29 06:26:00')
    assert first_id != latest_id
    assert _profile_weight(uid) == pytest.approx(55.1)

    resp = client_authenticated.post(f'/delete/{latest_id}')
    assert resp.status_code == 302
    assert _profile_weight(uid) == pytest.approx(60.0)


def test_updating_weight_record_resyncs_profile(client_authenticated):
    uid = _uid(client_authenticated)
    record_id = _add_weight(client_authenticated, 60.0, '2026-10-27 06:51:00')
    assert _profile_weight(uid) == pytest.approx(60.0)

    resp = client_authenticated.post(f'/update/{record_id}', json={
        'weight': 50.2, 'timestamp': '2026-10-29 06:26:00',
    })
    assert resp.status_code == 200
    assert _profile_weight(uid) == pytest.approx(50.2)


def test_batch_add_syncs_each_record_owner(client_authenticated):
    current_uid = _uid(client_authenticated)
    other_uid = UserManager().create_user(
        '_sync_other', '同步测试',
        {'name': '同步测试', 'birth_year': 1990, 'height': 160, 'weight': 60, 'gender': 'female'},
    )

    resp = client_authenticated.post('/batch_add', json={'records': [
        {'value': 0, 'type': '体重记录', 'weight': 55.1,
         'datetime': '2026-10-29 06:26:00'},
        {'value': 0, 'type': '体重记录', 'weight': 48.2,
         'datetime': '2026-10-29 06:31:00', 'user_id': other_uid},
    ]})
    assert resp.status_code == 200
    assert _profile_weight(current_uid) == pytest.approx(55.1)
    assert _profile_weight(other_uid) == pytest.approx(48.2)


def test_profile_weight_sync_leaves_manual_weight_without_records(app, client_authenticated):
    uid = _uid(client_authenticated)
    with app.app_context():
        from utils.db import get_db
        _sync_profile_weight_from_latest(get_db(), uid)
    assert _profile_weight(uid) == pytest.approx(70)


def test_profile_weight_sync_handles_invalid_user_and_db_error(app, client_authenticated):
    class BrokenDb:
        def cursor(self):
            raise RuntimeError('database unavailable')

    uid = _uid(client_authenticated)
    with app.app_context():
        _sync_profile_weight_from_latest(BrokenDb(), uid)
        _sync_profile_weight_from_latest(BrokenDb(), None)
    # 档案同步失败不能影响用户现有手动设置。
    assert _profile_weight(uid) == pytest.approx(70)


def test_energy_stats_use_each_users_latest_weight(app, client_authenticated):
    """每日能量统计必须按记录所属用户的最新体重算 BMR，不能串用户。"""
    current_uid = _uid(client_authenticated)
    other_uid = UserManager().create_user(
        '_energy_other', '能量测试',
        {'name': '能量测试', 'birth_year': 1990, 'height': 160, 'weight': 60, 'gender': 'female'},
    )

    measured_at = app_timestamp_str()
    energy_date = (app_today() - timedelta(days=1)).strftime('%Y-%m-%d')
    energy_time = f'{energy_date} 12:00:00'
    resp = client_authenticated.post('/batch_add', json={'records': [
        {'value': 0, 'type': '体重记录', 'weight': 55.1, 'datetime': measured_at},
        {'value': 0, 'type': '午餐', 'calories': 500, 'datetime': energy_time},
        {'value': 0, 'type': '体重记录', 'weight': 48.2, 'datetime': measured_at,
         'user_id': other_uid},
        {'value': 0, 'type': '午餐', 'calories': 500, 'datetime': energy_time,
         'user_id': other_uid},
    ]})
    assert resp.status_code == 200

    year = app_today().year
    expected_current_bmr = int(10 * 55.1 + 6.25 * 175 - 5 * (year - 1980) + 5)
    expected_other_bmr = int(10 * 48.2 + 6.25 * 160 - 5 * (year - 1990) - 161)
    assert _profile_weight(current_uid) == pytest.approx(55.1)
    assert _profile_weight(other_uid) == pytest.approx(48.2)
    assert settings.calculate_bmr(current_uid) == expected_current_bmr
    assert settings.calculate_bmr(other_uid) == expected_other_bmr
    assert expected_current_bmr != expected_other_bmr

    with app.app_context():
        from utils.db import get_db
        current_days, _ = build_timeline(get_db().cursor(), current_uid, days=7)
        other_days, _ = build_timeline(get_db().cursor(), other_uid, days=7)

    current_stats = next(item['data']['stats'] for item in current_days if item['date'] == energy_date)
    other_stats = next(item['data']['stats'] for item in other_days if item['date'] == energy_date)
    assert current_stats['cal_in'] == 500
    assert other_stats['cal_in'] == 500
    assert current_stats['cal_out_bmr'] == expected_current_bmr
    assert other_stats['cal_out_bmr'] == expected_other_bmr
    assert current_stats['net_calories'] == 500 - expected_current_bmr
    assert other_stats['net_calories'] == 500 - expected_other_bmr
