#!/usr/bin/env python3
"""
Sugar Bee MCP Server
支持 stdio（Claude Desktop）与 sse（通用 MCP 客户端）双模式。

环境变量:
  AGENT_API_TOKEN  (必需) — Phase 1 生成的 token
  API_BASE         (可选) — Flask 地址，默认 http://127.0.0.1:5001
  DB_PATH          (可选) — SQLite 路径，默认 glucose.db

用法:
  # stdio 模式（Claude Desktop）
  uv run python mcp_server.py

  # sse 模式（通用 MCP 客户端，默认端口 3001）
  uv run python mcp_server.py --transport sse --port 3001

Claude Desktop 配置示例 (~/Library/Application Support/Claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "sugar-bee": {
        "command": "uv",
        "args": ["run", "python", "mcp_server.py"],
        "env": {
          "AGENT_API_TOKEN": "<your-token>"
        }
      }
    }
  }
"""
import argparse
import os
import re
import sqlite3
from datetime import datetime
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "glucose.db"))
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:5001")
AGENT_API_TOKEN = os.environ.get("AGENT_API_TOKEN", "")
API_TIMEOUT = float(os.environ.get("API_TIMEOUT", "60.0"))
DEFAULT_USER_ID = int(os.environ.get("DEFAULT_USER_ID", "1"))

mcp = FastMCP("sugar-bee")


def _normalize_timestamp(ts: Optional[str] = None) -> str:
    """校验并补全时间戳，确保格式为 YYYY-MM-DD HH:MM:SS。"""
    if not ts:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 已经是完整格式
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}", ts):
        return ts
    # 缺少年份：-MM-DD HH:MM 或 MM-DD HH:MM → 补当年
    m = re.match(r"^-?(\d{2}-\d{2} \d{2}:\d{2}.*)$", ts)
    if m:
        return f"{datetime.now().year}-{m.group(1)}"
    # 其他无法识别的格式，回退到当前时间
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _api_headers(user_id: int) -> dict:
    return {
        "X-Agent-Token": AGENT_API_TOKEN,
        "X-User-Id": str(user_id),
        "Content-Type": "application/json",
    }


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


async def _api_post(user_id: int, endpoint: str, payload: dict) -> dict:
    """向 Flask API 发 POST，返回 JSON。409 冲突不抛异常，返回含 error 的 dict。"""
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            r = await client.post(
                f"{API_BASE}{endpoint}",
                headers=_api_headers(user_id),
                json=payload,
                timeout=API_TIMEOUT,
            )
            if r.status_code == 409:
                return r.json()
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        return {
            "status": "error",
            "error_type": "connection_error",
            "message": f"无法连接到后端服务（{API_BASE}）。请检查 Flask 应用是否已启动。",
        }
    except httpx.TimeoutException:
        return {
            "status": "error",
            "error_type": "timeout",
            "message": f"后端服务响应超时（{API_TIMEOUT}秒）。请稍后重试。",
        }
    except httpx.HTTPStatusError as e:
        return {
            "status": "error",
            "error_type": "http_error",
            "message": f"后端返回错误：{e.response.status_code} - {e.response.text[:200]}",
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": "unknown",
            "message": f"请求异常：{type(e).__name__}: {str(e)[:200]}",
        }


def _is_dup_error(data: dict) -> str | None:
    """检查 API 返回是否为重复记录冲突，返回错误信息或 None。"""
    if data.get("status") == "error" and data.get("error_type") == "duplicate":
        return data.get("message", "重复记录")
    return None


# ------------------------------------------------------------------
# 内联业务逻辑（供 batch_record / batch_parse_and_record 使用）
# 以下函数对照 Flask routes/api_records.py 实现，确保行为一致
# ------------------------------------------------------------------

def _validate_record_data(r: dict) -> list[str]:
    """校验单条记录的数据范围，返回警告信息列表（空列表表示无警告）。
    与 Flask 端 _validate_record_data() 保持一致。"""
    warnings: list[str] = []
    systolic = r.get("systolic_pressure")
    diastolic = r.get("diastolic_pressure")
    if systolic and diastolic:
        if systolic < 60 or systolic > 250:
            warnings.append(f"收缩压 {systolic} 超出正常范围（60-250）")
        if diastolic < 40 or diastolic > 180:
            warnings.append(f"舒张压 {diastolic} 超出正常范围（40-180）")
        if systolic <= diastolic:
            warnings.append(f"收缩压（{systolic}）不应小于等于舒张压（{diastolic}）")

    spo2 = r.get("spo2")
    if spo2 is not None and (spo2 < 90 or spo2 > 100):
        warnings.append(f"血氧饱和度 {spo2}% 超出正常范围（90-100%），可能被误填")

    pulse = r.get("pulse_rate")
    if pulse is not None and (pulse < 30 or pulse > 220):
        warnings.append(f"脉搏 {pulse} 超出正常范围（30-220）")

    value = r.get("value")
    if value and value > 0 and not systolic and not r.get("weight"):
        if value < 1.0 or value > 33.3:
            warnings.append(f"血糖值 {value} 超出正常范围（1.0-33.3 mmol/L）")

    weight = r.get("weight")
    if weight and weight > 0:
        if weight < 20.0 or weight > 300.0:
            warnings.append(f"体重 {weight} 超出正常范围（20-300 kg）")

    return warnings


def _check_duplicate(conn: sqlite3.Connection, user_id: int, record: dict) -> str | None:
    """检测重复记录。与 Flask /add 和 /batch_add 的重复检测逻辑保持一致。
    返回重复信息或 None。"""
    c = conn.cursor()
    ts = record.get("datetime") or record.get("timestamp", "")
    if not ts:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    systolic = record.get("systolic_pressure")
    diastolic = record.get("diastolic_pressure")
    weight = record.get("weight")
    value = record.get("value")
    rtype = record.get("type", "")
    is_pred = record.get("is_predicted", False)

    if systolic and diastolic:
        c.execute(
            """SELECT id, timestamp FROM records
               WHERE user_id = ? AND systolic_pressure = ? AND diastolic_pressure = ?
               AND timestamp BETWEEN datetime(?, '-3 minutes') AND datetime(?, '+3 minutes')
               LIMIT 1""",
            (user_id, systolic, diastolic, ts, ts),
        )
        dup = c.fetchone()
        if dup:
            return f"3 分钟内已有相同血压记录 (ID: {dup['id']}, 时间: {dup['timestamp']})"
    elif weight and weight > 0:
        c.execute(
            """SELECT id, timestamp FROM records
               WHERE user_id = ? AND weight = ?
               AND timestamp BETWEEN datetime(?, '-3 minutes') AND datetime(?, '+3 minutes')
               LIMIT 1""",
            (user_id, weight, ts, ts),
        )
        dup = c.fetchone()
        if dup:
            return f"3 分钟内已有相同体重记录 (ID: {dup['id']}, 时间: {dup['timestamp']})"
    elif value and value > 0 and not is_pred:
        c.execute(
            """SELECT id, timestamp, value FROM records
               WHERE user_id = ? AND type = ? AND date(timestamp) = date(?)
               AND is_predicted = 0 AND value > 0 AND systolic_pressure IS NULL AND weight IS NULL
               LIMIT 1""",
            (user_id, rtype, ts),
        )
        dup = c.fetchone()
        if dup:
            return f"今日已有「{rtype}」记录 (ID: {dup['id']}, 值: {dup['value']}, 时间: {dup['timestamp']})"
    return None


def _calculate_and_set_bmi(conn: sqlite3.Connection, user_id: int, weight: float) -> float | None:
    """根据用户档案身高计算 BMI。与 Flask 端行为一致。"""
    try:
        from settings import calculate_bmi
        bmi = calculate_bmi(float(weight), user_id=user_id)
        return bmi
    except Exception:
        return None


def _update_profile_weight(conn: sqlite3.Connection, user_id: int, weight: float) -> None:
    """更新 user_profiles 表中的 weight 字段。失败不抛异常，仅静默忽略。"""
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE user_profiles SET weight = ? WHERE user_id = ?",
            (float(weight), user_id),
        )
        conn.commit()
    except Exception:
        pass


def _insert_record(conn: sqlite3.Connection, record: dict) -> int:
    """统一执行 INSERT INTO records，返回新记录 ID。
    与 Flask /batch_add 的 INSERT 字段保持一致。"""
    c = conn.cursor()
    user_id = record.get("user_id", DEFAULT_USER_ID)
    rtype = record.get("type", "")
    value = record.get("value", 0)
    unit = record.get("unit", "mmol/L")
    notes = record.get("notes", "")
    timestamp = record.get("datetime") or record.get("timestamp", "")
    if not timestamp:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if "T" in timestamp:
        timestamp = timestamp.replace("T", " ")
        if len(timestamp) == 16:
            timestamp += ":00"

    systolic = record.get("systolic_pressure") or None
    diastolic = record.get("diastolic_pressure") or None
    weight = record.get("weight")
    bmi = record.get("bmi")
    if weight and not bmi:
        bmi = _calculate_and_set_bmi(conn, user_id, weight)

    c.execute(
        """INSERT INTO records
           (user_id, value, unit, type, notes, timestamp, calories, diet_analysis, is_predicted,
            distance, duration, heart_rate, max_heart_rate, systolic_pressure, diastolic_pressure,
            pulse_rate, weight, bmi, medication_name, steps, pace, max_pace, cadence, vo2max,
            spo2, carbs_grams, gi_value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, value, unit, rtype, notes, timestamp,
            record.get("calories", 0), record.get("diet_analysis", ""),
            1 if record.get("is_predicted", False) else 0,
            record.get("distance"), record.get("duration"),
            record.get("heart_rate"), record.get("max_heart_rate"),
            systolic, diastolic,
            record.get("pulse_rate"), weight, bmi,
            record.get("medication_name"), record.get("steps"),
            record.get("pace"), record.get("max_pace"),
            record.get("cadence"), record.get("vo2max"),
            record.get("spo2"), record.get("carbs_grams"),
            record.get("gi_value"),
        ),
    )
    return c.lastrowid


def _try_regex_parse(text: str) -> list[dict] | None:
    """尝试用正则解析 emoji 标记的多用户健康数据。

    支持格式（与 AI 解析输出格式一致）：
    - 🐯103/69、64，54.20  → 血压 103/69，脉搏 64，体重 54.20
    - 🐰122/68、59，69.90  → 血压 122/68，脉搏 59，体重 69.90

    返回结构化记录列表；无法解析时返回 None（调用方应 fallback 到 AI）。
    """
    from settings import EMOJI_USER_MAP

    if not text:
        return None

    # 构造 emoji 匹配正则
    emoji_pattern = "[" + "".join(re.escape(e) for e in EMOJI_USER_MAP) + "]"
    segments_re = re.compile(
        f"({emoji_pattern})\\s*([^{''.join(re.escape(e) for e in EMOJI_USER_MAP)}]+)"
    )

    matches = list(segments_re.finditer(text))
    if not matches:
        # 没有 emoji → 检查是否纯结构化数字（无 emoji 的 fallback 不支持）
        return None

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results: list[dict] = []

    for m in matches:
        emoji_char = m.group(1)
        segment_text = m.group(2).strip().rstrip("，,、；;")
        user_id = EMOJI_USER_MAP.get(emoji_char)
        if user_id is None:
            continue

        # 匹配血压: 数字/数字
        bp_match = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", segment_text)
        systolic = int(bp_match.group(1)) if bp_match else None
        diastolic = int(bp_match.group(2)) if bp_match else None

        # 移除已匹配的血压文本，避免脉搏/体重误识别
        remaining = segment_text
        if bp_match:
            remaining = remaining.replace(bp_match.group(0), "", 1)

        # 从剩余文本中提取所有数字
        all_numbers = re.findall(r"\d+\.?\d*", remaining)
        floats = [float(n) for n in all_numbers]
        ints = [int(n) for n in all_numbers if "." not in n]
        floats_with_decimal = [float(n) for n in all_numbers if "." in n]

        pulse = None
        weight_val = None

        # 脉搏: 整数，30-220 范围
        for n in ints:
            if 30 <= n <= 220:
                pulse = n
                break

        # 体重: 优先选带小数点的数字（如 54.20），其次排除已识别的脉搏值
        for f in floats_with_decimal:
            if 20.0 <= f <= 300.0:
                weight_val = f
                break
        if weight_val is None:
            for f in floats:
                if 20.0 <= f <= 300.0 and f != pulse:
                    weight_val = f
                    break

        # 组装记录（与 AI parse_glucose_input 输出字段对齐）
        records_for_user: list[dict] = []
        if systolic is not None and diastolic is not None:
            bp_record = {
                "user_id": user_id,
                "type": "血压测量",
                "value": 0,
                "systolic_pressure": systolic,
                "diastolic_pressure": diastolic,
                "datetime": now_str,
            }
            if pulse is not None:
                bp_record["pulse_rate"] = pulse
            records_for_user.append(bp_record)

        if weight_val is not None:
            records_for_user.append({
                "user_id": user_id,
                "type": "体重记录",
                "value": 0,
                "weight": weight_val,
                "datetime": now_str,
            })

        if not records_for_user:
            # 该 emoji 段没有识别到任何数据 → 整体失败，fallback 到 AI
            return None

        results.extend(records_for_user)

    return results if results else None


def _inline_batch_insert(
    conn: sqlite3.Connection,
    records: list[dict],
    conflict_resolution: str = "overwrite",
) -> dict:
    """批量写入记录的内联核心逻辑。返回 {inserted_ids, warnings, duplicates_skipped}。"""
    inserted_ids: list[int] = []
    all_warnings: list[str] = []
    duplicates_skipped: list[str] = []

    for r in records:
        user_id = r.get("user_id", DEFAULT_USER_ID)
        rtype = r.get("type", "")
        timestamp = r.get("datetime") or r.get("timestamp", "")
        if not timestamp:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 数据范围校验（收集警告，不阻止写入）
        warnings = _validate_record_data(r)
        if warnings:
            label = rtype or "未知记录"
            all_warnings.append(f"[{label}] " + "; ".join(warnings))

        # 2. 重复检测
        dup_msg = _check_duplicate(conn, user_id, r)
        if dup_msg:
            if conflict_resolution == "ask":
                # 在 MCP 工具层，ask 策略不常用；视为 skip 并记录
                duplicates_skipped.append(dup_msg)
                continue
            elif conflict_resolution == "skip":
                duplicates_skipped.append(dup_msg)
                continue
            elif conflict_resolution == "overwrite":
                # 删除同分钟旧记录（非预测记录）
                c = conn.cursor()
                c.execute(
                    """DELETE FROM records
                       WHERE user_id = ? AND strftime('%Y-%m-%d %H:%M', timestamp) = strftime('%Y-%m-%d %H:%M', ?)
                       AND is_predicted = 0""",
                    (user_id, timestamp),
                )

        # 3. BMI 计算
        weight = r.get("weight")
        if weight and not r.get("bmi"):
            bmi = _calculate_and_set_bmi(conn, user_id, weight)
            if bmi:
                r["bmi"] = bmi

        # 4. 执行插入
        rid = _insert_record(conn, r)
        r["id"] = rid
        inserted_ids.append(rid)

        # 5. 档案联动更新（体重）
        if weight:
            _update_profile_weight(conn, user_id, weight)

    return {
        "inserted_ids": inserted_ids,
        "warnings": all_warnings,
        "duplicates_skipped": duplicates_skipped,
    }


# ------------------------------------------------------------------
# 写操作 → 走 Flask HTTP API，复用业务逻辑（BMI / 预测关联 / 校验）
# ------------------------------------------------------------------

def _user_label(user_id: int) -> str:
    """返回用户标识（emoji + 名字），用于 MCP 响应展示。"""
    from settings import USER_EMOJI_MAP
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT display_name FROM app_users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    name = row["display_name"] if row else f"用户{user_id}"
    emoji = USER_EMOJI_MAP.get(user_id, "")
    return f"{emoji} {name}".strip()


def _bp_status(systolic: int, diastolic: int) -> str:
    """血压状态评估。"""
    if systolic > 140 or diastolic > 90:
        return "偏高"
    if systolic > 130 or diastolic > 85:
        return "警戒"
    return "正常"


def _validate_bp(systolic: int, diastolic: int) -> str | None:
    """校验血压参数，返回错误信息或 None。"""
    if systolic < 60 or systolic > 250:
        return f"收缩压 {systolic} 超出正常范围（60-250 mmHg）"
    if diastolic < 40 or diastolic > 180:
        return f"舒张压 {diastolic} 超出正常范围（40-180 mmHg）"
    if systolic <= diastolic:
        return f"收缩压（{systolic}）必须大于舒张压（{diastolic}）"
    return None


def _validate_glucose(value: float) -> str | None:
    """校验血糖参数，返回错误信息或 None。"""
    if value < 1.0 or value > 33.3:
        return f"血糖值 {value} 超出正常范围（1.0-33.3 mmol/L）"
    return None


def _validate_weight(weight: float) -> str | None:
    """校验体重参数，返回错误信息或 None。"""
    if weight < 20.0 or weight > 300.0:
        return f"体重 {weight} 超出正常范围（20-300 kg）"
    return None


def _validate_heart_rate(hr: int) -> str | None:
    """校验心率/脉搏参数，返回错误信息或 None。"""
    if hr < 30 or hr > 220:
        return f"心率 {hr} 超出正常范围（30-220 bpm）"
    return None


@mcp.tool()
async def record_blood_pressure(
    user_id: int,
    systolic: int,
    diastolic: int,
    pulse_rate: Optional[int] = None,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """记录一次血压测量。调用前，请向用户展示所有参数并请求确认；仅在用户明确同意后再执行。"""
    err = _validate_bp(systolic, diastolic)
    if err:
        return f"❌ 参数错误：{err}"
    if pulse_rate is not None:
        hr_err = _validate_heart_rate(pulse_rate)
        if hr_err:
            return f"❌ 参数错误：{hr_err}"
    ts = _normalize_timestamp(timestamp)
    payload: dict = {
        "type": "血压测量",
        "value": 0,
        "systolic_pressure": systolic,
        "diastolic_pressure": diastolic,
        "timestamp": ts,
    }
    if pulse_rate is not None:
        payload["pulse_rate"] = pulse_rate
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    dup = _is_dup_error(data)
    if dup:
        return f"⚠️ {dup}"
    rid = data.get("data", {}).get("id", "?")
    label = _user_label(user_id)
    status = _bp_status(systolic, diastolic)
    parts = [f"✅ {label} 血压记录成功"]
    parts.append(f"   {systolic}/{diastolic} mmHg [{status}]")
    if pulse_rate is not None:
        parts.append(f"   脉搏 {pulse_rate} bpm")
    parts.append(f"   时间 {ts} (ID: {rid})")
    return "\n".join(parts)


@mcp.tool()
async def record_weight(
    user_id: int,
    weight: float,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """记录一次体重。会自动计算并更新 BMI。调用前，请向用户展示所有参数并请求确认；仅在用户明确同意后再执行。"""
    err = _validate_weight(weight)
    if err:
        return f"❌ 参数错误：{err}"
    ts = _normalize_timestamp(timestamp)
    payload: dict = {
        "type": "体重记录",
        "weight": weight,
        "timestamp": ts,
    }
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    dup = _is_dup_error(data)
    if dup:
        return f"⚠️ {dup}"
    rid = data.get("data", {}).get("id", "?")
    label = _user_label(user_id)
    bmi = data.get("data", {}).get("bmi", "")
    parts = [f"✅ {label} 体重记录成功"]
    bmi_str = f"，BMI {bmi}" if bmi else ""
    parts.append(f"   {weight} kg{bmi_str}")
    parts.append(f"   时间 {ts} (ID: {rid})")
    return "\n".join(parts)


@mcp.tool()
async def record_glucose(
    user_id: int,
    value: float,
    record_type: str,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """记录一次血糖。record_type 示例：空腹、早餐后2小时、午餐后2小时、晚餐后2小时、睡前。调用前，请向用户展示所有参数并请求确认；仅在用户明确同意后再执行。"""
    err = _validate_glucose(value)
    if err:
        return f"❌ 参数错误：{err}"
    ts = _normalize_timestamp(timestamp)
    payload: dict = {
        "type": record_type,
        "value": value,
        "unit": "mmol/L",
        "timestamp": ts,
    }
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    dup = _is_dup_error(data)
    if dup:
        return f"⚠️ {dup}"
    rid = data.get("data", {}).get("id", "?")
    label = _user_label(user_id)
    from settings import check_glucose_compliance
    result = check_glucose_compliance(value, record_type)
    badge = "达标" if result["is_compliant"] else "未达标"
    parts = [f"✅ {label} 血糖记录成功"]
    parts.append(f"   {value} mmol/L ({record_type}) [{badge}]")
    parts.append(f"   时间 {ts} (ID: {rid})")
    return "\n".join(parts)


@mcp.tool()
async def record_exercise(
    user_id: int,
    exercise_type: str,
    distance: float,
    duration: Optional[str] = None,
    pace: Optional[str] = None,
    heart_rate: Optional[int] = None,
    steps: Optional[int] = None,
    calories: Optional[int] = None,
    notes: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """记录一次运动/锻炼。exercise_type 示例：跑步、走路、骑行、游泳、健身。distance 单位为公里。调用前，请向用户展示所有参数并请求确认；仅在用户明确同意后再执行。"""
    if heart_rate is not None:
        hr_err = _validate_heart_rate(heart_rate)
        if hr_err:
            return f"❌ 参数错误：{hr_err}"
    ts = _normalize_timestamp(timestamp)
    payload: dict = {
        "type": exercise_type,
        "value": 0,
        "distance": distance,
        "timestamp": ts,
    }
    if duration:
        payload["duration"] = duration
    if pace:
        payload["pace"] = pace
    if heart_rate is not None:
        payload["heart_rate"] = heart_rate
    if steps is not None:
        payload["steps"] = steps
    if calories is not None:
        payload["calories"] = calories
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    dup = _is_dup_error(data)
    if dup:
        return f"⚠️ {dup}"
    rid = data.get("data", {}).get("id", "?")
    label = _user_label(user_id)
    parts = [f"✅ {label} 运动记录成功"]
    detail = f"   {exercise_type} {distance}km"
    if duration:
        detail += f"，{duration}"
    if pace:
        detail += f"，配速 {pace}"
    if heart_rate:
        detail += f"，心率 {heart_rate}bpm"
    if steps:
        detail += f"，{steps}步"
    if calories:
        detail += f"，{calories}kcal"
    parts.append(detail)
    parts.append(f"   时间 {ts} (ID: {rid})")
    return "\n".join(parts)


def _has_numeric_data(text: str) -> bool:
    """判断文本是否包含可能被识别为健康数据的数字（血压格式、血糖值、体重等）。"""
    if not text:
        return False
    # 血压格式：123/80
    if re.search(r'\d{2,3}\s*/\s*\d{2,3}', text):
        return True
    # 血糖/体重等数值 + 单位
    if re.search(r'\d+\.?\d*\s*(mmol/L|mg/dL|kg|公斤|kcal|km)', text):
        return True
    # 纯数字序列（如 🐯103/69、64，54.20）
    if re.search(r'\d{2,3}\s*[，,、]\s*\d{2,3}', text):
        return True
    return False


def _format_parsed_preview(records: list[dict]) -> list[str]:
    """将 AI 解析结果格式化为可读预览。"""
    lines: list[str] = []
    for idx, r in enumerate(records, 1):
        parts: list[str] = []
        rtype = r.get('type', '')
        if r.get('systolic_pressure') and r.get('diastolic_pressure'):
            parts.append(f"血压 {r['systolic_pressure']}/{r['diastolic_pressure']}")
        if r.get('pulse_rate'):
            parts.append(f"脉搏 {r['pulse_rate']}")
        if r.get('spo2'):
            parts.append(f"血氧 {r['spo2']}%")
        if r.get('weight'):
            parts.append(f"体重 {r['weight']}kg")
        if r.get('value') and r.get('value') > 0:
            parts.append(f"血糖 {r['value']} mmol/L ({rtype})")
        if r.get('medication_name'):
            parts.append(f"用药 {r['medication_name']}")
        ts = r.get('datetime', '')
        time_str = ts.split(' ')[-1][:5] if ' ' in ts else ts
        line = f"  {idx}. {' / '.join(parts)} @ {time_str}"
        # 高亮异常值
        spo2 = r.get('spo2')
        if spo2 is not None and spo2 < 90:
            line += " ⚠️ 异常血氧值"
        lines.append(line)
    return lines


@mcp.tool()
async def parse_and_record(user_id: int, text: str) -> str:
    """用自然语言描述健康数据，AI 解析后自动入库。示例：\"爸爸空腹血糖 6.2，血压 128/80\"。调用前，请向用户展示解析结果并请求确认；仅在用户明确同意后再执行批量写入。"""
    # Step 1: parse
    data = await _api_post(user_id, "/parse_ai", {"text": text})
    # 诊断：API 返回错误
    if isinstance(data, dict) and data.get("status") == "error":
        return f"❌ 解析失败：{data.get('message', '未知错误')}"
    records = data if isinstance(data, list) else []
    if not records:
        hint = ""
        if _has_numeric_data(text):
            hint = "\n💡 提示：文本中包含数字，但 AI 未识别。建议用精确工具录入，例如 `record_blood_pressure` 或 `record_weight`。"
        return f"未解析到有效记录{hint}"

    # Step 2: 展示预览
    preview = _format_parsed_preview(records)
    preview_text = "\n".join(preview)

    # Step 3: batch insert
    result = await _api_post(
        user_id,
        "/batch_add",
        {"records": records, "conflict_resolution": "overwrite"},
    )
    dup = _is_dup_error(result)
    if dup:
        return f"⚠️ {dup}\n\n解析预览：\n{preview_text}"

    # 收集后端返回的警告
    warnings = ""
    if isinstance(result, dict) and result.get("data", {}).get("warnings"):
        warnings = "\n⚠️ 数据警告：\n" + "\n".join(f"  - {w}" for w in result["data"]["warnings"])

    return f"成功解析并记录 {len(records)} 条数据\n\n解析详情：\n{preview_text}{warnings}\n\n如数据有误，请使用 `undo_last_record` 撤销。"


@mcp.tool()
async def batch_record(user_id: int, records: list[dict]) -> str:
    """直接批量写入已结构化的健康记录，跳过 AI 解析环节。

    records 每项为 dict，支持字段：
    - type（必填）: 记录类型，如"血压测量"、"体重记录"、"空腹"等
    - value: 血糖值（mmol/L）
    - systolic_pressure / diastolic_pressure: 血压
    - pulse_rate: 脉搏
    - weight: 体重（kg）
    - timestamp / datetime: 时间戳（可选，默认当前时间）
    - notes: 备注（可选）

    调用前，请向用户展示所有记录并请求确认；仅在用户明确同意后再执行。
    """
    if not records:
        return "未提供任何记录"

    conn = _db()
    try:
        # 逐条参数校验（前置拦截，与单条工具保持一致）
        param_errors: list[str] = []
        for idx, r in enumerate(records, 1):
            rtype = r.get("type", "")
            if not rtype:
                param_errors.append(f"第 {idx} 条：缺少 type 字段")
                continue
            bp_sys = r.get("systolic_pressure")
            bp_dia = r.get("diastolic_pressure")
            if bp_sys is not None and bp_dia is not None:
                err = _validate_bp(bp_sys, bp_dia)
                if err:
                    param_errors.append(f"第 {idx} 条：{err}")
                pr = r.get("pulse_rate")
                if pr is not None:
                    hr_err = _validate_heart_rate(pr)
                    if hr_err:
                        param_errors.append(f"第 {idx} 条：{hr_err}")
            val = r.get("value")
            if val is not None and val > 0 and not bp_sys and not r.get("weight"):
                g_err = _validate_glucose(val)
                if g_err:
                    param_errors.append(f"第 {idx} 条：{g_err}")
            wt = r.get("weight")
            if wt is not None:
                w_err = _validate_weight(wt)
                if w_err:
                    param_errors.append(f"第 {idx} 条：{w_err}")

        if param_errors:
            return "❌ 参数错误：\n" + "\n".join(f"  - {e}" for e in param_errors)

        # 统一时间戳规范化
        for r in records:
            ts = r.get("datetime") or r.get("timestamp")
            r["datetime"] = _normalize_timestamp(ts)
            if "timestamp" in r:
                del r["timestamp"]
            # 确保 user_id 正确
            r["user_id"] = user_id

        # 内联批量写入
        result = _inline_batch_insert(conn, records, conflict_resolution="overwrite")
        conn.commit()

        inserted = result["inserted_ids"]
        warnings = result["warnings"]
        skipped = result["duplicates_skipped"]

        label = _user_label(user_id)
        lines = [f"✅ {label} 批量记录成功，共写入 {len(inserted)} 条"]
        if skipped:
            lines.append(f"⚠️ 跳过 {len(skipped)} 条重复记录")
        if warnings:
            lines.append("\n⚠️ 数据警告：")
            for w in warnings:
                lines.append(f"  - {w}")

        # 记录详情
        lines.append("")
        for idx, r in enumerate(records, 1):
            rid = inserted[idx - 1] if idx <= len(inserted) else "?"
            rtype = r.get("type", "")
            ts = r.get("datetime", "")
            time_str = ts.split(" ")[-1][:5] if " " in ts else ts

            if r.get("systolic_pressure"):
                status = _bp_status(r["systolic_pressure"], r["diastolic_pressure"])
                detail = f"  血压 {r['systolic_pressure']}/{r['diastolic_pressure']} [{status}]"
                if r.get("pulse_rate"):
                    detail += f"，脉搏 {r['pulse_rate']}bpm"
            elif r.get("weight"):
                bmi_str = ""
                if r.get("bmi"):
                    bmi_str = f"，BMI {r['bmi']}"
                detail = f"  体重 {r['weight']}kg{bmi_str}"
            elif r.get("value") and r.get("value") > 0:
                from settings import check_glucose_compliance
                g_result = check_glucose_compliance(r["value"], rtype)
                badge = "达标" if g_result["is_compliant"] else "未达标"
                detail = f"  血糖 {r['value']} mmol/L ({rtype}) [{badge}]"
            else:
                detail = f"  {rtype}"
            lines.append(f"{idx}. {detail}  ⏰ {time_str} (ID: {rid})")

        lines.append("\n如数据有误，请使用 `undo_last_record` 撤销。")
        return "\n".join(lines)
    except Exception as e:
        conn.rollback()
        return f"❌ 批量写入失败：{type(e).__name__}: {str(e)[:200]}"
    finally:
        conn.close()


@mcp.tool()
async def batch_parse_and_record(text: str) -> str:
    """批量解析含 emoji 标记的多用户健康数据并自动入库。

    支持格式：🐯103/69、64，54.20，🐰122/68、59，69.90
    其中 🐯=妈妈，🐰=爸爸，依次是血压/心率/体重。
    也支持混合自然语言，如"🐰122/68，爸爸空腹 6.2"。
    无 emoji 前缀的文本归默认用户。

    调用前，请向用户展示解析结果并请求确认；仅在用户明确同意后再执行。
    """
    from settings import EMOJI_USER_MAP

    # Step 0: 确定 auth_user_id（环境变量默认值，优先从 emoji 动态推断）
    auth_user_id = DEFAULT_USER_ID
    found_emojis = [e for e in EMOJI_USER_MAP if e in text]
    if len(found_emojis) == 1:
        auth_user_id = EMOJI_USER_MAP[found_emojis[0]]

    # Step 1: Fast path — 正则解析结构化数据（无需 Flask / LLM）
    records = _try_regex_parse(text)
    used_regex = records is not None

    # Step 2: Fallback — AI 解析（需 Flask 后端 + LLM）
    if records is None:
        data = await _api_post(auth_user_id, "/parse_ai", {"text": text})
        if isinstance(data, dict) and data.get("status") == "error":
            return f"❌ 解析失败：{data.get('message', '未知错误')}"
        records = data if isinstance(data, list) else []

    if not records:
        hint = ""
        if _has_numeric_data(text):
            hint = "\n💡 提示：文本中包含数字，但 AI 未识别。建议用精确工具录入，例如 `record_blood_pressure` 或 `record_weight`。"
        return f"未解析到有效记录{hint}"

    # 展示解析预览
    preview = _format_parsed_preview(records)
    preview_text = "\n".join(preview)

    # Step 3: 内联 SQLite 批量写入（替代 /batch_add HTTP，无需 Flask）
    conn = _db()
    try:
        # 统一时间戳规范化
        for r in records:
            ts = r.get("datetime") or r.get("timestamp")
            r["datetime"] = _normalize_timestamp(ts)
            if "timestamp" in r:
                del r["timestamp"]

        result = _inline_batch_insert(conn, records, conflict_resolution="overwrite")
        conn.commit()

        inserted = result["inserted_ids"]
        warnings = result["warnings"]
        skipped = result["duplicates_skipped"]
    except Exception as e:
        conn.rollback()
        conn.close()
        return f"❌ 批量写入失败：{type(e).__name__}: {str(e)[:200]}"
    finally:
        conn.close()

    # Step 4: 按用户分组汇总
    user_records: dict[int, list] = {}
    for r in records:
        uid = r.get('user_id', auth_user_id)
        user_records.setdefault(uid, []).append(r)

    lines = [f"✅ 已成功记录 {len(inserted)} 条数据"]
    if used_regex:
        lines[-1] += "（正则快速解析）"
    if skipped:
        lines.append(f"⚠️ 跳过 {len(skipped)} 条重复记录")
    lines.append("\n解析预览：")
    lines.append(preview_text)
    if warnings:
        lines.append("\n⚠️ 数据警告：")
        for w in warnings:
            lines.append(f"  - {w}")
    lines.append("")
    for uid, recs in user_records.items():
        label = _user_label(uid)
        lines.append(f"{'─' * 30}")
        lines.append(f"📋 {label}")
        lines.append(f"{'─' * 30}")
        for r in recs:
            rtype = r.get('type', '')
            ts = r.get('datetime', '')
            time_str = ts.split(' ')[-1][:5] if ' ' in ts else ts
            if r.get('systolic_pressure'):
                status = _bp_status(r['systolic_pressure'], r['diastolic_pressure'])
                line = f"  血压 {r['systolic_pressure']}/{r['diastolic_pressure']} [{status}]"
                if r.get('pulse_rate'):
                    line += f"，脉搏 {r['pulse_rate']}bpm"
                if r.get('spo2'):
                    line += f"，血氧 {r['spo2']}%"
                lines.append(line)
            if r.get('weight'):
                lines.append(f"  体重 {r['weight']}kg")
            if r.get('value') and r.get('value') > 0:
                from settings import check_glucose_compliance
                g_result = check_glucose_compliance(r['value'], rtype)
                badge = "达标" if g_result["is_compliant"] else "未达标"
                lines.append(f"  血糖 {r['value']} mmol/L ({rtype}) [{badge}]")
            lines.append(f"  ⏰ {time_str}  (ID: {r.get('id', '?')})")
        lines.append("")

    lines.append("如数据有误，请使用 `undo_last_record` 撤销。")
    return "\n".join(lines)


@mcp.tool()
async def undo_last_record(user_id: int) -> str:
    """撤销（删除）该用户最近一次写入的健康记录。调用前，必须向用户展示将要删除的记录详情并请求确认；仅在用户明确同意后再执行。"""
    conn = _db()
    c = conn.cursor()

    # 查出最近一条记录
    c.execute(
        """
        SELECT id, type, value, distance, systolic_pressure, diastolic_pressure,
               pulse_rate, spo2, weight, timestamp, notes
        FROM records
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = c.fetchone()

    if not row:
        conn.close()
        return "该用户没有任何记录可撤销"

    rid = row["id"]
    rtype = row["type"]
    ts = row["timestamp"]

    # 组装记录描述
    desc = f"{ts} | {rtype}"
    if row["systolic_pressure"]:
        desc += f" | 血压 {row['systolic_pressure']}/{row['diastolic_pressure']}"
        if row["pulse_rate"]:
            desc += f" 脉搏{row['pulse_rate']}"
        if row["spo2"]:
            desc += f" 血氧{row['spo2']}%"
    elif row["weight"]:
        desc += f" | 体重 {row['weight']}kg"
    elif row["distance"]:
        desc += f" | 距离 {row['distance']}km"
    else:
        desc += f" | 血糖 {row['value']} mmol/L"
    if row["notes"]:
        desc += f" | 备注: {row['notes']}"

    # 执行删除
    c.execute("DELETE FROM records WHERE id = ?", (rid,))
    conn.commit()
    conn.close()

    return f"已删除记录 (ID: {rid}): {desc}"


@mcp.tool()
async def today_summary(user_id: int) -> str:
    """查看指定用户今天的全部健康数据摘要。用于验证记录是否成功入库。"""
    conn = _db()
    c = conn.cursor()
    c.execute("""
        SELECT id, type, value, systolic_pressure, diastolic_pressure,
               pulse_rate, spo2, weight, bmi, timestamp, notes
        FROM records
        WHERE user_id = ? AND date(timestamp) = date('now')
        ORDER BY timestamp DESC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    label = _user_label(user_id)
    if not rows:
        return f"📋 {label} 今日暂无记录"

    lines = [f"📋 {label} 今日健康数据"]
    lines.append(f"{'─' * 30}")

    for r in rows:
        ts = r["timestamp"]
        time_str = ts.split(" ")[-1][:5] if " " in ts else ts
        rtype = r["type"]

        if r["systolic_pressure"]:
            status = _bp_status(r["systolic_pressure"], r["diastolic_pressure"])
            line = f"  血压 {r['systolic_pressure']}/{r['diastolic_pressure']} [{status}]"
            if r["pulse_rate"]:
                line += f"，脉搏 {r['pulse_rate']}bpm"
            if r["spo2"]:
                line += f"，血氧 {r['spo2']}%"
        elif r["weight"]:
            bmi_str = f"，BMI {r['bmi']}" if r["bmi"] else ""
            line = f"  体重 {r['weight']}kg{bmi_str}"
        elif r["value"] and r["value"] > 0:
            line = f"  血糖 {r['value']} mmol/L ({rtype})"
        else:
            line = f"  {rtype}"

        lines.append(f"{line}  ⏰ {time_str}")

    lines.append(f"{'─' * 30}")
    lines.append(f"共 {len(rows)} 条记录")
    return "\n".join(lines)

@mcp.tool()
async def list_today_records(user_id: int) -> str:
    """列出该用户今天的所有健康记录。"""
    conn = _db()
    c = conn.cursor()
    c.execute(
        """
        SELECT type, value, systolic_pressure, diastolic_pressure,
               pulse_rate, spo2, weight, timestamp, notes
        FROM records
        WHERE user_id = ? AND date(timestamp) = date('now')
        ORDER BY timestamp DESC
        """,
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "今日暂无记录"

    lines: list[str] = []
    for r in rows:
        ts = r["timestamp"]
        rtype = r["type"]
        parts = [f"{ts} | {rtype}"]
        if r["systolic_pressure"]:
            bp = f"血压 {r['systolic_pressure']}/{r['diastolic_pressure']}"
            if r["pulse_rate"]:
                bp += f" 脉搏{r['pulse_rate']}"
            if r["spo2"]:
                bp += f" 血氧{r['spo2']}%"
            parts.append(bp)
        elif r["weight"]:
            parts.append(f"体重 {r['weight']}kg")
        else:
            parts.append(f"血糖 {r['value']} mmol/L")
        if r["notes"]:
            parts.append(f"备注: {r['notes']}")
        lines.append(" | ".join(parts))

    return "\n".join(lines)


@mcp.tool()
async def get_user_info(user_id: int) -> str:
    """获取用户基本信息（身高、体重、性别等）。"""
    conn = _db()
    c = conn.cursor()
    c.execute(
        """
        SELECT u.username, u.display_name, p.name, p.birth_year,
               p.height, p.weight, p.gender
        FROM app_users u
        LEFT JOIN user_profiles p ON u.id = p.user_id
        WHERE u.id = ?
        """,
        (user_id,),
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return f"用户 {user_id} 不存在"

    return (
        f"用户名: {row['username']} ({row['display_name']})\n"
        f"姓名: {row['name']}\n"
        f"性别: {row['gender']}\n"
        f"身高: {row['height']}cm\n"
        f"体重: {row['weight']}kg\n"
        f"出生年份: {row['birth_year']}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sugar Bee MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="通信协议：stdio（Claude Desktop）或 sse（通用 MCP 客户端）",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)
