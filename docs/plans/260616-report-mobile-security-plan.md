> 来源：`/Users/hainingyu/.claude/plans/sharded-doodling-pnueli.md`  
> 同步日期：2026-06-16  
> 说明：本文件为 Claude Code Plan Mode 生成的原始计划，同步到项目 docs/ 方便团队查阅。

# Sugar Bee 综合健康报告 UX + 移动端适老化 + 关键安全修复实施计划

## Context

本次工作源于三方面用户反馈与代码审查结论：

1. **综合健康报告生成无反馈**：用户点击"重新生成"后页面长时间静止，没有 loading、进度或明确成功/失败提示。
2. **移动端适老化体验不足**：老年人用户需要更大的触控目标、更高的对比度、更清晰的文字层级和操作反馈。
3. **审查发现的关键安全问题**：部分后端路由未严格校验 `user_id` 归属，默认用户硬编码为 `1`，存在横向越权风险。

目标：在不改变整体视觉风格、不引入新框架的前提下，优先修复报告生成反馈问题，补充移动端适老化能力，并补齐关键数据隔离安全短板。

---

## 约束

- 保持 Bootstrap 5 + Material Design 风格不变。
- 按 CLAUDE.md 一文件一提交，中英双语 commit message。
- 所有功能开发在 `feat/*` 或 `fix/*` 分支进行，通过 `/git-feature done` 合入 main。
- 修改前先读懂现有代码，禁止顺手重构无关文件。

---

## 分阶段实施计划

### Phase 1：综合健康报告生成反馈优化（用户最直观痛点）

**关键文件**：
- `templates/index.html`
- `static/js/analysis.js`
- `routes/api_health.py`
- `services/health_service.py`

**具体修改**：

1. **前端：持久化 loading 与禁用重复点击**
   - 将 `static/js/analysis.js` 中的 `selectAnalysisOption` 逻辑合并到 `templates/index.html`，减少一次弱网请求。
   - 在 `triggerManualAnalysis` 中启用加载状态：
     - 选项：`showLoading: true`（复用 `apiCall` 的 `toggleLoading`）或新增专用"生成中"模态框。
     - 本次采用**专用模态框**：更明确，可展示"正在查询数据 / 正在生成分析 / 正在保存报告"步骤文案。
   - 生成期间禁用"重新生成"按钮和相关卡片点击。

2. **前端：优化成功/失败反馈**
   - 成功时：显示"分析报告已生成"Toast，并**局部刷新**报告卡片内容，而不是立即 `location.reload()`。
   - 失败时：区分网络错误、AI 服务错误、配额耗尽（429）、数据不足，给出明确中文提示。
   - 配额耗尽继续使用现有 `showQuotaExhaustedModal` 倒计时重试。

3. **后端：细化错误类型返回**
   - `routes/api_health.py:analyze_health` 捕获 AI 调用异常后，检查错误内容：
     - 429 / quota 相关 → `api_error(..., status_code=429, error_type='quota_exceeded', details={'retry_after': ...})`
     - AI 不可用 → `error_type='ai_unavailable'`
     - 其他 → `error_type='analysis_failed'`
   - `services/health_service.py` 中 `AI_AVAILABLE` 为 false 时返回结构化错误，避免返回 500 通用消息。

4. **后端：增加生成超时保护**
   - `ai_client.call_ai` 尚无超时；本阶段先为 `task_type='report'` 在 `health_service.py` 外层加 `signal`/线程超时（若平台支持），或至少记录开始时间并在前端提示"生成可能需要 10-30 秒"。
   - **简化方案**：本次不在后端引入任务队列，仅在前端展示持续 loading 并设置 `fetch` 超时提醒。

**验证**：
- 点击"重新生成"后，出现模态框/遮罩，按钮禁用，Toast 显示成功，卡片内容局部刷新。
- 断网或 AI Key 缺失时，显示明确错误提示。
- 使用 `uv run python app.py` 启动，浏览器 DevTools Network 观察 `/analyze_health` 请求与响应。

**提交**（按文件拆分）：
```
feat(report): add dedicated loading modal and refine error feedback for health analysis

为综合健康分析生成增加专用 loading 模态框和明确错误反馈：
- 合并 analysis.js 到 index.html，减少请求
- 生成期间禁用重复点击
- 成功后局部刷新报告卡片，不再整页 reload
```
```
feat(api): return structured error types for health analysis failures

健康分析接口返回结构化错误类型：
- 429 配额耗尽返回 retry_after
- AI 不可用/数据不足/其他错误分类返回 error_type
```

---

### Phase 2：移动端适老化增强

**关键文件**：
- `static/css/main.css`
- `templates/index.html`

**具体修改**：

1. **CSS：新增适老化工具类与模式开关**
   - 在 `:root` 或 body 上增加 `.elderly-mode` 类，可通过设置项持久化。
   - 新增工具类：
     ```css
     .elderly-text-lg { font-size: 1.15rem !important; line-height: 1.8 !important; }
     .elderly-touch-56 { min-height: 56px !important; min-width: 56px !important; }
     .elderly-contrast { --md-text-primary: #0F172A; --md-text-secondary: #334155; --border-color: #94A3B8; }
     ```

2. **CSS：默认移动端再放大一档**
   - 在现有 `@media (max-width: 480px)` 中：
     - `.slot-name` 从 `0.85rem` 提升到 `1rem`
     - `.slot-time` 从 `0.8rem` 提升到 `0.95rem`
     - `.btn-sm` 从 `min-height: 44px` 提升到 `48px`
     - `.dropdown-item` padding 从 `10px 16px` 提升到 `14px 18px`

3. **HTML：设置入口与状态持久化**
   - 在设置面板（或用户下拉菜单）增加"大字/高对比模式"开关。
   - 开关状态保存到 `localStorage`，页面加载时读取并给 `body` 添加/移除 `.elderly-mode`。

4. **HTML：用药列表与下拉项**
   - 给药物管理、用药计划、下拉菜单项统一添加适老化类或确保字号不小于 `1rem`。
   - 将血糖槽位内 `.slot-name` / `.slot-time` 的样式迁移到 CSS 类，便于统一控制。

5. **交互反馈增强**
   - 保存、删除、生成报告等关键操作成功后，Toast 停留时间延长至 4 秒。
   - 关键按钮增加 `:active` 缩放反馈（已部分存在，统一补充）。

**验证**：
- 在手机模拟器（375px）和真机查看：
  - 血糖槽位文字清晰可读
  - 按钮触控目标 ≥ 48px，建议区域 ≥ 56px
  - 大字模式开关生效，无布局错乱

**提交**：
```
feat(ui): add elderly-friendly mobile mode with larger text and touch targets

增加移动端适老化模式：
- 新增 .elderly-mode 工具类
- 默认移动端槽位文字、按钮、下拉项再放大一档
- 设置面板增加大字/高对比开关并持久化到 localStorage
```

---

### Phase 3：关键安全与数据隔离修复

**关键文件**：
- `routes/api_meds.py`
- `routes/api_records.py`
- `user_manager.py`
- `utils/auth.py`（视情况而定）

**具体修改**：

1. **`routes/api_meds.py`**
   - 所有 `plan_id` 查询/更新/删除增加 `AND user_id = ?` 条件。
   - 统一从 `user_manager.get_current_user_id()` 获取当前用户。

2. **`routes/api_records.py`**
   - `add_record` 中移除对请求体 `user_id` 的信任，强制使用 session 当前用户。
   - 如确有家庭切换等需求，改为校验传入 `user_id` 必须等于当前 session 用户。

3. **`user_manager.py`**
   - `get_current_user_id()` 默认返回 `None` 而不是 `1`。
   - 修改所有调用方：未登录时返回 401/登录页，而不是操作默认用户。
   - 同步修改 `get_user_stats` 等默认参数 `user_id=1` 的函数签名。

4. **`utils/auth.py`**（如时间允许）
   - 明确区分必须 token 鉴权的 Agent 端点和必须 session 鉴权的普通端点。
   - 避免混合装饰器隐式 fallback。

**验证**：
- 单元测试：为 `api_meds.py` 增加越权访问测试（若测试框架存在）。
- 手动验证：用户 A 登录后无法通过修改 `plan_id` 访问用户 B 的用药方案。

**提交**（按文件拆分）：
```
fix(security): enforce user_id isolation on medication plan endpoints

用药方案相关接口增加 user_id 校验，防止横向越权。
```
```
fix(security): remove trusted user_id override in add_record

新增记录接口不再信任前端传入的 user_id，强制使用当前 session 用户。
```
```
fix(security): default get_current_user_id to None instead of 1

未登录时默认返回 None，避免操作默认用户数据。
```

---

## 验证策略汇总

| 阶段 | 验证方式 | 检查点 |
|------|---------|--------|
| Phase 1 | 浏览器点击测试 + DevTools Network | loading 出现、按钮禁用、成功局部刷新、错误分类提示 |
| Phase 2 | 手机模拟器 + 真机 | 触控目标 ≥ 48px、大字模式生效、布局无错乱 |
| Phase 3 | 手动越权测试 + 登录态测试 | 无法访问他人用药方案、未登录返回 401 |

## 提交与分支策略

- 使用 `/git-feature start` 创建 `feat/report-loading-and-elderly-mobile` 分支。
- 每文件一提交，跨文件改动拆分为多次提交。
- Commit message 采用 conventional commits，中英双语，英文块在前、中文块在后。
- 每阶段完成后本地验证，再进入下一阶段。

## 风险与回退

| 风险 | 回退方案 |
|------|---------|
| 报告 loading 模态框样式异常 | 恢复 `triggerManualAnalysis` 为 `showLoading: false`，仅保留 Toast |
| 大字模式导致布局撑破 | 移除 `.elderly-mode` 相关类，回退到默认样式 |
| `get_current_user_id` 改为 None 后其他路由异常 | git revert 对应提交，补全调用方判空 |

---

## 关键文件清单

- `templates/index.html`
- `static/js/analysis.js`
- `static/css/main.css`
- `routes/api_health.py`
- `services/health_service.py`
- `routes/api_meds.py`
- `routes/api_records.py`
- `user_manager.py`
- `utils/auth.py`
