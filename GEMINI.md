# Project Intelligence: Sugar Bee

前身为 Glucose Tracker，这是一个专注于血糖健康管理的私密项目。

## 🧠 项目业务规范
- **功能特长**: 擅长处理长上下文的医疗血糖报告分析。
- **隐私保护**: 在生成分析或图表时，绝不输出真实的个人身份信息。

## 📁 隔离规范 (Isolation)
- **数据库**: `*.db` 数据库文件严禁提交。
- **本地报告**: `data/` 下生成的 Excel 和 PDF 报告仅限本地存储。
- **静态资源**: `static/avatars/` 已被 Git 忽略。
