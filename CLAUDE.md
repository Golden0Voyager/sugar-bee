# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

蜜蜂控糖 — 家庭血糖健康追踪 Web 应用，面向 2 型糖尿病患者。支持多用户（无密码切换，家庭信任环境）。核心能力：AI 自然语言/拍照录入、血糖预测与误差追踪、多维健康数据管理（血糖/血压/运动/饮食/体重/用药）、AI 健康分析报告。

## 运行命令

```bash
# 安装依赖（使用 uv，不用 pip）
uv pip install -r requirements.txt

# 启动应用（debug 模式，端口 5001）
python app.py
# 访问 http://127.0.0.1:5001
```

无自动化测试套件、lint 配置或构建步骤。`test_ai_compare.py` 是手动运行的 AI 提供商对比脚本。

## 架构

### 单体 Flask 应用，5 个 Python 模块

- **`app.py`**（~4800 行）：所有路由、业务逻辑、数据库操作。是最核心也最大的文件。
- **`parser.py`**：AI 解析器 — 自然语言/图片 → 结构化健康记录（血糖、血压、运动、饮食、体重、用药）。
- **`ai_client.py`**：统一 AI 调用层。Gemini 优先（模型链降级），全部失败后自动切 ZhipuAI。
- **`settings.py`**：配置中心 — AI 模型列表、血糖达标标准（《中国糖尿病防治指南 2024版》）、徽章系统、BMI 计算。
- **`user_manager.py`**：多用户管理，基于 Flask session 的用户切换，`UserManager.get_current_user_id()` 获取当前用户。

### 前端：单文件 `templates/index.html`（~9400 行）

CSS + HTML（Jinja2）+ JS 全合一。Material Design 风格，Bootstrap 5 布局。关键依赖：Chart.js（趋势图）、FullCalendar（月历）、Marked.js（Markdown 渲染）、Cropper.js（头像裁剪）。

### 数据库

SQLite（`glucose.db`），通过 Flask `g` 对象管理 per-request 连接。后台线程（预测任务）使用独立 `sqlite3.connect()` 连接。

核心表 `records` 复用一张表存所有数据类型（血糖/血压/运动/饮食/体重/用药），通过 `type` 列区分。所有查询都带 `user_id` 过滤实现多用户隔离。

数据库迁移在 `init_db()` 中以 `ALTER TABLE ... ADD COLUMN` + `try/except` 模式执行，保证幂等性。

### AI 模型降级链

Gemini：`gemini-3-flash-preview` → `gemini-2.5-flash` → ZhipuAI：`glm-4.7-flash`（文本）/ `glm-4.6v-flash`（视觉）

### 关键后台机制

- **AI 预测**：`predict_morning_fpg()` / `predict_post_exercise_glucose()` — 后台线程异步执行，不阻塞页面
- **预测关联**：真实值录入时自动匹配对应预测记录，计算误差
- **自动备份**：`threading.Timer` 每 24 小时备份数据库到 `backups/`，保留 30 天

### API 响应格式

所有 JSON 路由统一用 `api_success()` / `api_error()` 包装：`{status, data, message, timestamp}`

## 开发注意事项

- 前后端徽章逻辑（`getBadgeForRate()`）需保持同步：Python 在 `settings.py`，JS 在 `index.html`
- 血糖达标标准按类型区分（空腹/餐后1h/餐后2h/睡前/运动后），修改时注意 `settings.py` 中 `GLUCOSE_TARGETS` 的完整性
- `records` 表字段多（血糖、血压、运动、饮食、体重共用），新增数据维度需在 `init_db()` 中添加迁移列
- `.env` 中存放 API Key（`GEMINI_API_KEY` 等），`user_config.json` 存用户个人档案，两者均被 `.gitignore` 忽略
- 中文界面，所有用户可见文本使用中文
