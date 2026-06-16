# AGENTS.md — Sugar Bee (蜜蜂控糖)

> 本文档是面向通用 Agent 的快速参考。项目级约束、提交规范与 Claude Code 专用指南见 [`CLAUDE.md`](CLAUDE.md)。

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
| `routes/` | 9 Blueprint modules: auth, user, records, chat, dashboard, health, meds, prediction, admin |
| `ai_client.py` | AI call layer — Gemini chain → OpenAI-compatible providers (Modelscope, Volc), fallback to ZhipuAI |
| `glucose_parser.py` | AI natural language/image → structured health records |
| `user_manager.py` | Multi-user management, password auth, profile CRUD, provider bindings |
| `settings.py` | Glucose targets (Chinese Diabetes Guideline 2024), badge system, AI model config, BMI calc |
| `core/config.py` | `DB_NAME` (env `SUGAR_BEE_DB_PATH`), `AVATAR_FOLDER` |
| `utils/db.py` | `get_db()`, `close_db()`, `init_db()` — SQLite via Flask `g` |
| `utils/responses.py` | `api_success()` / `api_error()` — standard JSON envelope |
| `utils/auth.py` | `@login_required`, `@login_or_token_required` decorators |
| `mcp_adapter/server.py` | MCP server for Claude Desktop integration (stdio/sse), inline SQLite writes, regex fast path |
| `generate_report.py` | PDF health report generation (reportlab) |
| `models.py` | SQLAlchemy models for Alembic migrations |

### Database

SQLite (`glucose.db`, configurable via `SUGAR_BEE_DB_PATH` env var). Core `records` table stores all health data types (glucose/bp/exercise/diet/weight/medication) via a `type` column. All queries filter by `user_id`.

Migrations run in `init_db()` using `ALTER TABLE ... ADD COLUMN` + `try/except` (idempotent). Alembic available for structured migrations in `migrations/`.

### Background Threads

- AI prediction (`predict_morning_fpg`, `predict_post_exercise_glucose`) runs in daemon threads
- Auto-backup every 24h to `backups/` (30-day retention)
- Garmin auto-sync every 2h (configurable via `GARMIN_SYNC_INTERVAL`)
- Uses separate `sqlite3.connect()` connections (not Flask `g`)

### AI Provider Chain

Modelscope (text) → Volc Engine (text) → Gemini direct API (text) → Gemini fallback → all-failed raise

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
| `test_mcp_server.py` | `mcp_server.py`: all tools, inline batch, regex parse |
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

- Frontend badge logic (`getBadgeForRate()`) must stay in sync: Python in `settings.py`, JS in `index.html`
- Glucose targets vary by type (fasting/post-1h/post-2h/pre-sleep/post-exercise) — see `settings.py` `GLUCOSE_TARGETS`
- Adding new data dimensions to `records` requires a new `ALTER TABLE` migration in `init_db()`
- `.env` stores API keys, `user_config.json` stores user profiles — both gitignored
- SQLite `date('now')` uses UTC — use `date('now', 'localtime')` when comparing against local timestamps
- Coverage pragma `# pragma: no cover` is used on code paths that cannot be traced by coverage.py (subprocess isolation in `app.py`, `google.genai` namespace package in `ai_client.py`)
