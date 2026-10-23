#!/usr/bin/env python3
"""Sugar Bee 只读分析 MCP server（12 个 ``sbhealth_*`` 工具）。

供 Octop / 分析型 agent 查询血糖、体重、运动、血压、用药数据。
所有查询严格按 user_id 过滤，SQLite 以 ``mode=ro`` 打开，绝不写入。

运行方式：
  # 仓库内
  uv run python -m mcp_adapter.servers.reader
  # 根目录入口（部署脚本使用，与本地同名）
  uv run python mcp_reader.py

环境变量：
  SB_MCP_DB        数据库路径（默认 /opt/sugar_bee/app/db_data/glucose.db）
  SB_MCP_USER_ID   默认用户（默认 1）
  SB_MCP_TS_MODE   时间戳口径 local（默认）/ utc
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 允许以脚本方式直接运行时（python mcp_adapter/servers/reader.py）导入包内核心。
# 独立部署时需同步 mcp_adapter/core/ 与 utils/timezone.py，
# 见 docs/mcp-architecture-review.md「部署」一节。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mcp_adapter.core import analytics, domain, timefmt  # noqa: E402

DB_PATH = os.environ.get("SB_MCP_DB", "/opt/sugar_bee/app/db_data/glucose.db")
DEFAULT_USER_ID = int(os.environ.get("SB_MCP_USER_ID", "1"))

mcp = FastMCP(
    "sugar-bee-health",
    instructions=(
        f"查询糖友（user_id={DEFAULT_USER_ID}）在 Sugar Bee 中记录的健康数据：血糖、体重、"
        "运动、血压、用药。全部为只读查询。血糖单位 mmol/L，体重 kg，血压 mmHg，"
        "距离 km。回答时用中文，数据要说明统计口径和时间范围。"
    ),
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cutoff(days: int) -> str:
    """回溯窗口起点。口径见 core.timefmt（默认按本地墙钟，不再假定 UTC）。"""
    return timefmt.cutoff(days)


def _parse_duration_minutes(text: str | None) -> float | None:
    """把 '41min' / '1h20m' / '45分钟' / '45' 这类时长解析成分钟。"""
    return analytics.duration_minutes(text)


def _fmt_ts(ts: str | None) -> str | None:
    """时间戳转展示字符串（YYYY-MM-DD HH:MM），口径见 core.timefmt。"""
    return timefmt.display(ts)


# SQLite 日期/小时分组是否需要 UTC→本地平移。
# 默认 "local"：库里的 timestamp 本身就是本地墙钟，不能再用 SQL 加 8 小时。
_TS_SHIFT = ", '+8 hours'" if timefmt.ts_mode() == timefmt.UTC else ""


def _in_clause(values: tuple[str, ...]) -> str:
    """生成 IN 子句占位符，避免把类型列表硬编码进每条 SQL。"""
    return ",".join("?" * len(values))


def _type_in_sql(values: tuple[str, ...]) -> str:
    """把内部常量元组拼成 SQL IN 字面量（取值来自代码常量，不含用户输入）。"""
    joined = ",".join(f"'{v}'" for v in values)
    return f"type in ({joined})"



@mcp.tool()
def sbhealth_overview(days: int = 30) -> dict:
    """总览：最近 N 天各维度的最新记录和概要，适合作为开场摸底。

    Args:
        days: 回溯天数，默认 30
    """
    conn = _connect()
    try:
        out: dict = {"user_id": DEFAULT_USER_ID, "window_days": days}

        row = conn.execute(
            "select timestamp, value, type from records "
            "where user_id=? and timestamp>=? and type in ("
            + _in_clause(domain.GLUCOSE_LATEST_TYPES) + ") "
            "order by timestamp desc limit 1",
            (DEFAULT_USER_ID, _cutoff(days), *domain.GLUCOSE_LATEST_TYPES),
        ).fetchone()
        out["latest_glucose"] = (
            {"time": _fmt_ts(row["timestamp"]), "value": row["value"], "unit": "mmol/L", "type": row["type"]}
            if row else None
        )

        row = conn.execute(
            "select timestamp, weight, bmi from records "
            "where user_id=? and weight is not null order by timestamp desc limit 1",
            (DEFAULT_USER_ID,),
        ).fetchone()
        out["latest_weight"] = (
            {"time": _fmt_ts(row["timestamp"]), "weight_kg": row["weight"], "bmi": row["bmi"]}
            if row else None
        )

        row = conn.execute(
            "select timestamp, systolic_pressure, diastolic_pressure, pulse_rate from records "
            "where user_id=? and systolic_pressure is not null order by timestamp desc limit 1",
            (DEFAULT_USER_ID,),
        ).fetchone()
        out["latest_blood_pressure"] = (
            {
                "time": _fmt_ts(row["timestamp"]),
                "systolic": row["systolic_pressure"],
                "diastolic": row["diastolic_pressure"],
                "pulse": row["pulse_rate"],
            }
            if row else None
        )

        row = conn.execute(
            "select count(*) c, max(timestamp) t from records "
            "where user_id=? and timestamp>=? and type in ("
            + _in_clause(domain.EXERCISE_TYPES) + ")",
            (DEFAULT_USER_ID, _cutoff(days), *domain.EXERCISE_TYPES),
        ).fetchone()
        out["exercise_sessions_in_window"] = row["c"]
        out["last_exercise_time"] = _fmt_ts(row["t"])

        row = conn.execute(
            "select count(*) c, max(timestamp) t from records where user_id=? and timestamp>=?",
            (DEFAULT_USER_ID, _cutoff(days)),
        ).fetchone()
        out["total_records_in_window"] = row["c"]
        return out
    finally:
        conn.close()


@mcp.tool()
def sbhealth_glucose_summary(days: int = 30) -> dict:
    """血糖统计：均值、波动范围、目标范围内时间占比(TIR)、高/低血糖次数、按餐次拆分。

    Args:
        days: 回溯天数，默认 30
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "select timestamp, value, type from records "
            "where user_id=? and timestamp>=? and value>0 and value<40 "
            "and (unit='mmol/L' or type in ("
            + _in_clause(domain.GLUCOSE_SUMMARY_TYPES) + ")) "
            "order by timestamp",
            (DEFAULT_USER_ID, _cutoff(days), *domain.GLUCOSE_SUMMARY_TYPES),
        ).fetchall()

        if not rows:
            return {"user_id": DEFAULT_USER_ID, "window_days": days, "samples": 0, "note": "窗口内无血糖记录"}

        values = [r["value"] for r in rows]
        n = len(values)
        mean = sum(values) / n
        in_range = sum(1 for v in values if domain.in_target_range(v))
        high = sum(1 for v in values if v > domain.TIR_HIGH)
        low = sum(1 for v in values if v < domain.TIR_LOW)

        by_meal: dict[str, dict] = {}
        for r in rows:
            t = r["type"] or "其他"
            b = by_meal.setdefault(t, {"count": 0, "sum": 0.0, "max": None, "min": None})
            b["count"] += 1
            b["sum"] += r["value"]
            b["max"] = r["value"] if b["max"] is None else max(b["max"], r["value"])
            b["min"] = r["value"] if b["min"] is None else min(b["min"], r["value"])
        for b in by_meal.values():
            b["mean"] = round(b["sum"] / b["count"], 2)
            del b["sum"]

        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days,
            "samples": n,
            "mean_mmol_l": round(mean, 2),
            "min_mmol_l": round(min(values), 2),
            "max_mmol_l": round(max(values), 2),
            "target_range": f"{domain.TIR_LOW:.1f}-{domain.TIR_HIGH:.1f} mmol/L",
            "tir_percent": round(in_range * 100.0 / n, 1),
            "above_target_count": high,
            "below_target_count": low,
            "first_record": _fmt_ts(rows[0]["timestamp"]),
            "last_record": _fmt_ts(rows[-1]["timestamp"]),
            "by_measure_type": by_meal,
        }
    finally:
        conn.close()


@mcp.tool()
def sbhealth_glucose_records(days: int = 7, meal_type: str | None = None,
                            limit: int = 60, offset: int = 0) -> dict:
    """血糖明细记录，按时间倒序。

    Args:
        days: 回溯天数，默认 7
        meal_type: 可选，按测量类型过滤，如 空腹/餐后2小时/睡前/CGM/运动后
        limit: 最多返回条数，默认 60，上限 500
        offset: 跳过前 N 条，用于翻页
    """
    conn = _connect()
    try:
        sql = (
            "select timestamp, value, unit, type, notes from records "
            "where user_id=? and timestamp>=? and value>0"
        )
        params: list = [DEFAULT_USER_ID, _cutoff(days)]
        if meal_type:
            sql += " and type=?"
            params.append(meal_type)
        sql += " order by timestamp desc limit ? offset ?"
        params.append(min(max(limit, 1), 500))
        params.append(max(offset, 0))

        rows = conn.execute(sql, params).fetchall()
        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days,
            "meal_type_filter": meal_type,
            "count": len(rows),
            "offset": max(offset, 0),
            "has_more": len(rows) >= min(max(limit, 1), 500),
            "records": [
                {
                    "time": _fmt_ts(r["timestamp"]),
                    "value": r["value"],
                    "unit": r["unit"] or "mmol/L",
                    "type": r["type"],
                    "notes": r["notes"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


@mcp.tool()
def sbhealth_weight_trend(days: int = 90) -> dict:
    """体重与 BMI 趋势。

    Args:
        days: 回溯天数，默认 90
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "select timestamp, weight, bmi from records "
            "where user_id=? and timestamp>=? and weight is not null and weight>0 order by timestamp",
            (DEFAULT_USER_ID, _cutoff(days)),
        ).fetchall()
        if not rows:
            return {"user_id": DEFAULT_USER_ID, "window_days": days, "samples": 0}
        weights = [r["weight"] for r in rows]
        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days,
            "samples": len(rows),
            "latest_kg": weights[-1],
            "latest_bmi": rows[-1]["bmi"],
            "first_kg": weights[0],
            "change_kg": round(weights[-1] - weights[0], 2),
            "mean_kg": round(sum(weights) / len(weights), 2),
            "series": [
                {"time": _fmt_ts(r["timestamp"]), "weight_kg": r["weight"], "bmi": r["bmi"]}
                for r in rows[-60:]
            ],
        }
    finally:
        conn.close()


@mcp.tool()
def sbhealth_exercise_summary(days: int = 30) -> dict:
    """运动汇总：次数、总距离、总时长、总步数、平均心率，以及每次明细。

    Args:
        days: 回溯天数，默认 30
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "select timestamp, type, distance, duration, heart_rate, calories, steps, pace, cadence "
            "from records where user_id=? and timestamp>=? and type in ("
            + _in_clause(domain.EXERCISE_TYPES) + ") order by timestamp desc",
            (DEFAULT_USER_ID, _cutoff(days), *domain.EXERCISE_TYPES),
        ).fetchall()

        sessions = []
        total_distance = 0.0
        total_minutes = 0.0
        total_steps = 0
        total_calories = 0
        heart_rates = []
        for r in rows:
            mins = _parse_duration_minutes(r["duration"])
            dist = r["distance"] or 0
            total_distance += dist
            total_minutes += mins or 0
            total_steps += r["steps"] or 0
            total_calories += r["calories"] or 0
            if r["heart_rate"]:
                heart_rates.append(r["heart_rate"])
            sessions.append(
                {
                    "time": _fmt_ts(r["timestamp"]),
                    "type": r["type"],
                    "distance_km": dist,
                    "duration": r["duration"],
                    "duration_minutes": round(mins, 1) if mins else None,
                    "avg_heart_rate": r["heart_rate"],
                    "calories": r["calories"],
                    "steps": r["steps"],
                    "pace": r["pace"],
                }
            )
        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days,
            "session_count": len(rows),
            "total_distance_km": round(total_distance, 2),
            "total_minutes": round(total_minutes, 1),
            "total_steps": total_steps,
            "total_calories": total_calories,
            "avg_heart_rate": round(sum(heart_rates) / len(heart_rates), 1) if heart_rates else None,
            "sessions": sessions[:50],
        }
    finally:
        conn.close()


@mcp.tool()
def sbhealth_blood_pressure(days: int = 30) -> dict:
    """血压汇总与明细。

    Args:
        days: 回溯天数，默认 30
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "select timestamp, type, systolic_pressure, diastolic_pressure, pulse_rate "
            "from records where user_id=? and timestamp>=? and systolic_pressure is not null "
            "order by timestamp desc",
            (DEFAULT_USER_ID, _cutoff(days)),
        ).fetchall()
        if not rows:
            return {"user_id": DEFAULT_USER_ID, "window_days": days, "samples": 0}
        sys_v = [r["systolic_pressure"] for r in rows]
        dia_v = [r["diastolic_pressure"] for r in rows]
        high = sum(1 for r in rows if domain.is_high_bp(r["systolic_pressure"], r["diastolic_pressure"]))
        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days,
            "samples": len(rows),
            "systolic_mean": round(sum(sys_v) / len(sys_v), 1),
            "systolic_max": max(sys_v),
            "diastolic_mean": round(sum(dia_v) / len(dia_v), 1),
            "diastolic_max": max(dia_v),
            "above_140_90_count": high,
            "records": [
                {
                    "time": _fmt_ts(r["timestamp"]),
                    "type": r["type"],
                    "systolic": r["systolic_pressure"],
                    "diastolic": r["diastolic_pressure"],
                    "pulse": r["pulse_rate"],
                }
                for r in rows[:50]
            ],
        }
    finally:
        conn.close()


@mcp.tool()
def sbhealth_medication_log(days: int = 60) -> dict:
    """用药记录。

    Args:
        days: 回溯天数，默认 60
    """
    conn = _connect()
    try:
        rows = conn.execute(
            "select timestamp, type, medication_name, notes from records "
            "where user_id=? and timestamp>=? and medication_name is not null and medication_name<>'' "
            "order by timestamp desc",
            (DEFAULT_USER_ID, _cutoff(days)),
        ).fetchall()
        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days,
            "count": len(rows),
            "records": [
                {
                    "time": _fmt_ts(r["timestamp"]),
                    "type": r["type"],
                    "medication": r["medication_name"],
                    "notes": r["notes"],
                }
                for r in rows[:100]
            ],
        }
    finally:
        conn.close()


@mcp.tool()
def sbhealth_data_coverage() -> dict:
    """数据覆盖情况：最早/最晚记录时间、总量、各测量类型条数。用于判断数据是否够分析。"""
    conn = _connect()
    try:
        total = conn.execute(
            "select count(*) c, min(timestamp) lo, max(timestamp) hi from records where user_id=?",
            (DEFAULT_USER_ID,),
        ).fetchone()
        by_type = {
            r["type"]: r["c"]
            for r in conn.execute(
                "select type, count(*) c from records where user_id=? group by type order by c desc",
                (DEFAULT_USER_ID,),
            )
        }
        return {
            "user_id": DEFAULT_USER_ID,
            "total_records": total["c"],
            "first_record": _fmt_ts(total["lo"]),
            "last_record": _fmt_ts(total["hi"]),
            "by_type": by_type,
        }
    finally:
        conn.close()


# ============ 分析型工具（第三层） ============
# 原则：全量数据在 server 侧算完，只把结论回给 agent，
# 避免把 6.94 MB / 208 万 token 的原始明细塞进模型上下文。

@mcp.tool()
def sbhealth_trend_analysis(days: int = 90) -> dict:
    """血糖 / 体重 / 运动的多维趋势：斜率、首末对比、环比方向。

    全部计算在 server 侧完成，只回结论。日期按北京时间（UTC+8）聚合。
    """
    conn = _connect()
    try:
        cutoff = _cutoff(days)
        out: dict = {"window_days": days, "cutoff": cutoff, "note": "时间均为北京时间"}

        rows = conn.execute(
            "select strftime('%Y-%W', timestamp" + _TS_SHIFT + ") as wk, "
            "avg(value) as avg_v, count(*) as n from records "
            "where user_id=? and value is not null and timestamp>=? "
            "and type in (" + _in_clause(domain.GLUCOSE_ANALYSIS_TYPES) + ") "
            "group by wk order by wk",
            (DEFAULT_USER_ID, cutoff, *domain.GLUCOSE_ANALYSIS_TYPES),
        ).fetchall()
        if len(rows) >= 2:
            ys = [r[1] for r in rows]
            f_avg, s_avg, change = analytics.half_change(ys)
            out["glucose"] = {
                "weeks": len(rows),
                "weekly_avg_mmol": [round(y, 2) for y in ys],
                "slope_mmol_per_week": round(analytics.linear_slope(ys), 4),
                "first_half_avg": f_avg,
                "second_half_avg": s_avg,
                "change_mmol": change,
                "direction": analytics.direction(change),
            }

        wrows = conn.execute(
            "select timestamp, weight from records "
            "where user_id=? and weight is not null and weight>0 and timestamp>=? "
            "order by timestamp",
            (DEFAULT_USER_ID, cutoff),
        ).fetchall()
        if len(wrows) >= 2:
            delta = wrows[-1][1] - wrows[0][1]
            out["weight"] = {
                "count": len(wrows),
                "first_kg": round(wrows[0][1], 1),
                "last_kg": round(wrows[-1][1], 1),
                "change_kg": round(delta, 1),
                "direction": analytics.direction(delta, tolerance=0.5),
            }

        erows = conn.execute(
            "select strftime('%Y-%m', timestamp" + _TS_SHIFT + ") as ym, count(*) as n, "
            "sum(distance) as dist from records "
            "where user_id=? and timestamp>=? and type in ("
            + _in_clause(domain.EXERCISE_TYPES) + ") "
            "group by ym order by ym",
            (DEFAULT_USER_ID, cutoff, *domain.EXERCISE_TYPES),
        ).fetchall()
        if erows:
            counts = [r[1] for r in erows]
            avg_c = sum(counts) / len(counts)
            out["exercise"] = {
                "months": [{"month": r[0], "sessions": r[1],
                            "distance_km": round(r[2] or 0, 1)} for r in erows],
                "avg_sessions_per_month": round(avg_c, 1),
                "direction": "上升" if counts[-1] > avg_c
                else ("下降" if counts[-1] < avg_c * 0.8 else "基本平稳"),
            }
        return out
    finally:
        conn.close()


@mcp.tool()
def sbhealth_exercise_glucose_correlation(days: int = 90) -> dict:
    """按「当日运动距离」分层，对比各层的血糖表现。

    原本按「有运动 / 无运动」分组，但用户几乎每天都有步行记录，对照组为空。
    改为按当日总运动距离分三档（单位 km，数据库 distance 字段即 km）：
      - high:  当日距离 >= 3 km
      - mid:   1 km <= 当日距离 < 3 km
      - low:   当日距离 < 1 km
    日期一律按北京时间（UTC+8）切分。
    """
    conn = _connect()
    try:
        cutoff = _cutoff(days)
        ex_types = domain.EXERCISE_TYPES
        placeholders = _in_clause(ex_types)

        # 每日运动距离
        day_dist = {
            r[0]: (r[1] or 0.0) for r in conn.execute(
                "select date(timestamp" + _TS_SHIFT + ") as d, sum(distance) from records "
                "where user_id=? and timestamp>=? and type in (" + placeholders + ") "
                "group by d",
                (DEFAULT_USER_ID, cutoff, *ex_types),
            )
        }

        # 每日血糖均值
        day_glu = {
            r[0]: r[1] for r in conn.execute(
                "select date(timestamp" + _TS_SHIFT + ") as d, avg(value) from records "
                "where user_id=? and timestamp>=? and value is not null "
                "and type in (" + _in_clause(domain.GLUCOSE_ANALYSIS_TYPES) + ") "
                "group by d",
                (DEFAULT_USER_ID, cutoff, *domain.GLUCOSE_ANALYSIS_TYPES),
            )
        }

        buckets: dict = {"high": [], "mid": [], "low": []}
        for d, dist in day_dist.items():
            if d not in day_glu:
                continue
            if dist >= domain.EXERCISE_KM_HIGH:
                buckets["high"].append(day_glu[d])
            elif dist >= domain.EXERCISE_KM_LOW:
                buckets["mid"].append(day_glu[d])
            else:
                buckets["low"].append(day_glu[d])

        def stat(vals: list) -> dict:
            if not vals:
                return {"days": 0}
            return {
                "days": len(vals),
                "avg_mmol": round(sum(vals) / len(vals), 2),
                "max_mmol": round(max(vals), 2),
                "min_mmol": round(min(vals), 2),
            }

        out = {
            "window_days": days,
            "note": "时间均为北京时间，按当日运动距离分层",
            "high_ge3km": stat(buckets["high"]),
            "mid_1to3km": stat(buckets["mid"]),
            "low_lt1km": stat(buckets["low"]),
            "days_with_both_data": sum(len(v) for v in buckets.values()),
        }

        hi = out["high_ge3km"].get("avg_mmol")
        lo = out["low_lt1km"].get("avg_mmol")
        filled = [k for k in ("high_ge3km", "mid_1to3km", "low_lt1km")
                  if out[k].get("days")]
        if hi and lo:
            diff = hi - lo
            out["diff_high_minus_low"] = round(diff, 2)
            out["conclusion"] = (
                f"高运动量日（>=3km）平均血糖 {hi:.2f}，低运动量日（<1km）平均血糖 {lo:.2f}，"
                f"相差 {'高 ' if diff > 0 else '低 '}{abs(diff):.2f} mmol/L"
            )
        elif len(filled) == 1:
            only = out[filled[0]]
            label = {"high_ge3km": "高运动量（>=3km）",
                     "mid_1to3km": "中等运动量（1-3km）",
                     "low_lt1km": "低运动量（<1km）"}[filled[0]]
            out["conclusion"] = (
                f"有血糖记录的 {only['days']} 天全部落在{label}档，其他档位没有数据，无法做分层对比。"
                f"该档平均血糖 {only['avg_mmol']:.2f} mmol/L（范围 {only['min_mmol']:.2f}~{only['max_mmol']:.2f}）。"
            )
        else:
            out["conclusion"] = "数据不足，无法比较（需要有血糖记录且运动距离分布在不同档位）"
        return out
    finally:
        conn.close()


@mcp.tool()
def sbhealth_anomaly_alerts(days: int = 30) -> dict:
    """扫描异常事件：低血糖、高血糖、夜间高血糖、血压偏高。

    阈值：
    - 低血糖：value < 3.9
    - 高血糖：value > 13.9
    - 夜间高血糖：CGM 且北京时间 02-05 点且 value > 10.0
    - 血压偏高：收缩压 >= 140 或 舒张压 >= 90
    时间一律按北京时间（UTC+8）判断。
    """
    conn = _connect()
    try:
        cutoff = _cutoff(days)
        alerts: list = []

        for r in conn.execute(
            "select timestamp, value, type from records "
            "where user_id=? and timestamp>=? and value is not null "
            "and type in (" + _in_clause(domain.GLUCOSE_ANALYSIS_TYPES) + ") "
            "order by timestamp",
            (DEFAULT_USER_ID, cutoff, *domain.GLUCOSE_ANALYSIS_TYPES),
        ).fetchall():
            ts, v, t = r
            if v < domain.HYPOGLYCEMIA:
                alerts.append({"type": "低血糖", "time": _fmt_ts(ts),
                               "detail": f"{v:.1f} mmol/L（{t}）"})
            elif v > domain.HYPERGLYCEMIA:
                alerts.append({"type": "高血糖", "time": _fmt_ts(ts),
                               "detail": f"{v:.1f} mmol/L（{t}）"})
            if t == "CGM":
                hour = conn.execute(
                    "select cast(strftime('%H', ?" + _TS_SHIFT + ") as integer)", (ts,)
                ).fetchone()[0]
                if hour in domain.NOCTURNAL_HOURS and v > domain.NOCTURNAL_HIGH:
                    alerts.append({"type": "夜间高血糖", "time": _fmt_ts(ts),
                                   "detail": f"{v:.1f} mmol/L（凌晨 {hour} 点）"})

        for r in conn.execute(
            "select timestamp, systolic_pressure, diastolic_pressure, pulse_rate "
            "from records where user_id=? and timestamp>=? "
            "and systolic_pressure is not null order by timestamp",
            (DEFAULT_USER_ID, cutoff),
        ).fetchall():
            ts, sys_v, dia_v, pr = r
            if domain.is_high_bp(sys_v, dia_v):
                pulse = f"，脉搏 {pr}" if pr else ""
                alerts.append({
                    "type": "血压偏高", "time": _fmt_ts(ts),
                    "detail": f"{sys_v}/{dia_v} mmHg{pulse}",
                })

        counts: dict = {}
        for a in alerts:
            counts[a["type"]] = counts.get(a["type"], 0) + 1
        return {
            "window_days": days,
            "note": "时间均为北京时间",
            "total": len(alerts),
            "summary": counts,
            "recent_10": alerts[-10:],
        }
    finally:
        conn.close()


# ============ 导出工具（全量数据的合规出口） ============

# 虚拟根为 /root（backend: local_shell + virtual_mode + root_dir=/root），
# agent 侧路径要去掉 /root 前缀，否则会被解析成 /root/root/...
_VIRTUAL_ROOT = "/root"

# 只允许写这两个目录，防路径穿越
_EXPORT_BASE = "/root/health_tools/exports"
_WORKSPACES_BASE = "/root/.octop/workspaces"

# 维度 -> (SQL 列, 中文表头, 类型过滤)
_EXPORT_SPECS = {
    "glucose": (
        "timestamp, value, unit, type, notes",
        ["时间", "血糖值", "单位", "测量类型", "备注"],
        _type_in_sql(domain.GLUCOSE_ANALYSIS_TYPES) + " and value is not null",
    ),
    "weight": (
        "timestamp, weight, bmi",
        ["时间", "体重kg", "BMI"],
        "weight is not null and weight > 0",
    ),
    "exercise": (
        "timestamp, type, distance, duration, heart_rate, calories, steps, pace",
        ["时间", "运动类型", "距离km", "时长", "心率", "千卡", "步数", "配速"],
        _type_in_sql(domain.EXERCISE_TYPES),
    ),
    "blood_pressure": (
        "timestamp, systolic_pressure, diastolic_pressure, pulse_rate",
        ["时间", "收缩压", "舒张压", "脉搏"],
        "systolic_pressure is not null",
    ),
    "medication": (
        "timestamp, medication_name, value, unit, notes",
        ["时间", "药品", "剂量", "单位", "备注"],
        "medication_name is not null",
    ),
}


def _to_virtual_path(real: str) -> str:
    """真实路径转 agent 可读的虚拟路径（去掉 /root 前缀）。"""
    if real.startswith(_VIRTUAL_ROOT):
        return real[len(_VIRTUAL_ROOT):] or "/"
    return real


def _resolve_export_dir(workspace: str | None) -> tuple[str, str]:
    """解析导出目录，返回 (真实目录, 虚拟目录)。带白名单校验，防路径穿越。"""
    if not workspace:
        os.makedirs(_EXPORT_BASE, exist_ok=True)
        return _EXPORT_BASE, _to_virtual_path(_EXPORT_BASE)

    # 只接受纯 ID，拒绝任何路径分隔符
    if os.sep in workspace or "/" in workspace or workspace in (".", ".."):
        raise ValueError("workspace 只接受 agent ID，不能带路径")
    target = os.path.join(_WORKSPACES_BASE, workspace, "outbound")
    # 二次确认仍在白名单内
    real = os.path.realpath(target)
    if not real.startswith(os.path.realpath(_WORKSPACES_BASE) + os.sep):
        raise ValueError("非法工作区路径")
    os.makedirs(real, exist_ok=True)
    return real, _to_virtual_path(real)


def _prune_exports(export_dir: str, keep: int = 10) -> int:
    """清理旧导出文件，只保留最近 keep 个，返回删除数量。"""
    files = sorted(
        (f for f in Path(export_dir).glob("sugar_bee_*.csv")),
        key=lambda p: p.stat().st_mtime,
    )
    removed = 0
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _csv_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


@mcp.tool()
def sbhealth_export(
    data_type: str = "all",
    days: int | None = None,
    workspace: str | None = None,
    max_rows: int = 50000,
) -> dict:
    """把健康数据导出为 CSV 文件，供 agent 用 read_file 分页读取。

    适用于需要翻看原始明细的场景（汇总和趋势请优先用其他 sbhealth_* 工具）。
    文件写入白名单目录，数据库仍以只读模式打开，绝不写入。

    Args:
        data_type: glucose / weight / exercise / blood_pressure / medication / all，
                   默认 all（全维度导出为多个文件）
        days: 回溯天数，不传则导出全部历史
        workspace: 可选，指定写入某个 agent 的工作区 outbound 目录（只接受 agent ID）；
                   不传则写入共享导出目录
        max_rows: 单维度行数上限，默认 50000，防止内存爆炸

    Returns:
        包含虚拟路径、行数、列名、文件大小和分页读取建议的字典
    """
    conn = _connect()
    try:
        real_dir, virtual_dir = _resolve_export_dir(workspace)
        cutoff = _cutoff(days) if days else "0000-01-01 00:00:00"
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        wanted = list(_EXPORT_SPECS) if data_type == "all" else [data_type]
        unknown = [t for t in wanted if t not in _EXPORT_SPECS]
        if unknown:
            return {
                "error": f"未知维度: {', '.join(unknown)}",
                "valid_types": list(_EXPORT_SPECS) + ["all"],
            }

        files = []
        for key in wanted:
            cols, headers, where = _EXPORT_SPECS[key]
            sql = (
                f"select {cols} from records where user_id=? and timestamp>=? and {where} "
                "order by timestamp"
            )
            rows = conn.execute(sql, (DEFAULT_USER_ID, cutoff)).fetchall()
            if not rows:
                continue
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[-max_rows:]

            name = f"sugar_bee_{key}_{stamp}.csv"
            path = os.path.join(real_dir, name)
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(headers)
                for r in rows:
                    cells = []
                    for i, v in enumerate(r):
                        # 第一列是 UTC 时间戳，转北京时间
                        cells.append(_fmt_ts(v) if i == 0 else _csv_cell(v))
                    w.writerow(cells)

            size_kb = round(os.path.getsize(path) / 1024, 1)
            files.append({
                "data_type": key,
                "file_path": _to_virtual_path(path),
                "rows": len(rows),
                "truncated": truncated,
                "columns": headers,
                "size_kb": size_kb,
            })

        if not files:
            return {"error": "指定范围内没有数据", "data_type": data_type, "days": days}

        total_rows = sum(f["rows"] for f in files)
        return {
            "user_id": DEFAULT_USER_ID,
            "window_days": days if days else "全部历史",
            "note": "文件时间为北京时间（UTC+8），CSV 带 BOM，Excel 可直接打开",
            "files": files,
            "total_rows": total_rows,
            "pruned_old_files": _prune_exports(real_dir),
            "read_hint": (
                "用 read_file 读，path 用上面的 file_path（虚拟路径，不要加 /root 前缀）。"
                f"默认每次返回 100 行，靠 offset 翻页；{total_rows} 行约需 "
                f"{max(1, -(-total_rows // 100))} 次读取，"
                "建议先只读尾部最新数据，或改用汇总类工具。"
            ),
        }
    finally:
        conn.close()


def main() -> None:
    """stdio 入口（MCP 客户端拉起后即阻塞在此）。"""
    mcp.run()


if __name__ == "__main__":
    main()
