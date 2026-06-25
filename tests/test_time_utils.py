import datetime


def test_utc_evening_maps_to_shanghai_next_morning(monkeypatch):
    monkeypatch.setenv("SUGAR_BEE_TIMEZONE", "Asia/Shanghai")
    from utils.timezone import to_app_time

    utc_dt = datetime.datetime(2026, 6, 23, 22, 57, tzinfo=datetime.UTC)

    assert to_app_time(utc_dt) == datetime.datetime(2026, 6, 24, 6, 57)


def test_today_str_uses_app_timezone(monkeypatch):
    monkeypatch.setenv("SUGAR_BEE_TIMEZONE", "Asia/Shanghai")
    from utils import timezone

    fixed_utc = datetime.datetime(2026, 6, 23, 22, 57, tzinfo=datetime.UTC)
    monkeypatch.setattr(timezone, "utc_now", lambda: fixed_utc)

    assert timezone.today_str() == "2026-06-24"
