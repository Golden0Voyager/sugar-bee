# Sugar Bee 后续待办清单

> 本文件汇总 2026-06-16 代码审查与优化后**尚未实施**的问题与改进方向，按优先级排列。
> 最近已完成工作见 PR #19（报告生成反馈、移动端适老化、关键安全修复）。

---

## P0 — 安全与稳定性（建议尽快处理）

| 问题 | 位置 | 说明 | 建议方案 |
|------|------|------|---------|
| 后台线程 SQLite 并发访问 | `app.py:100-121` | 后台预测/备份线程独立 `sqlite3.connect`，与 Flask 主线程竞争 | 启用 WAL 模式；捕获 `database is locked` 指数退避重试；或统一连接池 |
| 自动备份/同步定时器失败仍无限重启 | `app.py:175-213` | 备份或 Garmin 同步异常后 `finally` 仍重启定时器 | 连续失败 N 次后停止并告警；token 缺失时停止同步 |
| 预测关联 SQL 字符串拼接 | `services/prediction_service.py:57-72` | `type_condition` 直接拼入 SQL | 改用参数化查询或严格白名单校验 `record_type` |
| Agent Token 鉴权 fallback 风险 | `utils/auth.py:31-68` | 未带 Token 时回退到 session 鉴权 | 明确区分必须 Token 的端点与必须 session 的端点，避免混合装饰器 |
| AI 客户端无超时 | `ai_client.py:56-98` | OpenAI/Gemini 客户端均未设置 timeout | `httpx.Client(timeout=...)` + `OpenAI(timeout=...)` |
| AI 解析 JSON 提取过于贪婪 | `glucose_parser.py:324` | `r'(\[[\s\S]*\])'` 可能提取到无效 JSON | 先尝试 `json.loads`；失败后使用非贪婪正则 `r'(\[[\s\S]*?\])'` |
| 解析失败静默返回 | `glucose_parser.py:330-332` | 异常直接 `return []` | 记录错误日志并让上层返回 500/502 |
| 前后端血糖目标逻辑不一致 | `settings.py:177-196` vs `templates/index.html:2124-2139` | 前端缺少 `晚饭前/晚餐前` 分支 | 对齐逻辑或由后端统一生成目标映射 |
| `get_current_user_id` 改 None 后的调用方判空 | 多处 | 部分路由未处理 `None` | 统一在路由入口校验，未登录返回 401/跳转 |

---

## P1 — 交互、性能与可维护性（近期优化）

### 前端交互

| 问题 | 位置 | 说明 | 建议方案 |
|------|------|------|---------|
| 时间线数据并发控制缺陷 | `templates/index.html:1965-1998` | `_timelineLoading` 为 rejected promise 时二次请求会抛异常 | `await _timelineLoading` 包 try-catch |
| `renderOverview` 硬编码卡片选择器 | `templates/index.html:2560-2573` | 用 `:nth-child` 假设模块顺序，模块禁用时崩溃 | 给卡片加 `data-card-type`，按属性选择；或判空 |
| `loadHealthStats` 未判空 | `templates/index.html:2240-2358` | 大量 `querySelector` 结果直接 `.innerHTML` | 所有选择器后加 `if (el)` 保护 |
| 时间轴渲染大量重复 | `templates/index.html:5135-5631` | `showDayStats` 与 `renderDayDetails` 逻辑几乎相同 | 提取 `renderTimelineItem(entry)` 共享 |
| 长按槽位状态竞态 | `templates/index.html:2672-2755` | `renderOverview` 重绘时长按状态可能错乱 | `document` 级 `mouseup` 外移；状态 Map 隔离 |
| `renderDayDetails` 循环 `innerHTML +=` | `templates/index.html:5584` | 多次重排，事件监听器丢失 | 一次性构建字符串或 `document.createElement` |
| 手机端日视图导航重复渲染 | `templates/index.html:5667-5677` | `mobileDayNav(true)` 又调回 `showMobileDayView` → `renderDayDetails` | 检查并合并手机端导航逻辑 |
| 手动预测按钮查找方式脆弱 | `templates/index.html:3074-3111` | `[onclick*="triggerManualPrediction"]` 查找按钮 | 给按钮加唯一 ID |
| 配额模态框倒计时泄漏 | `templates/index.html:3116-3181` | 模态框异常移除时定时器可能未清理 | 回调中检查 `modal` 是否仍在 DOM |
| `showDayStats` 缺少 XSS 转义 | `templates/index.html:5170-5189` | 动态内容未 `escapeHtml` | 统一使用 `escapeHtml` |
| 健康分析卡片初始渲染依赖服务端 | `templates/index.html:976-1075` | JS 局部刷新逻辑已添加，但首次加载仍是 Jinja2 | 保持现状即可；如需纯客户端渲染再统一 |

### 图表与渲染

| 问题 | 位置 | 说明 | 建议方案 |
|------|------|------|---------|
| 图表初始化重复代码 | `initGlucoseChart` / `initCgmChart` / `initBloodPressureChart` / `initWeightChart` | 日期范围、配置结构大量重复 | 提取 `getDateRange(days, offset)` + `createChartBaseConfig()` |
| CGM 检测重复扫描 | `templates/index.html:4123-4133` | 每次调用遍历全部时间线 | 仅在 `timelineData` 变化时缓存 `hasCgm` |
| 图表 `afterBuildTicks` 修改内部数组 | `templates/index.html:4011-4021` | 直接 `unshift`/`push` ticks | 返回新数组 |
| Chart.js 自定义插件未清理 | 多个图表 init | `destroy()` 不清理插件引用 | 图表重建时同步注销/复用插件 |

### 样式

| 问题 | 位置 | 说明 | 建议方案 |
|------|------|------|---------|
| 剩余大量内联样式 | `templates/index.html`（约 380+ 处） | 头像、按钮、文字等仍有 `style=""` | 继续提取为 `.avatar-sm/.avatar-lg/.btn-action` 等工具类 |
| 用药列表小字 | `templates/index.html:521, 544, 564` 等 | 多处 `0.75rem`/`0.7rem` | 统一使用 CSS 类控制，适老化模式下再放大 |
| 编辑记录模态框字段过多 | `#editModal` | 16+ 输入框，老人认知负荷高 | 分步/分组展示，或按类型动态显示字段 |
| 适老化后续增强 | — | 当前仅字体/触控目标 | 增加高对比强制模式、操作震动反馈、底部固定保存按钮 |

---

## P2 — 代码质量与工程化（中期）

| 问题 | 位置 | 说明 | 建议方案 |
|------|------|------|---------|
| 生产环境泄露堆栈 | 多处路由 | `traceback.print_exc()` 后直接返回原始异常 | 生产环境用 `logging.error()`，前端返回通用错误 |
| `SECRET_KEY` 长度 | `app.py:28-42` | 开发模式使用 128 位 | 统一 `secrets.token_hex(32)` |
| 头像上传 MIME/大小校验 | `app.py:46` | 仅检查后缀，限制 16MB | 限制 2-5MB；校验 MIME 类型 |
| `get_user()` 全表扫描 | `user_manager.py:65-71` | 调用 `get_all_users()` 再线性查找 | 实现 `get_user_by_id()` 直接查询 |
| 用药动作/预测值字段未对齐 | `glucose_parser.py:282-315` vs `api_records.py:464-475` | `medication_action`、`predicted_value` 未写入 DB | 新增迁移列并写入；校验枚举值 |
| 冲突检测逻辑重复 | `add_record` vs `batch_add` | 血压/血糖/体重冲突规则分散 | 提取统一冲突检测函数 |
| 重复函数 | `templates/index.html:6766, 8600` | `importCsvFile` 与 `importCSV` | 删除其中一个 |
| 默认 `user_id=1` 残留 | `services/*.py` | 多个服务函数仍有默认参数 | 逐步移除默认值，强制调用方传入 |
| 流式聊天异常处理 | `ai_client.py:215-224` | 网络中断直接抛异常 | 增加 `try/except` 并 yield 错误提示 |

---

## 建议的下一步实施顺序

1. **P0 安全/稳定性**：优先处理 Agent 鉴权、SQLite 并发、AI 超时、解析错误静默。
2. **P1 前端稳定性**：`ensureTimelineData`、空元素保护、时间轴渲染统一。
3. **P1 性能**：图表工厂函数、CGM 缓存、`innerHTML +=` 重构。
4. **P2 工程化**：统一错误处理、移除默认用户、清理死代码。
5. **适老化后续**：底部固定按钮、震动反馈、更高对比度选项。

---

## 备注

- 以上问题来自 2026-06-15/16 的多轮代码审查，部分已在 PR #17、#18、#19 中解决。
- 每行位置基于审查时的代码快照，后续重构可能导致行号偏移，建议以具体函数/选择器为准。
- 新增功能前请先按 CLAUDE.md 进入 Plan Mode 并走 `/git-feature` 流程。
