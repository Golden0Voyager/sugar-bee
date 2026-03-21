# Project Intelligence: Sugar Bee

前身为 Glucose Tracker，这是一个专注于血糖健康管理的私密项目。

## 🚀 运行环境 (Runtime)
- **框架**: Flask + SQLite
- **环境管理**: `uv` (强制)
- **静态资源**: `static/avatars/` 已被 Git 忽略。

## 🧠 AI 协作规范 (AI Patterns)
- **固定模型**: **Gemini 1.5 Pro** (擅长处理长上下文的医疗报告分析)。
- **隐私逻辑**: 在生成 PDF 或分析图表时，绝不输出真实的个人身份信息。

## 📁 隔离规范 (Isolation)
- **数据库**: `*.db` 数据库文件严禁提交。
- **报告**: `data/` 下生成的 Excel 和 PDF 报告仅限本地存储。
