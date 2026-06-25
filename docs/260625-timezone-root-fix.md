# 时区问题根因修复 (2026-06-25)

## 背景

Cloud Run 部署后，服务器时区强制 UTC（不可控），导致业务代码中大量时间相关功能出现 +8 小时偏移。虽然 Cloud Run 设置了 `TZ=Asia/Shanghai` 环境变量，但**代码层面多处硬编码使用 `datetime.now()` / `date.today()`**，绕过应用自带的 `utils/timezone.py` 时区抽象层。

## 根因

应用已实现 `utils/timezone.py` 时区抽象层（提供 `now()`、`today()`、`timestamp_str()` 等函数，自动根据 `SUGAR_BEE_TIMEZONE` 环境变量转换时区），但业务代码未全部接入：

| 位置 | 原写法 | 问题 |
|---|---|---|
| `glucose_parser._preprocess_relative_dates()` | `datetime.datetime.now()` | 相对日期（"昨天"、"60天前"）计算用 UTC 日期 |
| `glucose_parser.split_by_emoji()` | `.strftime(...)` | AI 提示词中的"当前录入时间"用 UTC |
| `glucose_parser._ensure_weight_captured()` | `.strftime(...)` | 体重兜底记录时间用 UTC |
| `user_manager.get_user_config()` | `date.today()` | 年龄计算用 UTC 日期 |
| `user_manager.get_user_config()` | `datetime.now().year` | 兜底年龄用 UTC 年 |

## 修复

| 文件 | 变更 | 说明 |
|---|---|---|
| `glucose_parser.py` | `+5 -3` | `datetime.now()` → `app_now()`，`.strftime()` → `app_timestamp_str()` |
| `user_manager.py` | `+4 -4` | `date.today()` → `app_today()`，移除无用 `import datetime` |
| `tests/test_glucose_parser.py` | `+58` | 3 个新测试：时区感知相对日期、AI 提示词时间、体重兜底时间 |
| `tests/test_user_manager_extended.py` | `+25` | 1 个新测试：用户年龄按应用时区计算 |

## 验证

- `uv run python -m pytest tests/ -q` — **1030 tests passed** ✅
- `uv run ruff check .` — **0 lint errors** ✅
- `uv run python -m pytest tests/ --cov --cov-report=term-missing` — **100% 覆盖 (3527/3527)** ✅
