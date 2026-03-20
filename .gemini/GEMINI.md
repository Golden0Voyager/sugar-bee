# 🚀 Vibe Coding 项目大脑

## 1. 核心项目架构 & 联动协议
- **@quant_lab (量化中心)**:
  - 技术栈: Python (uv), yfinance, AkShare, Pandas.
  - 核心文件: `analyst_brain.py` (逻辑控制), `ai_config.py` (全局配置).
  - **强制准则**: 信号输出必须包含 [0-1] 的“可信度分数”，并附带对应的信号触发源。
- **@glucose_tracker (健康中心)**:
  - 技术栈: Streamlit (优先), JSON/CSV.
  - **数据完整性**: 修改 `parser.py` 前必须通过 `validator.py` 校验 `user_config.json` 的 Schema。
- **@knowledge_base (知识工厂)**:
  - 功能: 自动整理 PDF/DOCX。使用 Zhipu AI 生成卡片式 README。
  - **检索联动**: 遇到计算逻辑模糊时，AI 必须主动执行本地检索脚本，参考 `knowledge_base` 中的历史文献。

## 2. 2026 开发者生存规范 (Execution Guards)
- **环境隔离**: 严禁直接执行 `python`，必须前缀 `uv run`。始终检查项目根目录的 `uv.lock` 状态。
- **数据直观**: 编写任何解析逻辑前，必须 `!head -n 10` 或 `!df.info()`。**严禁基于猜想编写代码。**
- **架构纯净度**: `analyst_base.py` 仅保留核心公式。所有 UI、绘图、IO 扩展必须进入 `integration/` 文件夹。
- **参考隔离**: 严禁向 `ChatDev` 或 `valuecell` 写入任何业务代码，仅允许读取其架构模式。

## 3. 自动化工具链与审美 (Tools & Style)
- **网络代理**: 识别到 `pip install`, `curl`, 或 `yfinance` 下载任务时，自动注入：
  `export https_proxy=http://127.0.0.1:8118; export http_proxy=http://127.0.0.1:8118`
- **模型分工**: **Gemini 3.0 Pro** 负责多项目跨目录逻辑重构与长文档清洗；Claude 辅助局部 Snippet 优化。
- **UI/PPT 规范**: 强制联动 `web-design-guidelines` 技能。
  - 风格：Material Design 3 (MD3)。
  - 元素：高负空间、圆角 (16px+)、暗色模式优先。

## 4. 交互偏好 & 进阶本能
- **交互限制**: 仅限中文。代码输出仅限 Diff 或 Minimal Functional Block。
- **主动复用**: 发现 `quant_lab` 的 `plotting_engine` 与 `glucose_tracker` 的趋势图逻辑重合时，强制提示重构为通用组件。
- **技能自查**: 每当启动新功能开发，优先调用 `find-skills` 检查是否有现成 Vercel 技能可用。