# 🍯 Sugar Bee（蜜蜂控糖）

> 面向家庭场景的 2 型糖尿病血糖与健康数据管理 Web 应用。

Sugar Bee 是一个轻量级 Flask 单体应用，帮助家庭成员记录、追踪和分析血糖及相关健康数据。支持多用户快速切换、AI 自然语言/拍照录入、综合健康报告与血糖预测。

---

## 🚀 核心特性

- **多用户隔离**：家庭内部无密码快速切换，所有数据按 `user_id` 过滤隔离。
- **AI 智能录入**：通过自然语言或拍照自动提取血糖、血压、运动、饮食、体重、用药记录。
- **多维健康视图**：趋势图（Chart.js）、时间线、月历（FullCalendar）、统计概览卡片。
- **AI 健康分析与预测**：综合健康报告、空腹血糖预测、运动后血糖预测。
- **移动端适老化**：大字/高对比模式、更大触控目标、操作反馈增强。
- **自动备份**：每日定时备份 SQLite 数据库到 `backups/`，保留 30 天。

---

## 📦 快速开始

```bash
# 1. 安装依赖
uv pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 GEMINI_API_KEY 等必要密钥

# 3. 启动应用
uv run python app.py
```

访问 http://127.0.0.1:5001

---

## 📁 项目文档

| 文档 | 说明 |
|------|------|
| [`docs/260616-DEV_NOTES.md`](docs/260616-DEV_NOTES.md) | 开发笔记、架构说明、更新日志 |
| [`docs/plans/260612-cloud-run-deployment-plan.md`](docs/plans/260612-cloud-run-deployment-plan.md) | Google Cloud Run 部署方案 |
| [`docs/plans/260616-next-steps.md`](docs/plans/260616-next-steps.md) | 后续待办清单（P0/P1/P2） |
| [`docs/plans/260616-report-mobile-security-plan.md`](docs/plans/260616-report-mobile-security-plan.md) | 综合报告 UX + 适老化 + 安全修复计划 |

---

## ⚠️ 使用注意

本项目当前为 **MVP 版本**，面向家庭局域网信任环境设计：
- 无密码或弱密码切换，**不建议直接暴露到公网**。
- 修改数据库前建议先备份：`cp glucose.db glucose.db.backup_$(date +%Y%m%d)`。
- 详细安全与架构约束见 [`CLAUDE.md`](CLAUDE.md) 和 [`docs/260616-DEV_NOTES.md`](docs/260616-DEV_NOTES.md)。

---

*Created by Haining Yu*
