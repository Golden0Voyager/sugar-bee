"""MCP 层纯计算函数：无数据库、无 IO、无全局状态。

放这里的理由：统计口径（TIR、斜率、分层对比）是最容易在多个 server
之间写歪的部分。抽成纯函数后可以独立跑单测，也便于两个 server 复用。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from mcp_adapter.core.domain import TIR_HIGH, TIR_LOW


def duration_minutes(text) -> float | None:
    """把 ``41min`` / ``1h20m`` / ``45分钟`` / ``45`` 这类时长解析成分钟。"""
    if text is None or text == "":
        return None
    t = str(text).strip().lower()
    m = re.match(
        r"^(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hour|小时|个小时))?"
        r"(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|分钟)?)?$",
        t,
    )
    if not m:
        try:
            return float(t)
        except ValueError:
            return None
    hours = float(m.group(1) or 0)
    minutes = float(m.group(2) or 0)
    if hours == 0 and minutes == 0:
        return None
    return hours * 60 + minutes


def stats(values: Sequence[float]) -> dict:
    """基础统计：样本数、均值、最小、最大。空序列返回 ``{"samples": 0}``。"""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"samples": 0}
    return {
        "samples": len(vals),
        "mean": round(sum(vals) / len(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
    }


def tir(values: Sequence[float]) -> dict:
    """目标范围内时间占比（Time In Range）。

    区间取 ``domain.TIR_LOW`` ~ ``domain.TIR_HIGH``（对齐指南通用标准
    3.9-10.0 mmol/L）。同时给出高于/低于区间的次数。
    """
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"samples": 0}
    n = len(vals)
    in_range = sum(1 for v in vals if TIR_LOW <= v <= TIR_HIGH)
    return {
        "samples": n,
        "mean": round(sum(vals) / n, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "tir_percent": round(in_range * 100.0 / n, 1),
        "above_count": sum(1 for v in vals if v > TIR_HIGH),
        "below_count": sum(1 for v in vals if v < TIR_LOW),
    }


def linear_slope(ys: Sequence[float]) -> float:
    """等间隔序列的最小二乘斜率（x 取 0..n-1）。少于 2 个点返回 0.0。"""
    vals = [float(y) for y in ys]
    n = len(vals)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(vals) / n
    denom = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, vals, strict=True)) / denom


def half_change(ys: Sequence[float]) -> tuple[float, float, float]:
    """前后半段均值对比，返回 ``(前半均值, 后半均值, 差值)``。"""
    vals = [float(y) for y in ys]
    n = len(vals)
    if n < 2:
        return 0.0, 0.0, 0.0
    first = sum(vals[: n // 2]) / (n // 2)
    second = sum(vals[n // 2 :]) / (n - n // 2)
    return round(first, 2), round(second, 2), round(second - first, 2)


def direction(delta: float, tolerance: float = 0.3) -> str:
    """按差值方向给出「上升 / 下降 / 基本平稳」。"""
    if delta > tolerance:
        return "上升"
    if delta < -tolerance:
        return "下降"
    return "基本平稳"


def bucket_by_value(
    items: Iterable[tuple[str, float]],
    high: float,
    low: float,
) -> dict[str, list[float]]:
    """按数值把 ``(key, value)`` 分到 high / mid / low 三档。"""
    buckets: dict[str, list[float]] = {"high": [], "mid": [], "low": []}
    for _key, value in items:
        if value >= high:
            buckets["high"].append(value)
        elif value >= low:
            buckets["mid"].append(value)
        else:
            buckets["low"].append(value)
    return buckets


def summarize_buckets(buckets: dict[str, Sequence[float]]) -> dict:
    """对每档做基础统计，输出 ``{档位: stats}``。"""
    return {name: stats(values) for name, values in buckets.items()}
