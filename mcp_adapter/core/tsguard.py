"""时间戳护栏 —— 写入前的最后一道闸门。

为什么需要它：调用写入工具的 Agent（老愚）没有可靠时钟。模型会凭上下文推测"现在几点"，
结果可能把时间写到未来，或者把昨天的记录写成今天。库里一旦进了这种时间，
趋势图和统计都会跟着错，而且很难事后发现。

规则（可用环境变量调整）：

- ``SB_TS_FUTURE_TOLERANCE_MIN``（默认 15 分钟）：晚于服务器当前时间超过该值 → 直接纠正为服务器时间
- ``SB_TS_DRIFT_WARN_HOURS``（默认 3 小时）：与服务器时间相差超过该值 → 保留原值（可能是补录历史），
  但在回复里显式告警
- 其余情况：保留原值，附一行服务器当前时间，给模型一个新鲜的时间锚点

``guard_timestamp`` 返回 ``(最终时间戳, 需要追加到回复的提示行)``，
调用方负责把提示行拼进返回给模型的文本里——这是让模型"看见"当前时间的关键。
"""

from __future__ import annotations

import os

from mcp_adapter.core.timefmt import parse as _parse_ts
from utils.timezone import now as _app_now

FUTURE_TOLERANCE_MIN = float(os.environ.get("SB_TS_FUTURE_TOLERANCE_MIN", "15"))
SOFT_DRIFT_HOURS = float(os.environ.get("SB_TS_DRIFT_WARN_HOURS", "3"))


def server_clock() -> tuple[str, str]:
    """服务器当前本地墙钟时间：``(YYYY-MM-DD HH:MM:SS, 周X)``。"""
    dt = _app_now()
    return dt.strftime("%Y-%m-%d %H:%M:%S"), f"周{'一二三四五六日'[dt.weekday()]}"


def guard_timestamp(ts: str | None) -> tuple[str, list[str]]:
    """校验要写入的时间戳，返回 (最终时间戳, 提示行)。"""
    now_s, weekday = server_clock()
    parsed = _parse_ts(ts)
    now_dt = _parse_ts(now_s)
    if parsed is None or now_dt is None:
        return now_s, [f"   🕐 服务器当前时间 {now_s}（{weekday}）"]

    delta_min = (parsed - now_dt).total_seconds() / 60.0
    if delta_min > FUTURE_TOLERANCE_MIN:
        return now_s, [
            f"   ⚠️ 提交的时间 {ts} 晚于服务器当前时间 {now_s}，已按服务器时间写入",
        ]

    hours = abs(delta_min) / 60.0
    if hours >= SOFT_DRIFT_HOURS:
        return ts, [
            f"   ⚠️ 写入时间 {ts} 与服务器当前时间 {now_s}（{weekday}）"
            f"相差 {hours:.1f} 小时；若不是补录历史数据，请用服务器时间重新记录",
        ]
    return ts, [f"   🕐 服务器当前时间 {now_s}（{weekday}）"]
