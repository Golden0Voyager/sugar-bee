# MCP 层梳理：两个 server 要不要合并

> 结论先行：**不要合并成一个 server 进程，要合并成一个代码包 + 两个入口。**
> 本文记录判断依据、已落地的改动、以及后续阶段的分工。

## 1. 现状盘点

项目里有两个自定义 MCP server，服务于完全不同的调用方：

| 维度 | `mcp_adapter/servers/writer.py`（写服务） | `mcp_adapter/servers/reader.py`（读服务） |
|---|---|---|
| 工具数 | 11 | 12 |
| 能力 | 8 写 + 3 读 | 12 读（含分析与导出） |
| 传输 | stdio + sse | stdio |
| 运行位置 | 本机（与 Flask 同机）+ 云端 `/root/health_tools` | 云端 `/root/health_tools`，DB 在 `/opt/sugar_bee/app/db_data` |
| 数据访问 | Flask HTTP（解析/单条写入）+ 直连 DB（批量） | 只读 SQLite（`file:...?mode=ro`） |
| 用户模型 | 每个工具显式传 `user_id` | 固定 `SB_MCP_USER_ID`（默认 1） |
| 返回形态 | 中文文本（给聊天展示） | 结构化 `dict`（给 agent 二次计算） |
| 依赖 | `utils.db`、`httpx`、Flask 在跑 | 仅 sqlite3 + mcp + `utils.timezone` |

两者共用一个 `records` 表，但**运行边界、信任级别、故障域都不同**。

## 2. 为什么不合并成一个 server

### 2.1 只读是结构性保证，不该退化成"代码自觉"

读服务用 `sqlite3.connect("file:...?mode=ro", uri=True)` 打开数据库 —— 即使代码写错也
写不进去。合并后，远端分析 agent 会同时拿到 `batch_record`、`undo_last_record`
这类破坏性工具，只读只能靠"它不调用"来保证。这是安全上的实质降级。

MCP 协议本身没有按连接裁剪工具集的标准做法，FastMCP 也不支持"同一 server 按传输
方式暴露不同工具"。要做只能自己包一层，复杂度远高于两个入口。

### 2.2 部署拓扑不同

- 写服务需要：Flask 在 `127.0.0.1:5001`、`AGENT_API_TOKEN`、仓库源码。
- 读服务需要：容器内 `/opt/...` 的 DB 路径、`/root` 虚拟根、导出目录白名单。

合并后必然出现大量 `if 环境 == 容器` 分支，比现在更难维护。

### 2.3 故障域不同

写服务的 `parse_and_record` 依赖 Flask；读服务必须在 Flask 挂掉时照常出报表。
合并会让"分析"跟着"写入"一起不可用。

### 2.4 工具数量与选择准确率

23 个工具塞进一个 server，客户端每次都要在 23 个候选里挑，工具误选率明显上升。
现在 11 / 12 的切分已经接近"单 server 可维护"的上限。

### 2.5 返回契约不同

写服务返回中文段落（给人看），读服务返回 dict（给 agent 算）。合并要么牺牲机器可读性，
要么牺牲聊天可读性。

## 3. 该合并的是代码，不是进程

已经落地：抽出 `mcp_adapter/core/`，两个 server 都依赖它，但各自保留入口。

```
mcp_adapter/
├── core/
│   ├── domain.py      # 记录类型、TIR/低血糖/高血糖/血压阈值（单一事实源）
│   ├── timefmt.py     # 时间戳解析、展示、回溯窗口、重复检测窗口
│   └── analytics.py   # TIR、斜率、前后半段、运动分层等纯函数
├── servers/
│   ├── writer.py      # 写服务（11 工具）：记录/撤销/当日核对
│   └── reader.py      # 只读分析（12 工具）：sbhealth_*
└── tests/             # 234 个用例

mcp_writer.py              # 入口 → servers.writer（Octop sugarwrite / Claude Desktop）
mcp_reader.py              # 入口 → servers.reader（Octop sbhealth）
```

两个入口都部署在服务器的**同一个目录** `/root/health_tools/` 下，见第 6 节。

### 3.1 命名约定（2026-10-20 统一）

入口文件名和包内模块**一一对应**，本地和服务器**同名同层**：

| 入口文件 | 指向 | 谁在用 |
|---|---|---|
| `mcp_writer.py` | `servers/writer.py`（11 工具，写入） | Octop `sugarwrite`、Claude Desktop |
| `mcp_reader.py` | `servers/reader.py`（12 工具，只读） | Octop `sbhealth` |

旧的 `mcp_server.py`（太泛，项目里有两个 server）和 `scripts/sugar_bee_mcp.py`
（用项目名当区分，但另一个文件也是 sugar_bee 的）已删除，**没有保留别名**——
留别名等于把歧义继续摆在目录里，而全部引用点（2 个 connector、部署脚本、
`pyproject.toml` 的 coverage omit、4 份文档）都已一次改完。

薄壳只负责 `sys.path` 引导和 `main()` 转发，不含业务逻辑；改包内部结构时入口不用动。

设计约束：`core/` **不 import settings、不 import Flask**，只依赖标准库和
`utils/timezone.py`，保证读服务可以独立部署。与 `settings.py` 的一致性由
`mcp_adapter/tests/test_core.py::TestDomainParity` 断言，两边数值一旦漂移 CI 会红。

## 4. 本次修复的真实缺陷

### 4.1 overwrite 会误删同分钟的其它维度记录（数据丢失）

`_inline_batch_insert` 的 overwrite 分支原来只按 `user_id + 分钟` 删除：

```sql
DELETE FROM records WHERE user_id = ? AND 分钟相同 AND is_predicted = 0
```

场景：08:00 已有一条血糖，用户 08:00 补录体重（体重值相同触发重复判定），
删除语句会把 08:00 的血糖一起删掉。

修复：新增 `_overwrite_scope()`，按记录种类收窄（血压 / 体重 / 用药 / 血糖+类型），
与 `_check_duplicate` 的判定口径一致。回归用例：
`tests/test_mcp_fixes.py::TestOverwriteScope`。

### 4.2 PostgreSQL 模式下会直接报错的 SQL

`_db()` 在 `SUGAR_BEE_DATABASE_URL` 存在时返回 PostgreSQL 连接，但三处 SQL 用了
SQLite 专有函数：

| 位置 | 原写法 | 问题 |
|---|---|---|
| `_check_duplicate`（血压/体重） | `timestamp BETWEEN datetime(?, '-3 minutes') AND ...` | PG 无 `datetime()` |
| `_check_duplicate`（血糖） | `date(timestamp) = date(?)` | PG 的 `date()` 语义不同 |
| `today_summary` / `list_today_records` | `date(timestamp) = date('now','localtime')` | PG 无 `'localtime'` 修饰符 |

修复：边界值在 Python 侧算好（`core.timefmt.shift_minutes` / `day_bounds`），SQL 只做
`timestamp >= ? AND timestamp < ?`。两种方言都能跑，且能命中 `timestamp` 索引。

### 4.3 读服务的时间口径与全项目冲突（整体 +8 小时）

读服务原来假定库里存 UTC（原文件 `scripts/sugar_bee_mcp.py`，现 `servers/reader.py`）：

- `_fmt_ts()` 一律 `+ timedelta(hours=8)`
- `_cutoff()` 用 `datetime.now(timezone.utc)`
- 4 处 SQL 用 `strftime(..., '+8 hours')` / `date(..., '+8 hours')`

但仓库的约定是**存本地墙钟时间**：

- `utils/timezone.py` 首行注释：`Records are stored as naive local wall-clock timestamps.`
- `utils/sql_dialect.now_sql()` 的 SQLite 分支是 `datetime('now', 'localtime')`
- 本地库小时分布：07 点 1537 条、08 点 1007 条，明显是早餐前后的测量峰值（若存 UTC，
  峰值应出现在 23-00 点）

后果：读服务展示的时间整体前移 8 小时，按日/周聚合会把跨零点的记录归错日期。

修复：`core.timefmt` 默认 `local`（不平移）；确实存 UTC 的部署用
`SB_MCP_TS_MODE=utc` 显式切换，不再靠猜。

### 4.4 阈值与类型清单散落

原来 `'CGM','空腹','餐后'...` 这类元组在读服务里重复 6 次，
`3.9 / 10.0 / 13.9 / 140 / 90 / 2-5 点` 各自硬编码。现在统一到 `core/domain.py`，
并加了一条 `test_tir_range_matches_settings` 守护与 `settings.GLUCOSE_TARGETS` 的一致性。

## 5. 还需要做什么

### Phase 2：消除与 Flask 的重复业务逻辑（建议下一步）

`mcp_adapter/servers/writer.py` 里有一段注释写着"以下函数对照 Flask `routes/api_records.py`
实现，确保行为一致" —— 这是最脆的一处：**同一套校验/去重/BMI/档案联动写了两遍**。
任何一边改规则，另一边就悄悄不一致。

建议：

1. 抽 `services/record_service.py`：`validate_record()`、`find_duplicate()`、
   `insert_record()`、`sync_profile_weight()`。
2. `routes/api_records.py` 与 `mcp_adapter/servers/writer.py` 都调用它。
3. `batch_record` / `batch_parse_and_record` 改为走 Flask `/batch_add`（与
   `parse_and_record` 一致），彻底去掉内联写库路径 —— 顺带拿回预测关联、
   `external_id`、事务边界这些只在 Flask 侧实现的逻辑。

### Phase 3：契约与命名统一

| 项 | 现状 | 建议 |
|---|---|---|
| 命名 | `record_*` / `today_*` / `sbhealth_*` | 读：`get_*` / `list_*` / `analyze_*` / `export_*`；`sbhealth_` 前缀冗余（server 名已经隔离） |
| 返回 | 文本 或 dict | 统一 `{"ok": true, ...}` + `display` 字段（机器可读 + 人可读） |
| 错误 | `"❌ 参数错误：..."` | `{"ok": false, "code": "...", "message": "...", "hint": "..."}` |
| 标注 | 无 | MCP `ToolAnnotations`：读工具 `readOnlyHint=True`，`undo_last_record` `destructiveHint=True` |
| 用户 | 读服务固定 user 1 | 显式 `user_id` + `list_users` 工具 |
| 测试 | 读服务 0 覆盖 | 补 12 个工具的集成测试（临时 SQLite + `SB_MCP_DB`） |

工具改名会破坏现有 Octop / Claude Desktop 的提示词，需要保留一版 alias 过渡。

## 6. 部署：两个 server 放在同一个目录

线上（Octop connector）是 **stdio 按会话拉起**，所以“部署”= 把文件放到某个路径，
再让 connector 指向那个路径。现在两个服务都住在 `/root/health_tools/`：

```
/root/health_tools/
├── mcp_reader.py        门牌 → mcp_adapter.servers.reader（12 工具，只读）
├── mcp_writer.py        门牌 → mcp_adapter.servers.writer（11 工具，写入）
├── mcp_adapter/
│   ├── core/            domain / timefmt / analytics（两边共用）
│   └── servers/         reader.py / writer.py
├── utils/               timezone.py / db.py / sql_dialect.py
├── settings.py          纯常量模块，writer 的血糖达标判定 / emoji 映射依赖它
├── core/config.py       utils/db.py 依赖它
└── pylibs/              第三方依赖（flask 等，历史遗留）
```

Octop 侧两条 connector：

| connector | args | 关键 env |
|---|---|---|
| `sbhealth` | `/root/health_tools/mcp_reader.py` | — |
| `sugarwrite` | `/root/health_tools/mcp_writer.py` | `DB_PATH=/opt/sugar_bee/app/db_data/glucose.db`、`API_BASE=http://127.0.0.1:5000`、`PYTHONPATH=/root/health_tools/pylibs` |

发布统一走脚本：

```bash
./scripts/deploy_mcp.sh --dry-run          # 先看会传什么
./scripts/deploy_mcp.sh                    # 发布两个 server + 远端自检
./scripts/deploy_mcp.sh --restart          # 改了 connector 路径后需要
./scripts/deploy_mcp.sh --only reader      # 只发只读服务
```

脚本做四件事：本地语法预检（含 `settings.py`）→ 远端备份旧入口为 `.bak_<时间戳>` →
同步 `mcp_adapter/`、`utils/` 三个文件、`core/config.py`、`settings.py`、两个入口薄壳 →
用 `/root/.octop/venv/bin/python` 在服务器上真跑两个入口。

远端自检必须覆盖**“同步过的模块真的能 import”**，而不只是入口能加载：

```json
{"reader": {"entry": "...", "tools": 12, "db": "..."},
 "writer": {"entry": "...", "tools": 11, "db": "...",
            "user_info_ok": true, "settings_ok": true,
            "compliance_ok": true, "summary_ok": true}}
```

- `settings_ok` ← `settings.EMOJI_USER_MAP` 非空
- `compliance_ok` ← `settings.check_glucose_compliance(6.0, '空腹')` 有返回
- `summary_ok` ← `today_summary` 返回里没有 `Error executing tool`

> 这三项是 2026-10-20 补的。当时漏同步 `settings.py`，云端写入链路大半是坏的
> （见 6.1），而自检只看 `get_user_info` 恰好是通的，所以没发现。

薄壳会向上查找“包含 `mcp_adapter/servers/*.py` 的目录”加入 `sys.path`，
仓库布局（`scripts/` → 仓库根）和服务器布局（`health_tools/` 同级）都能工作。

### 6.1 为什么把 writer 从 `/opt/sugar_bee/app` 搬出来

原来 `sugarwrite` 指向 `/opt/sugar_bee/app/mcp_server.py`（Flask 源码目录）。两个问题：

1. **路径耦合**：`git pull` 会连带改变 MCP 的入口文件，容易出“只拷了 mcp_server.py、
   没拷包”的事故。
2. **真 bug**：`_db()` 在 SQLite 模式下打开 `PROJECT_ROOT/glucose.db`。在服务器上
   `PROJECT_ROOT=/opt/sugar_bee/app`，而 **`/opt/sugar_bee/app/glucose.db` 并不存在**
   —— `sqlite3.connect()` 会静默新建空库。于是 `batch_record`、
   `batch_parse_and_record`、`undo_last_record`、`today_summary`、`list_today_records`、
   `get_user_info` 这 6 个工具全都在读一个空库（报 `no such table: records`）。

搬到 `health_tools` 并在 connector 里显式设 `DB_PATH` 后，两边读写同一个
`/opt/sugar_bee/app/db_data/glucose.db`，问题消失。

**但搬迁留下了一个回归**：`writer.py` 有 5 处 `from settings import ...`
（`check_glucose_compliance`、`EMOJI_USER_MAP`、`USER_EMOJI_MAP`），
而 `settings.py` 从来没在同步清单里。结果 `record_glucose`、`parse_and_record`、
`batch_record`、`batch_parse_and_record`、`today_summary`、`list_today_records`
报 `No module named 'settings'` —— **基本是全部写入能力**。

`get_user_info` 恰好不碰 `settings`，所以当时那条“真读一次库”的自检是通过的。
教训：自检要覆盖**不同的依赖分支**，不能只挑一条能跑通的路径。

已把 `settings.py` 加入同步清单。它是纯常量模块，只 `from utils.timezone import now`
（纯标准库实现），不 import Flask，可以安全独立部署。

### 6.2 实际发布记录

2026-10-20 发布，远端自检输出：

```json
{"reader": {"entry": "mcp_adapter.servers.reader", "tools": 12,
            "db": "/opt/sugar_bee/app/db_data/glucose.db"},
 "writer": {"entry": "mcp_adapter.servers.writer", "tools": 11,
            "db": "/opt/sugar_bee/app/db_data/glucose.db", "user_info_ok": true,
            "settings_ok": true, "compliance_ok": true, "summary_ok": true}}
```

用 Octop 的原样命令做 MCP 握手 + `tools/call get_user_info`，返回真实档案
（`yzq0535 / 愚群 / 170cm / 68.7kg`），证明写服务已经读到正确的库。

线上数据抽样（`sbhealth_overview(7)`）：睡前记录显示 `2026-10-20 22:00`、体重
`07:15`，符合本地墙钟；若仍按旧的 `+8h` 口径，睡前会显示成次日 `06:00`。

回滚：

```bash
# 读服务：旧文件是 33KB 独立快照，不依赖包
ssh lighthouse_sh 'cd /root/health_tools && cp sugar_bee_mcp.py.bak_20261020_100300 sugar_bee_mcp.py'
# 写服务：connector 配置整体备份在服务器上
ssh lighthouse_sh 'ls /root/health_tools/octop_connectors_backup_*.json'
```

### 6.3 connector 改动的两个坑

1. **`put_custom_servers()` 会丢掉 `allowed_tools`**。它内部走
   `validate_servers_map()` → `normalize_server_spec()`，只保留白名单字段，
   `allowed_tools` 不在其中，写一次就没了。要保留只能绕过校验，直接
   `svc.encrypt_and_store(instance_id=..., payload={"servers": 完整字典})`。
2. **它写的是“整份自定义 server 字典”**，不是单个 server。只传 `{"sugarwrite": ...}`
   会把 `sbhealth`、`garmin` 一起删掉。正确做法是先读全量、改一项、整体写回。

### 6.4 什么时候需要重启 Octop

stdio 子进程按会话拉起，工具名与签名没变时不用重启。但**改了 connector 的
`args`/`env`** 就必须重启，否则进程内缓存的还是旧配置：

```bash
systemctl restart octop   # 启动约 20 秒，之后 curl 127.0.0.1:8088 应为 200
```

## 6.5 Flask 容器：改 env 只能重建，`.env` 不能有行内注释

`AGENT_API_TOKEN` 是 MCP 进程调 Flask 时带的共享密钥，`utils/auth.py::login_or_token_required`
校验它，只保护 `/add`、`/parse_ai`、`/batch_add` 三个写接口。
读接口不需要 token —— 因为没有写入权限，token 的意义是“证明这是 Octop 转来的”。

### 容器 env 是在创建时烘焙的

线上 Flask 跑在容器 `sugar-bee` 里，**不是 compose 起的**（`/opt/sugar_bee/` 下没有
yml），而是 `docker run`：`--env-file /opt/sugar_bee/app/.env` + 若干 `-e`。
`.env` **没有挂载进容器**，所以：

```
docker restart sugar-bee     # 不会重新读 .env
```

想改 env 只能 `docker stop` + 重新 `docker run`。原来这套参数只存在于容器运行时，
服务器上没有任何地方记录 —— 容器一旦丢失就无法原样重建。

已固化成 `/opt/sugar_bee/run_container.sh`（0700，支持 `--dry-run`，
健康检查失败自动回滚到 `sugar-bee-prev`）。同时把原先散在 `-e` 里的 6 个变量
（`FLASK_ENV`、`GUNICORN_LOG_LEVEL`、`GUNICORN_TIMEOUT`、`SUGAR_BEE_DB_PATH`、
`TZ`、`SESSION_COOKIE_SECURE`）并入 `.env`，让它成为唯一事实来源。

### `.env` 不能写行内注释

`GARMIN_SYNC_INTERVAL=7200    # 可选，默认 2 小时`

`docker run --env-file` **不剥离行内注释**（只跳过 `#` 开头的整行），
整串会被原样塞进 `int()`，gunicorn worker 直接 `ValueError` 起不来。
本地 `.env` 也有同样的两行。已清理，注释移到上一行。

### 本地与云端不是“共用一个 token”

两边 `.env` 独立、无任何同步机制，本地 `API_BASE` 默认 `127.0.0.1:5001`，
云端容器 `:5000`，数据库也是两份。只是当初抄了同一个值。

轮换时可以分开做，成本差别很大：

| | 要改几处 | 具体位置 |
|---|---|---|
| 本地 | 1 处 | 项目根 `.env`（Flask 校验方和 `mcp_writer.py` 读同一个文件） |
| 云端 | 2 处，必须同时 | ① `/opt/sugar_bee/app/.env`（Flask 侧）② Octop connector blob 的 `servers.sugarwrite.env.AGENT_API_TOKEN`（MCP 侧） |

**漏改云端的第 ② 处 = 云端写入立刻全挂**（Flask 换了新密钥，MCP 还在拿旧的）。

安全性上，因为两边的值曾经相同，**泄漏进 opencode.db 的那一份等于两边都泄漏**。
所以不要保持相同 —— 换成两个不同的值，之后本地泄漏不牵连云端。

### 验证 token 的零副作用手法

`POST /add` 带 token + `X-User-Id: 999999`（不存在的用户）：
装饰器 `utils/auth.py` 会先查用户是否存在，不存在直接返回 404，
**根本不进入写入函数**。所以 `404` = token 有效，`401` = token 无效。

> 不要用空 JSON `{}` 探活。`/add` 对空 body 不做校验，会插进一条
> `type=None, value=0.0` 的脏记录（已实际踩过，id=36686，只能手工删）。

## 7. 验证

```bash
uv run python -m pytest mcp_adapter/tests/ -q   # 237 passed
uv run ruff check mcp_adapter/                  # All checks passed
uv run python -m pytest tests/ -q               # 1120 passed, 2 skipped
```

`tests/` 里有 19 个 ERROR 与代码无关：沙箱拒绝创建 pytest 临时目录
（`PermissionError: EEXIST ... mkdir '/private/var/folders/.../pytest-of-root'`），
集中在 `test_gcs_sync` / `test_routes_admin_report` / `test_sync_pg_to_sqlite`。
