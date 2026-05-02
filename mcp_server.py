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
import sqlite3
from datetime import datetime
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "glucose.db"))
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:5001")
AGENT_API_TOKEN = os.environ.get("AGENT_API_TOKEN", "")

mcp = FastMCP("sugar-bee")


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
    """向 Flask API 发 POST，返回 JSON。"""
    async with httpx.AsyncClient(trust_env=False) as client:
        r = await client.post(
            f"{API_BASE}{endpoint}",
            headers=_api_headers(user_id),
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()


# ------------------------------------------------------------------
# 写操作 → 走 Flask HTTP API，复用业务逻辑（BMI / 预测关联 / 校验）
# ------------------------------------------------------------------

@mcp.tool()
async def record_blood_pressure(
    user_id: int,
    systolic: int,
    diastolic: int,
    pulse_rate: Optional[int] = None,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """记录一次血压测量。"""
    payload: dict = {
        "type": "血压测量",
        "value": 0,
        "systolic_pressure": systolic,
        "diastolic_pressure": diastolic,
        "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if pulse_rate is not None:
        payload["pulse_rate"] = pulse_rate
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    rid = data.get("data", {}).get("id", "?")
    return f"血压记录成功 (ID: {rid})"


@mcp.tool()
async def record_weight(
    user_id: int,
    weight: float,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """记录一次体重。会自动计算并更新 BMI。"""
    payload: dict = {
        "type": "体重记录",
        "weight": weight,
        "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    rid = data.get("data", {}).get("id", "?")
    return f"体重记录成功 (ID: {rid})"


@mcp.tool()
async def record_glucose(
    user_id: int,
    value: float,
    record_type: str,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """记录一次血糖。record_type 示例：空腹、早餐后2小时、午餐后2小时、晚餐后2小时、睡前。"""
    payload: dict = {
        "type": record_type,
        "value": value,
        "unit": "mmol/L",
        "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if notes:
        payload["notes"] = notes

    data = await _api_post(user_id, "/add", payload)
    rid = data.get("data", {}).get("id", "?")
    return f"血糖记录成功 (ID: {rid})"


@mcp.tool()
async def parse_and_record(user_id: int, text: str) -> str:
    """用自然语言描述健康数据，AI 解析后自动入库。示例：\"爸爸空腹血糖 6.2，血压 128/80\""""
    # Step 1: parse
    data = await _api_post(user_id, "/parse_ai", {"text": text})
    records = data if isinstance(data, list) else []
    if not records:
        return "未解析到有效记录"

    # Step 2: batch insert
    await _api_post(
        user_id,
        "/batch_add",
        {"records": records, "conflict_resolution": "overwrite"},
    )
    return f"成功解析并记录 {len(records)} 条数据"


# ------------------------------------------------------------------
# 读操作 → 直连 SQLite（Flask 暂无列表型读接口）
# ------------------------------------------------------------------

@mcp.tool()
async def list_today_records(user_id: int) -> str:
    """列出该用户今天的所有健康记录。"""
    conn = _db()
    c = conn.cursor()
    c.execute(
        """
        SELECT type, value, systolic_pressure, diastolic_pressure,
               pulse_rate, weight, timestamp, notes
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
