# Sugar Bee MCP 接入指南

本文档说明如何通过 API 或 MCP Server 让 Agent 绕过前端直接记录健康数据。

MCP 相关代码已集中到 `mcp_adapter/` 目录：

```
mcp_adapter/
├── core/                    # 两个 server 共享：类型/阈值、时间口径、纯计算
│   ├── domain.py
│   ├── timefmt.py
│   └── analytics.py
├── servers/
│   ├── writer.py            # 写服务：11 个工具（记录/撤销/当日核对）
│   └── reader.py            # 只读分析：12 个 sbhealth_* 工具
└── tests/                   # 234 个用例
```

入口（都是薄壳，不含逻辑）：

| 入口 | 指向 | 用途 |
|---|---|---|
| `mcp_writer.py`（根目录） | `servers/writer.py` | 写服务，Octop `sugarwrite` / Claude Desktop 指向它 |
| `mcp_reader.py`（根目录） | `servers/reader.py` | 只读分析，Octop `sbhealth` 指向它 |
| `uv run python -m mcp_adapter.servers.writer` | — | 模块方式启动写服务 |
| `uv run python -m mcp_adapter.servers.reader` | — | 模块方式启动读服务 |

> 两个入口**本地和服务器同名同层**：仓库根 `/` 与 `/root/health_tools/` 下都叫
> `mcp_writer.py` / `mcp_reader.py`，部署时不用换名字，也不会认错哪个是哪个。
> 历史名字 `mcp_server.py`、`scripts/sugar_bee_mcp.py` 已废弃。

> 为什么不直接用 `mcp/` 作为目录名？因为项目依赖了官方的 `mcp` Python 包，若本地目录也叫 `mcp` 会产生命名冲突，导致 `from mcp.server.fastmcp import FastMCP` 解析失败。因此使用 `mcp_adapter/` 避免冲突。

---

## 改动概览

1. **Token 鉴权**：Flask 新增 `X-Agent-Token` + `X-User-Id` 双头鉴权，三个写接口已开放：
   - `POST /add` — 单条写入
   - `POST /parse_ai` — 自然语言解析
   - `POST /batch_add` — 批量写入
2. **MCP Server**：`mcp_adapter/servers/writer.py` 支持 **stdio**（Claude Desktop）与 **sse**（任意 MCP 客户端）双模式。

---

## 用户映射

| 用户 | user_id | 常用称呼 |
|---|---|---|
| 爸爸 / 愚群 | `1` | `爸爸`、`愚群`、`🐰` |
| 妈妈 / 金虎 | `6` | `妈妈`、`金虎`、`🐯` |

---

## 方式一：HTTP API 直接调用（最通用）

任何能发 HTTP 请求的 agent / 脚本 / CLI 都可用此方式，不依赖 MCP 协议。

### 前置条件

`.env` 中已写入 `AGENT_API_TOKEN`，Flask 在 5001 端口运行。

### 1. 记录血压

```bash
TOKEN=$(grep AGENT_API_TOKEN .env | cut -d= -f2)

curl -sS -X POST http://127.0.0.1:5001/add \
  -H "X-Agent-Token: $TOKEN" \
  -H "X-User-Id: 6" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "血压测量",
    "value": 0,
    "systolic_pressure": 104,
    "diastolic_pressure": 60,
    "pulse_rate": 64,
    "timestamp": "2026-05-02 06:30:00",
    "notes": "早上空腹血压"
  }'
```

### 2. 记录体重

```bash
curl -sS -X POST http://127.0.0.1:5001/add \
  -H "X-Agent-Token: $TOKEN" \
  -H "X-User-Id: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "体重记录",
    "weight": 72.5,
    "timestamp": "2026-05-02 06:30:00"
  }'
```

### 3. 记录血糖

```bash
curl -sS -X POST http://127.0.0.1:5001/add \
  -H "X-Agent-Token: $TOKEN" \
  -H "X-User-Id: 1" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "空腹",
    "value": 6.2,
    "unit": "mmol/L",
    "timestamp": "2026-05-02 07:30:00"
  }'
```

### 4. 自然语言解析并入库

```bash
curl -sS -X POST http://127.0.0.1:5001/parse_ai \
  -H "X-Agent-Token: $TOKEN" \
  -H "X-User-Id: 6" \
  -H "Content-Type: application/json" \
  -d '{"text":"妈妈早上 6:30 空腹血压 104/60 脉搏 64"}'
```

返回结构化数组后，再用 `/batch_add` 写入：

```bash
curl -sS -X POST http://127.0.0.1:5001/batch_add \
  -H "X-Agent-Token: $TOKEN" \
  -H "X-User-Id: 6" \
  -H "Content-Type: application/json" \
  -d '{
    "records": [ { ... } ],
    "conflict_resolution": "overwrite"
  }'
```

### 常见 record_type 值

- 血糖：`空腹`、`早餐后2小时`、`午餐后2小时`、`晚餐后2小时`、`睡前`
- 血压：`血压测量`
- 体重：`体重记录`

---

## 方式二：MCP Server（协议级集成）

`mcp_adapter/servers/writer.py` 将功能封装为 MCP 工具，支持两种传输模式。

### 模式 A：stdio（Claude Desktop 专用）

```bash
uv run python mcp_writer.py
# 或显式指定
uv run python mcp_writer.py --transport stdio
```

配置 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "sugar-bee": {
      "command": "uv",
      "args": ["run", "python", "mcp_writer.py", "--transport", "stdio"],
      "env": {
        "AGENT_API_TOKEN": "你的token值"
      }
    }
  }
}
```

### 模式 B：sse（任意 MCP 客户端 / Hermes / Google CLI）

```bash
export AGENT_API_TOKEN="你的token值"
export FASTMCP_PORT=3001   # 默认 8000，可自选
uv run python mcp_writer.py --transport sse
```

启动后，任意 MCP 客户端连接 `http://127.0.0.1:3001/sse` 即可调用工具。

> 若 Flask 不在 `127.0.0.1:5001`，加 `API_BASE=http://ip:端口`。

### 可用工具（写入 / 核对）

| 工具名 | 用途 | 示例问法 |
|---|---|---|
| `record_blood_pressure` | 记录血压（收缩压/舒张压/脉搏/血氧） | "帮妈妈记录血压 110/70，脉搏 72" |
| `record_weight` | 记录体重（自动算 BMI） | "记录爸爸体重 73.2kg" |
| `record_glucose` | 记录血糖（按测量类型判定达标） | "记录爸爸空腹血糖 6.0" |
| `record_exercise` | 记录运动（距离/时长/配速/心率） | "记一下今天跑步 5 公里 32 分钟" |
| `parse_and_record` | 自然语言 → AI 解析 → 入库 | "爸爸早上空腹血糖 5.8，血压 120/80" |
| `batch_record` | 批量写入已结构化记录（跳过 AI 解析） | 由上层 agent 组装 records 后调用 |
| `batch_parse_and_record` | emoji 多用户批量解析入库 | "🐯103/69、64，54.20" |
| `undo_last_record` | 撤销该用户最近一条记录 | "刚才那条记错了，删掉" |
| `today_summary` | 今日数据摘要（核对入库结果） | "爸爸今天记了哪些？" |
| `list_today_records` | 列出今日所有记录 | "今天妈妈有什么记录？" |
| `get_user_info` | 查看用户信息 | "妈妈身高体重是多少？" |

> 另有只读分析 server（`mcp_adapter/servers/reader.py`，12 个 `sbhealth_*` 工具，
> 血糖统计/趋势/相关性/异常扫描/CSV 导出），面向远端分析 agent，见
> [`docs/mcp-architecture-review.md`](../docs/mcp-architecture-review.md)。

### 共享核心

两个 server 的类型清单、阈值与时间口径统一在 `mcp_adapter/core/`：

| 模块 | 职责 |
|---|---|
| `core/domain.py` | 记录类型、TIR/低血糖/高血糖/血压阈值（与 `settings.py` 由单测守护一致） |
| `core/timefmt.py` | 时间戳解析/展示/回溯窗口（默认本地墙钟，`SB_MCP_TS_MODE=utc` 可切） |
| `core/analytics.py` | TIR、斜率、前后半段对比、运动分层等纯计算 |

### 在 Python 脚本中直接调用

```python
import asyncio
from mcp_adapter.servers.writer import record_blood_pressure, list_today_records

async def main():
    # 写
    result = await record_blood_pressure(
        user_id=6, systolic=110, diastolic=70, pulse_rate=72
    )
    print(result)

    # 读
    print(await list_today_records(6))

asyncio.run(main())
```

---

## 环境变量速查

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AGENT_API_TOKEN` | — | **必需**。鉴权令牌，在 `.env` 中。 |
| `API_BASE` | `http://127.0.0.1:5001` | Flask 服务地址。 |
| `DB_PATH` | `./glucose.db` | SQLite 路径（仅 MCP 读工具使用）。 |
| `FASTMCP_PORT` | `8000` | SSE 模式监听端口。 |

---

## 常见问题

**Q: 端口 5001 被占用？**
```bash
lsof -ti:5001 | xargs kill   # 杀掉占用进程
uv run python app.py           # 重新启动
```

**Q: curl 返回 401？**
- 检查 `X-Agent-Token` 是否与 `.env` 中一致
- 检查 Flask 是否已重启（改 `.env` 后需重启）

**Q: MCP Server 启动报错 `ModuleNotFoundError`？**
```bash
uv pip install mcp httpx
```

**Q: 为什么本地包不叫 `mcp`？**
- 因为依赖的官方库就是 `mcp`，本地目录若同名会覆盖它，导致 `from mcp.server.fastmcp import FastMCP` 失败。统一使用 `mcp_adapter` 作为本地包名。
