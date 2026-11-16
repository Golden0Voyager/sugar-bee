"""时间戳口径统一。

存储约定（与 utils/timezone.py 一致）：``records.timestamp`` 存的是
**本地墙钟时间**（naive local wall-clock），读出后不需要再 +8h。
仓库里所有写入路径（``app_timestamp_str()``）与 SQL 片段
（``now_sql()`` 用 ``datetime('now','localtime')``）都遵循这一约定。

历史遗留：读服务（原 ``scripts/sugar_bee_mcp.py``，现 ``mcp_adapter/servers/reader.py``）
曾假定库里存 UTC 并统一 +8h 展示，
与上述约定冲突（会让展示时间整体前移 8 小时）。若某个部署确实存 UTC，
用 ``SB_MCP_TS_MODE=utc`` 显式切换，不要靠猜。
"""
from __future__ import annotations

import datetime
import logging
import os
import re

from utils.timezone import app_timezone, now, timestamp_str, utc_now

logger = logging.getLogger(__name__)

LOCAL = "local"
UTC = "utc"


def ts_mode() -> str:
    """当前时间戳口径：``local``（默认）或 ``utc``。"""
    mode = os.environ.get("SB_MCP_TS_MODE", LOCAL).strip().lower()
    return UTC if mode == UTC else LOCAL


def now_str() -> str:
    """当前本地墙钟时间，格式 ``YYYY-MM-DD HH:MM:SS``。"""
    return timestamp_str()


def today_str() -> str:
    """今天的日期，格式 ``YYYY-MM-DD``。"""
    return now().date().isoformat()


def cutoff(days: int) -> str:
    """回溯窗口起点（含）。``days`` 小于 1 时按 1 天算。"""
    span = datetime.timedelta(days=max(int(days), 1))
    ref = utc_now().replace(tzinfo=None) if ts_mode() == UTC else now()
    return (ref - span).strftime("%Y-%m-%d %H:%M:%S")


def day_bounds(day: datetime.date | None = None) -> tuple[str, str]:
    """返回某一天的 ``[start, end)`` 边界字符串，用于替代 ``date(col)=date(?)``。

    SQL 侧只做 ``timestamp >= ? AND timestamp < ?`` 比较，SQLite / PostgreSQL
    都能走索引，且不受方言的日期函数差异影响。
    """
    d = day or now().date()
    start = datetime.datetime.combine(d, datetime.time.min)
    end = start + datetime.timedelta(days=1)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def today_bounds() -> tuple[str, str]:
    """今天（本地）的 ``[start, end)``。"""
    return day_bounds(now().date())


def parse(ts) -> datetime.datetime | None:
    """把数据库里的时间戳解析成 naive datetime；无法解析返回 None。"""
    if ts is None:
        return None
    if isinstance(ts, datetime.datetime):
        return ts.replace(tzinfo=None)
    text = str(ts).strip().replace("T", " ")
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def display(ts) -> str | None:
    """时间戳转展示用字符串 ``YYYY-MM-DD HH:MM``。

    ``local`` 模式下原样输出；``utc`` 模式下先按 UTC 解释再换算到业务时区。
    """
    if ts is None or ts == "":
        return None
    parsed = parse(ts)
    if parsed is None:
        return str(ts)[:16]
    if ts_mode() == UTC:
        parsed = (
            parsed.replace(tzinfo=datetime.UTC).astimezone(app_timezone()).replace(tzinfo=None)
        )
    return parsed.strftime("%Y-%m-%d %H:%M")


def shift_minutes(ts: str, minutes: int) -> str:
    """时间戳平移若干分钟，用于「N 分钟内重复」这类窗口比较。"""
    parsed = parse(ts)
    if parsed is None:
        return ts
    return (parsed + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def normalize_input(ts: str | None = None) -> str:
    """校验并补全用户/agent 传入的时间戳，确保格式为 ``YYYY-MM-DD HH:MM:SS``。

    支持：
    - 完整时间戳 ``YYYY-MM-DD HH:MM[:SS]``（补秒）
    - 裸时间 ``H:MM`` / ``HH:MM`` / ``H点MM`` / ``H时MM分``（补**今天**）
    - 相对日期 + 时间 ``今天/昨天/前天 6:35``
    - 缺年份的 ``-MM-DD HH:MM`` / ``MM-DD HH:MM``（补当年，保持原样不加秒）
    - 其他无法识别的格式（回退到当前时间，并打 WARNING）

    2026-11-16 修复：原来只认「完整时间戳」和「缺年份」两种，``6:35`` 这类
    **静默退化成「此刻」**。后果是 agent 按用户说的「6:35」记录，实际落库成
    收到消息的时间（实测偏 51 分钟），而工具照样返回「记录成功」，两边都看不出错。
    裸时间现在按今天补全；仍然认不出的才回退，并留一条 WARNING 便于排查。
    """
    if not ts:
        return now_str()
    s = str(ts).strip()
    # 1) 完整时间戳：保留原有语义（有秒留秒、没秒不加），只把 T 分隔统一成空格
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        day, hh, mm, ss = m.groups()
        out = f"{day} {int(hh):02d}:{mm}"
        return f"{out}:{ss}" if ss is not None else out
    # 2) 相对日期 + 时间：今天 6:35 / 昨天6点35 / 前天 6:35
    m = re.match(r"^(今天|昨天|前天)\s*(\d{1,2})\s*[:：点时]\s*(\d{1,2})\s*分?", s)
    if m:
        delta = {"今天": 0, "昨天": -1, "前天": -2}[m.group(1)]
        day = (now() + datetime.timedelta(days=delta)).date().isoformat()
        hh, mm = int(m.group(2)), int(m.group(3))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{day} {hh:02d}:{mm:02d}:00"
    # 3) 裸时间：6:35 / 06:35 / 6点35 / 6时35分（按今天补全）
    m = re.match(r"^(\d{1,2})\s*[:：点时]\s*(\d{1,2})\s*分?$", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{now().date().isoformat()} {hh:02d}:{mm:02d}:00"
    # 4) 缺年份：-05-02 06:30 / 05-02 06:30（保持原样，不擅自加秒）
    m = re.match(r"^-?(\d{2}-\d{2} \d{2}:\d{2}.*)$", s)
    if m:
        return f"{now().year}-{m.group(1)}"
    logger.warning("normalize_input: 无法识别 %r，回退到当前时间", ts)
    return now_str()
