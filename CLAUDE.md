## ⚠️ 环境约束（强制）

- **包管理器**：`uv pip install <pkg>`（禁止 `pip` / `python -m pip`）
- **运行脚本**：`uv run python <script>.py`（禁止直接 `python`）
- Python 3.12+

---

# CLAUDE.md

本文件为 Claude Code 在 Sugar Bee（蜜蜂健康）项目中的操作指南。

## 项目概述

蜜蜂健康 — 面向家庭场景的慢病管理与健康数据记录 Web 应用（血压/血糖/体重/运动/餐食/骨密度/体检）。支持多用户无密码快速切换、AI 自然语言/拍照录入、综合健康分析、血糖预测、自动备份。

- **后端**：Flask + SQLite（开发），模块化 Blueprint 路由
- **前端**：单文件 `templates/index.html`（Jinja2 + Bootstrap 5 + Material Design）
- **AI**：多模型降级链（端砚 Sage-A1.5 → SenseNova → Agnes → ModelScope,配置见 `settings.AI_PROVIDERS`）

---

## 常用命令

```bash
# 安装依赖
uv pip install -r requirements.txt

# 启动应用（debug 模式，端口 5001）
uv run python app.py

# 运行测试
uv run python -m pytest tests/ -q

# 代码检查
uv run ruff check .
```

---

## 架构

### 核心模块

| 文件/目录 | 作用 |
|---|---|
| `app.py` | Flask 应用入口、首页、后台任务（备份/Garmin 同步）、Blueprint 注册 |
| `routes/` | 9 个 Blueprint 模块：auth / user / records / chat / dashboard / health / meds / prediction / admin |
| `services/` | 业务逻辑：健康分析、预测、用药、用户数据等 |
| `ai_client.py` | 统一 AI 调用层，按 `settings.AI_PROVIDER_ORDER` 跨供应商降级(含思考型模型空 content 兜底) |
| `glucose_parser.py` | 自然语言/图片 → 结构化健康记录 |
| `settings.py` | 血糖达标标准、AI 模型配置、BMI 计算、徽章逻辑 |
| `user_manager.py` | 多用户管理、session 切换、profile CRUD、手机/邮箱登录绑定 |
| `core/config.py` | `DB_NAME`、`AVATAR_FOLDER` 等配置 |
| `utils/db.py` | `get_db()` / `close_db()` / `init_db()`，Flask `g` 管理连接 |
| `utils/responses.py` | `api_success()` / `api_error()` 统一 JSON 响应包装 |
| `utils/auth.py` | `@login_required` / `@login_or_token_required` |
| `mcp_adapter/servers/writer.py` | 写 MCP Server（stdio/sse，11 工具），根目录 `mcp_writer.py` 是入口 |
| `mcp_adapter/servers/reader.py` | 只读分析 MCP Server（12 工具），根目录 `mcp_reader.py` 是入口 |
| `generate_report.py` | PDF 健康报告生成（reportlab） |
| `models.py` | SQLAlchemy 模型，用于 Alembic 迁移 |

### 前端

- 单文件 `templates/index.html`，CSS + HTML + JS 合一
- Bootstrap 5 + Bootstrap Icons
- Chart.js（趋势图）、FullCalendar（月历）、Marked.js（Markdown）、Cropper.js（头像裁剪）

### 数据库

- SQLite（`glucose.db`，可通过 `SUGAR_BEE_DB_PATH` 覆盖）
- 核心表 `records` 复用存储血糖/血压/运动/饮食/体重/用药，通过 `type` 列区分
- 所有查询必须带 `user_id` 过滤，实现多用户隔离
- 迁移在 `init_db()` 中以 `ALTER TABLE ... ADD COLUMN` + `try/except` 保证幂等；Alembic 脚本在 `migrations/`

### 后台机制

- **AI 预测**：`predict_morning_fpg()` / `predict_post_exercise_glucose()`，后台线程异步执行
- **预测关联**：真实值录入时自动匹配对应预测记录并计算误差
- **自动备份**：每 24 小时备份 `glucose.db` 到 `backups/`，保留 30 天
- **Garmin 同步**：每 2 小时同步一次（`GARMIN_SYNC_INTERVAL` 可配置）

### API 响应格式

所有 JSON 路由统一用 `api_success()` / `api_error()` 包装：

```json
{ "status": "success|error", "data": {}, "message": "...", "timestamp": 1234567890 }
```

---

## 开发与提交规范

- 中文界面，所有用户可见文本使用中文
- 按文件粒度提交，禁止 `git add .`
- 所有功能开发在 `feat/*`、`fix/*`、`refactor/*`、`docs/*`、`chore/*` 分支上进行
- 使用 `/git-feature start` 与 `/git-feature done` 管理分支生命周期
- 大规模修改前进入 Plan Mode
- 禁止顺手重构无关文件，禁止跨项目（如 `quant_lab`）共享非通用代码
- 修改 `glucose_parser.py` 前需先校验 `user_config.json` Schema
- 数据库修改前提示用户备份 `backups/`

---

## 测试与质量

- 测试框架：pytest，约 1030 个测试，覆盖率 100%
- CI 阈值：`fail_under=95`
- Lint：`ruff`，配置继承 `~/.config/ruff/ruff.toml`，项目扩展在 `pyproject.toml`

---

## 开发注意事项

- 前后端徽章逻辑（`getBadgeForRate()`）需保持同步：Python 在 `settings.py`，JS 在 `index.html`
- 血糖达标标准按类型区分（空腹/餐后1h/餐后2h/睡前/运动后），修改时注意 `settings.py` 中 `GLUCOSE_TARGETS` 的完整性
- `records` 表字段多，新增数据维度需在 `init_db()` 中添加迁移列
- `.env` 中存放 API Key，`user_config.json` 存用户个人档案，两者均被 `.gitignore` 忽略
- 严禁读取/修改 `.env`、`secrets.json`、`cookies.json` 等凭据文件
- Agent 直接写入数据请参考 [`mcp_adapter/README.md`](mcp_adapter/README.md)
- 更多项目文档见 [`docs/260616-README.md`](docs/260616-README.md)
