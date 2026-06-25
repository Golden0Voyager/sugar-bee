"""Application timezone helpers.

Records are stored as naive local wall-clock timestamps. These helpers make the
business timezone explicit so Cloud Run / database UTC defaults cannot shift a
user's "today" by eight hours.
"""
from __future__ import annotations

import datetime
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Shanghai"


def app_timezone() -> ZoneInfo:
    tz_name = app_timezone_name()
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def app_timezone_name() -> str:
    return os.environ.get("SUGAR_BEE_TIMEZONE") or os.environ.get("TZ") or DEFAULT_TIMEZONE


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def to_app_time(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(app_timezone()).replace(tzinfo=None)


def now() -> datetime.datetime:
    return to_app_time(utc_now())


def today() -> datetime.date:
    return now().date()


def today_str() -> str:
    return today().isoformat()


def timestamp_str() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")
