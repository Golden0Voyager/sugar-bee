"""共享核心层单测：domain / timefmt / analytics。

同时守护与 settings.py 的一致性——MCP 侧不 import settings，靠这里断言
两边阈值不会漂移。
"""
from __future__ import annotations

import datetime
import os
import re
from unittest.mock import patch

import pytest

from mcp_adapter.core import analytics, domain, timefmt


class TestDomainParity:
    """domain 常量必须与 settings.py 对齐。"""

    def test_tir_range_matches_settings(self):
        from settings import GLUCOSE_TARGETS
        default = GLUCOSE_TARGETS["default"]
        assert default["min"] == domain.TIR_LOW
        assert default["max"] == domain.TIR_HIGH

    def test_types_are_non_empty_and_unique(self):
        for group in (
            domain.GLUCOSE_TYPES,
            domain.GLUCOSE_ANALYSIS_TYPES,
            domain.GLUCOSE_LATEST_TYPES,
            domain.GLUCOSE_SUMMARY_TYPES,
            domain.EXERCISE_TYPES,
        ):
            assert group
            assert len(set(group)) == len(group)

    def test_analysis_types_subset_of_glucose_types(self):
        assert set(domain.GLUCOSE_ANALYSIS_TYPES) <= set(domain.GLUCOSE_TYPES)


class TestDomainJudgement:
    def test_in_target_range(self):
        assert domain.in_target_range(domain.TIR_LOW) is True
        assert domain.in_target_range(domain.TIR_HIGH) is True
        assert domain.in_target_range(domain.TIR_LOW - 0.1) is False
        assert domain.in_target_range(domain.TIR_HIGH + 0.1) is False

    @pytest.mark.parametrize(
        ("systolic", "diastolic", "expected"),
        [
            (150, 80, "偏高"),
            (120, 95, "偏高"),
            (140, 90, "偏高"),
            (135, 80, "警戒"),
            (120, 88, "警戒"),
            (130, 85, "警戒"),
            (120, 80, "正常"),
        ],
    )
    def test_classify_bp(self, systolic, diastolic, expected):
        assert domain.classify_bp(systolic, diastolic) == expected

    def test_is_high_bp(self):
        assert domain.is_high_bp(140, 80) is True
        assert domain.is_high_bp(120, 90) is True
        assert domain.is_high_bp(139, 89) is False


class TestTimeMode:
    def test_default_is_local(self):
        with patch.dict(os.environ, {}, clear=True):
            assert timefmt.ts_mode() == timefmt.LOCAL

    def test_utc_switch(self):
        with patch.dict(os.environ, {"SB_MCP_TS_MODE": "UTC"}):
            assert timefmt.ts_mode() == timefmt.UTC

    def test_unknown_value_falls_back_to_local(self):
        with patch.dict(os.environ, {"SB_MCP_TS_MODE": "whatever"}):
            assert timefmt.ts_mode() == timefmt.LOCAL


class TestTimeFormat:
    def test_now_str_shape(self):
        assert len(timefmt.now_str()) == 19

    def test_today_str_shape(self):
        assert len(timefmt.today_str()) == 10

    def test_cutoff_is_earlier_than_now(self):
        assert timefmt.cutoff(1) < timefmt.now_str()

    def test_cutoff_utc_mode(self):
        with patch.dict(os.environ, {"SB_MCP_TS_MODE": "utc"}):
            assert timefmt.cutoff(1) < timefmt.now_str()

    def test_cutoff_minimum_one_day(self):
        assert timefmt.cutoff(0) == timefmt.cutoff(1)

    def test_day_bounds_explicit_day(self):
        start, end = timefmt.day_bounds(datetime.date(2026, 5, 2))
        assert start == "2026-05-02 00:00:00"
        assert end == "2026-05-03 00:00:00"

    def test_day_bounds_defaults_to_today(self):
        start, end = timefmt.day_bounds()
        assert start.startswith(timefmt.today_str())
        assert end > start

    def test_today_bounds(self):
        start, end = timefmt.today_bounds()
        assert start < end

    @pytest.mark.parametrize(
        "value",
        [
            "2026-05-02 06:30:00",
            "2026-05-02T06:30:00",
            "2026-05-02 06:30",
            "2026-05-02",
            datetime.datetime(2026, 5, 2, 6, 30, 0),
        ],
    )
    def test_parse_accepts_common_shapes(self, value):
        parsed = timefmt.parse(value)
        assert parsed is not None
        assert parsed.year == 2026

    def test_parse_none(self):
        assert timefmt.parse(None) is None

    def test_parse_invalid(self):
        assert timefmt.parse("not-a-timestamp") is None

    def test_display_local_does_not_shift(self):
        assert timefmt.display("2026-05-02 06:30:00") == "2026-05-02 06:30"

    def test_display_utc_mode_shifts(self):
        with patch.dict(os.environ, {"SB_MCP_TS_MODE": "utc"}):
            assert timefmt.display("2026-05-02 06:30:00") == "2026-05-02 14:30"

    def test_display_none_and_empty(self):
        assert timefmt.display(None) is None
        assert timefmt.display("") is None

    def test_display_unparseable_falls_back_to_prefix(self):
        assert timefmt.display("2026/05/02 06:30:00") == "2026/05/02 06:30"

    def test_shift_minutes(self):
        assert timefmt.shift_minutes("2026-05-02 06:30:00", -3) == "2026-05-02 06:27:00"
        assert timefmt.shift_minutes("2026-05-02 06:30:00", 3) == "2026-05-02 06:33:00"

    def test_shift_minutes_unparseable_returns_input(self):
        assert timefmt.shift_minutes("bad", 3) == "bad"


class TestNormalizeInput:
    def test_none_returns_now(self):
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", timefmt.normalize_input(None))

    def test_full_timestamp_preserved(self):
        ts = "2026-05-02 06:30:00"
        assert timefmt.normalize_input(ts) == ts

    def test_missing_year_gets_current_year(self):
        result = timefmt.normalize_input("-05-02 06:30")
        assert result == f"{timefmt.now().year}-05-02 06:30"

    def test_garbage_falls_back_to_now(self):
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", timefmt.normalize_input("明天早上"))


class TestDurationMinutes:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("41min", 41),
            ("1h20m", 80),
            ("45分钟", 45),
            ("45", 45),
            ("1.5h", 90),
            ("2小时", 120),
        ],
    )
    def test_parses(self, text, expected):
        assert analytics.duration_minutes(text) == expected

    @pytest.mark.parametrize("text", [None, "", "0", "0min", "abc"])
    def test_returns_none(self, text):
        assert analytics.duration_minutes(text) is None


class TestStats:
    def test_empty(self):
        assert analytics.stats([]) == {"samples": 0}

    def test_basic(self):
        assert analytics.stats([1, 2, 3]) == {
            "samples": 3,
            "mean": 2.0,
            "min": 1.0,
            "max": 3.0,
        }

    def test_skips_none(self):
        assert analytics.stats([1, None, 3])["samples"] == 2


class TestTir:
    def test_empty(self):
        assert analytics.tir([]) == {"samples": 0}

    def test_counts(self):
        result = analytics.tir([3.0, 5.0, 10.0, 11.0])
        assert result["samples"] == 4
        assert result["tir_percent"] == 50.0
        assert result["below_count"] == 1
        assert result["above_count"] == 1

    def test_boundaries_count_as_in_range(self):
        result = analytics.tir([domain.TIR_LOW, domain.TIR_HIGH])
        assert result["tir_percent"] == 100.0


class TestTrend:
    def test_linear_slope_flat(self):
        assert analytics.linear_slope([5, 5, 5]) == 0.0

    def test_linear_slope_up(self):
        assert analytics.linear_slope([1, 2, 3]) == 1.0

    def test_linear_slope_short(self):
        assert analytics.linear_slope([1]) == 0.0

    def test_half_change(self):
        first, second, delta = analytics.half_change([4, 4, 6, 6])
        assert (first, second, delta) == (4.0, 6.0, 2.0)

    def test_half_change_short(self):
        assert analytics.half_change([4]) == (0.0, 0.0, 0.0)

    @pytest.mark.parametrize(
        ("delta", "expected"),
        [(0.5, "上升"), (-0.5, "下降"), (0.1, "基本平稳")],
    )
    def test_direction(self, delta, expected):
        assert analytics.direction(delta) == expected


class TestBuckets:
    def test_bucket_by_value(self):
        buckets = analytics.bucket_by_value(
            [("a", 3.5), ("b", 2.0), ("c", 0.5)], high=3.0, low=1.0
        )
        assert buckets == {"high": [3.5], "mid": [2.0], "low": [0.5]}

    def test_summarize_buckets(self):
        result = analytics.summarize_buckets({"high": [1, 2], "low": []})
        assert result["high"]["samples"] == 2
        assert result["low"] == {"samples": 0}
