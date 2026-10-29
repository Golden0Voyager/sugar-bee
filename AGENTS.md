# AGENTS.md — Sugar Bee (蜜蜂健康)

> 本文档是面向通用 Agent 的快速参考。项目级约束、提交规范与 Claude Code 专用指南见 [`CLAUDE.md`](CLAUDE.md)。

## 与用户沟通的方式（Agent 请遵守）

用户不是专业程序员，请按下面方式对话，不要改这条约束：

- **默认用中文**回复；代码、命令、日志、字段名沿用仓库原样，不必翻译。
- **少用术语**。必须用时，先用一句大白话说明它是什么，再带上原词（例如「索引（index，相当于书的目录）」）。
- **由浅入深**：先给结论和类比，再讲细节；不要一上来就罗列实现细节或大量文件路径。
- 结尾说明「改了什么、我验证了什么、你还需要做什么」，不要假设用户能读懂 diff。

## Quick Reference

```bash
# Install dependencies
uv pip install -r requirements.txt

# Run app (debug, port 5001)
uv run python app.py

# Run all tests
uv run python -m pytest tests/ -q

# Run with coverage
uv run python -m pytest tests/ --cov --cov-report=term-missing

# Run a specific test
uv run python -m pytest tests/test_settings.py -v
```

## Makefile（Cloud Run 部署）

```bash
make help           # 显示所有命令
make lint           # ruff 检查
make test           # 运行测试
make build          # 构建推送 + 自动更新 cloud-run.yaml digest
make deploy         # gcloud run services replace
make deploy-quick   # make deploy-quick DIGEST=sha256:xxx（跳过 YAML 更新）
make logs           # 查看 Cloud Run 实时日志
make clean-images   # 列出 Artifact Registry 历史镜像
```

**注意**：`make build` 自动获取新镜像 digest 并写入 `deploy/cloud-run.yaml`，使用 digest 而非 `:latest` 确保部署可重复。

## Environment Constraints (Enforced)

- **Package manager**: `uv pip install <pkg>` — NEVER use `pip` or `python -m pip`
- **Run scripts**: `uv run python <script>.py` — NEVER bare `python`
- Python 3.12+ required

## Architecture

Monolithic Flask app (`app.py`) with Blueprint modules for routing. Single-page frontend (`templates/index.html`, ~9400 lines) with Jinja2 + CSS + JS, using Bootstrap 5, Chart.js, FullCalendar, and Marked.js.

### Core Modules

| File | Role |
|---|---|
| `app.py` | Flask app, route `/`, backup, Garmin sync, Blueprint registration |
| `routes/` | 11 Blueprint modules: auth, user, records, chat, dashboard, health, meds, prediction, admin, bone, checkup |
| `ai_client.py` | AI call layer — Gemini chain → OpenAI-compatible providers (Modelscope, Volc), fallback to ZhipuAI |
| `glucose_parser.py` | AI natural language/image → structured health records |
| `checkup_parser.py` | 体检报告图片（照片或 PDF 页）→ 结构化指标（视觉模型只抄录，状态按参考范围自算） |
| `bone_density.py` | 骨密度领域逻辑：T 值 WHO 分级、部位归一化、趋势汇总、入参校验 |
| `bone_parser.py` | 骨密度（DXA）报告图片（照片或 PDF 页）→ 多部位 T/Z/BMD（视觉模型只抄录，一次报告出多条） |
| `utils/report_files.py` | 报告上传解码：base64 → 模型图片列表，PDF 服务端渲染成 JPEG，多文件合并，`MAX_IMAGES` 上限 |
| `routes/api_bone.py` | 骨密度记录 CRUD + `/api/bone/parse` 报告识别，独立表 `bone_density_records` |
| `routes/api_checkup.py` | 体检历史（`/api/checkup/parse` 只解析、`/save` 落库、`/list`、`/<id>`、`/delete`） |
| `static/css/main.css` | 全局样式；`.dropdown-item` 是 flex，两行式菜单项要用 `.item-stacked` 改回 block |
| `user_manager.py` | Multi-user management, password auth, profile CRUD, provider bindings |
| `branding.py` | 应用品牌个性化：场景模板（persona）、自定义应用名/图标/标语、模块建议与校验 |
| `settings.py` | Glucose targets (Chinese Diabetes Guideline 2024), badge system, AI model config, BMI calc |
| `core/config.py` | `DB_NAME` (env `SUGAR_BEE_DB_PATH`), `AVATAR_FOLDER` |
| `utils/db.py` | `get_db()`, `close_db()`, `init_db()` — SQLite via Flask `g` |
| `utils/responses.py` | `api_success()` / `api_error()` — standard JSON envelope |
| `utils/auth.py` | `@login_required`, `@login_or_token_required` decorators |
| `mcp_adapter/servers/writer.py` | 写 MCP server（stdio/sse，11 工具）：单条走 Flask API，批量内联写库 |
| `mcp_adapter/servers/reader.py` | 只读分析 MCP server（12 个 `sbhealth_*` 工具），`mode=ro`，可独立部署 |
| `mcp_adapter/core/` | MCP 共享核心：`domain.py` 类型/阈值、`timefmt.py` 时间口径、`analytics.py` 纯计算 |
| `scripts/deploy_mcp.sh` | 两个 MCP server 一起发布到 `/root/health_tools`（`--dry-run` / `--restart` / `--only`） |
| `generate_report.py` | PDF health report generation (reportlab) |
| `models.py` | SQLAlchemy models for Alembic migrations |

### Database

- **本地 (SQLite)**: `glucose.db`（默认，可 `SUGAR_BEE_DB_PATH` 覆盖）
- **生产 (Cloud Run)**: PostgreSQL 17 via Cloud SQL（`SUGAR_BEE_DATABASE_URL` 环境变量触发切换）

`DB_TYPE` 由 `core/config.py` 自动判断：有 `SUGAR_BEE_DATABASE_URL` 则为 `postgres`，否则 `sqlite`。
`utils/sql_dialect.py` 提供统一 SQL 片段（`date_sql`、`epoch_sql` 等），业务代码无需写 `if DB_TYPE`。

Core `records` table stores all health data types (glucose/bp/exercise/diet/weight/medication) via a `type` column. All queries filter by `user_id`。

### Background Threads

- AI prediction (`predict_morning_fpg`, `predict_post_exercise_glucose`) runs in daemon threads
- Auto-backup every 24h to `backups/` (30-day retention)
- Garmin auto-sync every 2h (configurable via `GARMIN_SYNC_INTERVAL`)
- Uses separate `sqlite3.connect()` connections (not Flask `g`)

### AI Provider Chain

Modelscope (text/vision) → SenseNova (text only, no vision)

## Linting

- **Tool**: ruff (in dev deps: `uv pip install ruff`)
- **Global config**: `~/.config/ruff/ruff.toml` — all projects inherit this
- **Project config**: `pyproject.toml` extends global, adds project-specific ignores

```bash
# Check code
uv run ruff check .

# Auto-fix
uv run ruff check . --fix

# Check specific file
uv run ruff check app.py
```

**ruff config in `pyproject.toml`**:
```toml
[tool.ruff]
extend = "~/.config/ruff/ruff.toml"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM"]
ignore = ["E501", "E402", "E702"]  # E402/E702 for test files
```

**Global rules**: `E` (errors), `F` (Pyflakes), `W` (warnings), `I` (imports), `N` (naming), `UP` (pyupgrade), `B` (bugbear)

## Testing

- **Framework**: pytest 9.x, 30 test files, **1030 tests** + 2 skipped
- **Coverage**: **100%** (3,527 statements, 0 missing) across all 27 source modules
- **CI threshold**: `fail_under=95` in `pyproject.toml`
- **Test DB**: Each test gets a temp SQLite file via `db_info` fixture; `SUGAR_BEE_DB_PATH` env var set automatically
- **Auth helper**: `client_authenticated` fixture creates `_test` user and logs in
- **Isolation**: `isolate_db` fixture patches `core.config.DB_NAME` for integration tests
- **Async support**: `pytest-asyncio` for MCP server async tests
- **CI env vars**: `SECRET_KEY=test-secret-key-for-ci`, `FLASK_ENV=testing`

### Key Test Files

| File | Coverage Target |
|---|---|
| `test_app_py.py` | `app.py`: index route, backup, Garmin sync, background tasks |
| `test_ai_client.py` | `ai_client.py`: provider chain, Gemini model, CN endpoint |
| `test_auth_decorators.py` | `utils/auth.py`: login_required, login_or_token_required |
| `test_generate_report.py` | `glucose_parser.py`: parse, history_context |
| `test_glucose_parser.py` | `glucose_parser.py` additional branches |
| `test_mcp_server.py` | `mcp_adapter/servers/writer.py`: all tools, inline batch, regex parse |
| `test_routes_coverage.py` | Route coverage gaps: auth, health, records, user |
| `test_remaining_routes.py` | Prediction, meds, admin, font registration |
| `test_services_garmin.py` | Garmin connect API, activity mapping, sync logic |
| `test_user_manager.py` / `test_user_manager_extended.py` | User CRUD, password, provider bindings |

### CI (GitHub Actions)

```yaml
# .github/workflows/ci.yml
- pip install -r requirements.txt
- pytest -v          # 1030 tests
- py_compile check   # syntax validation
- docker build -t sugar-bee:ci .
```

**Note**: CI uses `pip` (not `uv`) — intentional for GitHub Actions compatibility.

## Gotchas

- **功能模块与品牌**：模块白名单唯一来源是 `branding.ALL_MODULES`（glucose / blood_pressure / exercise / weight / medication / diet / bone_density / checkup），前端 `index.html` 的 Jinja `ALL_MODULES`、`MODULE_LABELS` 和 JS `ACTIVE_MODULES` 必须同步
- 新用户默认 `branding.BASE_MODULES`（慢病管理基础版：血压/用药/体重/运动/餐食/骨密度/体检），血糖按首次引导场景显式开启；`enabled_modules` 为空仍视为「全部启用」（历史兼容）
- **`enabled_modules` 空数组 = 全部启用**，不是「一个都不要」。所有读它的地方必须用同一套语义：`app.py` 首页和 `/api/user/modules` 都是 `or ALL_MODULES`；`/api/user/branding` 返回的是原始值（`[]`），前端 `initOnboarding()` 必须自己补上 `raw.length ? raw : data.all_modules`。写它的两个接口都禁止空数组（`build_branding_payload` 和 `/api/user/modules` POST），否则「关掉全部」会被读成「全部打开」
- 应用名/图标/标语/场景存于 `user_profiles`（`app_name` / `app_emoji` / `app_tagline` / `persona`），展示前统一走 `branding.resolve_branding()` 回落默认品牌；`app_name` 为空即「未完成初始化」，前端会弹首次引导
- **场景模板只管功能，不管名字**：所有模板默认应用名都是「蜜蜂健康」、默认图标都是 🐝，模板之间只有功能模块和标语不同。`PERSONAS` 里的 `icon` 只用于选择卡片上的小图标（让选项看起来不一样），不会被写进用户品牌设置；`emoji` / `name_suggestions` 由 `persona_options()` 统一注入。历史行为是每个模板各起各的名字（我的血压本 / 我的运动日志），一家人的界面看起来像六个不同应用，已废弃
- **场景模板固定 4 个**（`daily_health` 日常健康 / `glucose_care` 多维控糖 / `fitness` 运动健身 / `family_care` 为家人记录）：血糖是唯一不在 `BASE_MODULES` 里的模块，所以模板差异主要落在「要不要血糖」上。加模板前先想清楚——曾经有 6 个，其中「全部功能」和「控糖」模块完全相同（纯重复），已删。`tests/test_branding.py` 会卡住模板数量、顺序和模块组合重复
- **设置页点场景只换模块**：`applyPersona(persona, prefix)` 里只有 `prefix === 'onboarding'` 才用默认值填名字/图标/标语，设置页只改模块勾选，不能覆盖用户已经改好的名字
- **扩容模块白名单要配一次性数据迁移**：`enabled_modules` 非空即白名单，老账号存的旧列表里没有新模块，不补的话升级后新功能入口直接消失。做法是在 `utils/db.py` 的 `_apply_module_backfill()` 里加模块名，并把 `MIGRATION_NEW_MODULES` 换个新标记名（标记存 `app_migrations` 表，只跑一次，否则用户主动关掉后重启又被打回）
- **首次引导不能套用模板覆盖老配置**：`initOnboarding()` 里若 `branding.enabled_modules` 非空就沿用其勾选，只在新账号时才 `applyPersona()`。默认模板 `daily_health` 不含血糖，套用会把血糖用户的血糖模块冲掉
- **两段式引导**：先「品牌初始化」(`onboardingModal`)，再「新手使用引导」(`tutorialModal`，教怎么记、怎么测才准)。后者由 `maybeShowTutorial()` 触发，标记存 `localStorage['sbTutorialSeen:<userId>']`（按用户隔离，同设备换人要再教一次），右上角菜单「📖 使用引导」可随时重看
- **引导文案要跟着模块走**：`tutorialModal` 里的示例和「怎么测才准」各段都用 `{% if 'xxx' in active_modules %}` 包着。妈妈/岳母没有血糖模块，讲「长按血糖格子」等于讲了个不存在的功能。`tests/test_tutorial.py` 会校验这层条件
- **AI 生成类功能必须先过模块门禁**（历史 bug）：血糖预测曾挂在首页后台线程上无条件跑，给不测血糖的家人凭空插「空腹 6.x mmol/L」+ 一段 AI 解释，在「每日详情」里看着像他自己测的。现在三层都要挡：
  1. 入口层：`app.py` 首页 `if 'glucose' not in enabled_modules` 就不起线程；`/trigger_prediction` 直接 400 `module_disabled`；`backfill_post_exercise_predictions` 开头短路
  2. 渲染层：`renderDayDetails` 和日视图时间轴的循环里 `if (r.is_predicted && !ACTIVE_MODULES.has('glucose')) return;` —— 只藏「预测」徽章不够，数值/类型/AI 解释会一起漏出来
  3. 数据层：`utils/db.py::_apply_prediction_cleanup()` 一次性删掉非血糖用户的历史 `is_predicted = 1` 记录（只删 AI 生成的，用户真实数据一条不动）
  门禁**不要放进 `predict_morning_fpg` 内部**：那些函数被大量测试用精确的 `fetchone.side_effect` 序列驱动，内部多一次查询会把序列整体错位
- **报告上传走 `utils/report_files.py`，不要直接塞 base64 给视觉模型**（历史 bug：只支持单张 jpg，PDF 直接被拒）。两个入口 `/api/checkup/parse` 和 `/api/bone/parse` 都先用 `to_report_pages(images_b64)` 展开全部页面，再交给 `utils/report_processing.py` 分批：
  - PDF 有可靠原生文字层时走文本模型；扫描件、乱码、文本输出异常或指标缺乏原文依据时回退视觉模型。视觉页才用 pypdfium2 惰性渲染成 JPEG（`PDF_DPI=150`），不要在请求开始时渲染整份 PDF
  - `MAX_IMAGES=3` 是**每次模型调用**的页数上限，`MAX_REPORT_PAGES=50` 是整次上传的安全上限；整份报告必须全部处理，超过上限明确报错，不能静默截断。模型并发最多 2，部分失败必须返回 `failed_pages` / `warnings`
  - 前端 `REPORT_MAX_FILES=50` 只负责超限提示，不得再 `slice()` 静默丢弃文件；失败页提示要转义
  - 解码失败抛 `ReportFileError`，路由统一转 400 + 「请上传体检报告照片或 PDF」，不要让它冒成 500
  - 渲染后的图片一律按 `image/jpeg` 往下传，别再用上传时的 mime（PDF 的 mime 传下去模型会报错）

- **`{% set active_modules %}` 必须放在模板最顶部**（`<body>` 之后第一件事）：右上角头像菜单里的入口也要按模块显示，Jinja 的 `{% set %}` 只对之后的内容生效，放到页面中间会让菜单渲染时 `active_modules` 还是 undefined，条件恒为假、入口静默消失
- **低频功能入口在头像菜单里**：骨密度 / 体检历史一年才做一次，不占首页版面，入口是头像菜单里「💊 药物管理」下面那两项（带「最近一次」摘要，靠 `boneEntrySummary` / `checkupEntrySummary` 两个 id 填充）。药物管理本身也做了 `medication` 模块判断——运动健身模板没有用药，不该出现这个入口
- **首页趋势卡片必须跟着模块开关走**（历史 bug）：`glucoseMonitorCard` / `bloodPressureChartCard` / `weightChartCard` 曾经无条件渲染，没开血糖的家人打开首页会看到一张只有绿色目标区间、一条数据都没有的「血糖趋势」图，像是自己的记录丢了。三张卡片现在都包在 `{% if 'xxx' in active_modules %}` 里；对应的 `initGlucoseChart` / `initBloodPressureChart` / `initWeightChart` 开头有 `if (!document.getElementById('xxx')) return;` 兜底——卡片不渲染时它们会被 JS 调用到（`changeBPRange` 等），缺这行就是 TypeError。血压/体重还有一层「没数据就 `display:none`」的 JS 兜底，但那只管有没有数据，管不了模块关没关
- **`avg_glucose` 的黑名单要跟着新维度补**：`services/timeline_service.py` 用「value>0 且没有 systolic_pressure 且 type 不在黑名单」判断一条记录是不是血糖，黑名单漏一个类型就会把别的维度当血糖算（`体重` 漏过一次，日历上出现「血糖68 (0%)」）。前端 `generateCalendarEvents` 里的 `NON_GLUCOSE_TYPES` / `isGlucoseEntry` 是同一份规则，改一边要改另一边
- **能量统计只在真记了饮食或运动时显示**（历史 bug）：后端恒按 `净热量 = 摄入 - (基础代谢 + 运动消耗)` 算，什么都没记的一天 `净热量 = 0 - 基础代谢` 也恒为负，于是日历徽章和每日详情天天写着「👍 减糖减脂 1417」，等于说「你躺着就减了 1417 大卡」。三个渲染点（`generateCalendarEvents` 的能量徽章、`showDayStats` 的 chips 行、`renderDayDetails` 的统计行）统一用 `hasEnergyData(stats)` = `cal_in > 0 || cal_out_exercise > 0` 把关，`tests/test_home_energy_badge.py` 会卡住调用点数量
- **CGM 槽位匹配口径：5 分钟窗口、窗口内 CGM 优先**（2026-10-29 定稿，勿改回 30 分钟）：三个入口必须同步——`routes/api_dashboard.py::api_day_overview`、`routes/api_prediction.py::prediction_status`、`services/dashboard_service.py::get_dashboard_stats`。规则：CGM 读数距槽位 ≤5 分钟才算数（6–30 分钟窗外回退手工记录）；`prediction_status` 里窗口内有 CGM 时**以 CGM 为准并重算预测误差**，不再只作「无手工记录时的兜底」。手工记录按 type 归槽（空腹→fasting），CGM 按时间贴近槽位，两套匹配逻辑不要混
- **默认头像唯一清单是 `utils/avatars.py`**：人物/动物各 9 个，文件在 `static/avatars/defaults/`；空头像按用户 ID 固定回退，主动选择写 `app_users.avatar`，已有上传图不可被回退覆盖。新增默认头像必须同步 SVG、清单和测试；`.gitignore` 与 `.dockerignore` 只给 `defaults/` 开例外，用户上传文件仍忽略
- Frontend badge logic (`getBadgeForRate()`) must stay in sync: Python in `settings.py`, JS in `index.html`
- Glucose targets vary by type (fasting/post-1h/post-2h/pre-sleep/post-exercise) — see `settings.py` `GLUCOSE_TARGETS`
- Adding new data dimensions to `records` requires a new `ALTER TABLE` migration in `init_db()`
- `.env` stores API keys, `user_config.json` stores user profiles — both gitignored
- SQLite `date('now')` uses UTC — use `date('now', 'localtime')` when comparing against local timestamps
- Coverage pragma `# pragma: no cover` is used on code paths that cannot be traced by coverage.py (subprocess isolation in `app.py`, `google.genai` namespace package in `ai_client.py`)

## 环境差异（⚠️ 重要）

| 场景 | 数据库 | 触发条件 |
|------|--------|---------|
| 本地开发 | SQLite (`glucose.db`) | 无 `SUGAR_BEE_DATABASE_URL` |
| 生产 (Cloud Run) | PostgreSQL 17 | 有 `SUGAR_BEE_DATABASE_URL` |

**常见陷阱：**
- `_inline_params()` 只会在 PostgreSQL 模式下被调用（`_CompatCursor.execute`），SQLite 不走此路径
- `epoch_sql('?')` 在两种模式下都产生占位符（SQLite: `strftime('%s', ?)`，PG: `EXTRACT(EPOCH FROM ?::timestamp)`）
- `date_sql('timestamp')` 返回列表达式而非占位符（SQLite: `DATE(timestamp)`，PG: `timestamp::date`）
- 大多数 `utils/sql_dialect.py` 函数在生产模式和本地测试中行为不同；写新 SQL 片段务必双端验证
