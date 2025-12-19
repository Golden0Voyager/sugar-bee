# 智能血糖记录本 (Smart Glucose Tracker)

这是一个轻量级的本地 Web 应用，用于记录和追踪血糖数据。它集成了 Gemini AI，支持通过自然语言输入（如“今天早上空腹6.5”）自动提取数据。

## 功能特点
- **智能输入**: 描述你的血糖情况，AI 自动提取数值、时间、类型和备注。
- **手动录入**: 标准的表单输入。
- **数据管理**: 本地 SQLite 数据库，数据安全。
- **导出**: 一键导出 CSV 格式，方便在 Excel 中查看。

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置 API Key
复制 `.env.example` 为 `.env` 并填入你的 Gemini API Key：
```bash
cp .env.example .env
```
打开 `.env` 文件，修改 `GEMINI_API_KEY=你的密钥`。

### 3. 运行应用
```bash
python app.py
```
然后浏览器访问 `http://127.0.0.1:5001`。

## 目录结构
- `app.py`: 主程序 (Web 服务 + 数据库)
- `parser.py`: AI 解析逻辑
- `templates/index.html`: 前端界面
- `glucose.db`: 数据库文件 (自动生成)
