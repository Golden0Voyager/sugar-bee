# 蜜蜂控糖 - 开发笔记

> 本文件整合了项目开发过程中的所有技术文档，供后续维护参考。

---

## 目录

1. [更新日志](#更新日志)
2. [多用户架构](#多用户架构)
3. [数据库迁移](#数据库迁移)
4. [UI 优化记录](#ui-优化记录)
5. [数据范围说明](#数据范围说明)
6. [技术栈](#技术栈)
7. [未来计划](#未来计划)

---

## 更新日志

### v2.3 (2026-06-16)

- **综合健康报告生成 UX 优化**
  - 增加专用 loading 模态框，生成期间禁用重复点击
  - 成功后局部刷新报告卡片，失败后按网络/AI 不可用/配额耗尽/分析失败分类提示
  - 后端返回结构化错误类型（`error_type` + `details`）
- **移动端适老化增强**
  - 设置面板增加"大字/高对比模式"开关，状态持久化到 `localStorage`
  - 移动端槽位文字、按钮、下拉项触控目标进一步放大
  - 新增 `.elderly-mode` 高对比/大字体工具类
- **关键安全修复**
  - 用药方案接口（`api_meds.py`）所有 `plan_id` 操作增加 `AND user_id = ?` 隔离
  - 新增记录接口（`api_records.py`）不再信任前端传入的 `user_id`，强制使用 session 当前用户
  - `user_manager.get_current_user_id()` 默认返回 `None` 而非硬编码 `1`
- **前端统一与可维护性**
  - CSS 去重、提取变量与基础组件类
  - 清理 380+ 处内联样式
  - 合并重复 JS 逻辑（配额模态框、健康分析选项等）
  - 血糖槽位事件委托到容器，减少重绘监听器
- **AI 调用链与兼容性**
  - 接入 ModelScope / SenseNova / DeepSeek 等模型作为解析与对话兜底
  - 移除 Dashscope 相关配置，简化对话流
  - 修复 SQLite `%s` 占位符兼容性问题，统一使用 `?`

### v2.2 (2026-02-08)
- 新增体重/BMI 追踪功能（数据库、路由、表单、图表、时间线、AI解析）
- BMI 使用中国标准分类（偏瘦<18.5, 正常18.5-24, 超重24-28, 肥胖>=28）
- 修复查找重复数据前端错误（`result.data` 兼容处理）
- 添加按钮防抖机制，防止重复提交
- 修复右上角默认头像图标不显示问题

### v2.1 (2026-01-07)
- 合并两个独立用户按钮为统一下拉菜单
- 新增用户信息卡片、功能分组（切换用户/个人管理/数据管理）
- Material Design 风格阴影、交互动画

### v2.0 (2026-01-06)
- 健康分析：下拉菜单改为模态框弹窗，卡片式选项
- 新增"今天"选项（1天数据快速分析）
- Toast 通知系统替代 alert 弹窗

### v1.0 (2026-01-05及之前)
- 血糖/血压/运动/饮食/药物 核心功能
- 多用户支持（无密码切换）
- AI 健康分析（Gemini）
- FPG 智能预测

---

## 多用户架构

### 设计选择：MVP 无密码方案
- 适用场景：家庭内部 2-3 人，信任环境
- 用户切换：下拉选择，无需登录密码
- 数据隔离：每条记录关联 `user_id`，查询全部带用户过滤
- 模块化配置：每用户可启用不同功能模块

### 数据库表结构
- `app_users`: 用户基本信息（username, display_name, avatar, is_active）
- `user_profiles`: 用户配置（身高/体重/性别/出生年/启用模块/默认餐食/目标范围）
- `records`: 所有健康记录，通过 `user_id` 列关联用户
- `medication_plans`: 用药计划，通过 `user_id` 隔离
- `health_analyses`: 健康分析报告，按用户独立生成

### 添加新用户
```sql
INSERT INTO app_users (username, display_name, is_active)
VALUES ('newuser', '新用户', 1);

INSERT INTO user_profiles (user_id, name, birth_year, height, weight, gender, enabled_modules)
VALUES (last_insert_rowid(), '新用户', 1990, 175, 70, 'male',
        '["glucose", "blood_pressure", "exercise", "diet", "weight"]');
```

### 安全注意事项
- MVP 版本无密码保护，仅适合局域网家庭使用
- 不要暴露到公网
- 建议定期备份数据库：`cp glucose.db glucose.db.backup_$(date +%Y%m%d)`

---

## 数据库迁移

### 迁移脚本 (migrate_multiuser.sql)

应用启动时 `init_db()` 自动执行迁移，使用 `ALTER TABLE ... ADD COLUMN` + `try/except` 模式保证幂等性。

关键迁移列：
- `records.user_id` — 用户关联（默认值 1）
- `records.systolic_pressure / diastolic_pressure / pulse_rate` — 血压数据
- `records.weight / bmi` — 体重/BMI 数据
- `records.is_predicted` — FPG 预测标记

初始迁移 SQL（仅首次部署需要）：
```sql
CREATE TABLE IF NOT EXISTS app_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    avatar TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES app_users(id),
    name TEXT, birth_year INTEGER, height INTEGER, weight INTEGER,
    gender TEXT, default_meals TEXT, target_ranges TEXT,
    enabled_modules TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 性能索引
CREATE INDEX IF NOT EXISTS idx_records_user_id ON records(user_id);
CREATE INDEX IF NOT EXISTS idx_medication_user_id ON medication_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_health_analyses_user_id ON health_analyses(user_id);
```

---

## UI 优化记录

### 健康分析交互 (v2.0)

**核心改动**：下拉菜单 → 模态框弹窗
- 4个卡片选项：今天(1天)、近7天(推荐)、近14天、近30天
- Toast 通知系统：成功(绿)、失败(红)、加载(蓝)，自动3秒消失
- 卡片 Hover 上浮效果 (`translateY(-4px)`)
- 推荐选项紫色边框标识

**关键函数**：
- `selectAnalysisOption(days)` — 关闭模态框 → 显示加载 Toast → 触发分析
- `triggerManualAnalysis(days)` — async/await API 调用
- `showToast(message, type)` — 通用轻提示

### 用户下拉菜单合并 (v2.1)

**核心改动**：2个按钮合并为1个统一下拉菜单
- 用户信息卡片（头像48px + 姓名 + 基本资料）
- 功能分组：切换用户 / 个人管理 / 数据管理
- 按钮 Hover：背景变蓝 + 头像反色
- 菜单项 Hover：背景变蓝 + 向右移动 (`padding-left: 1.25rem`)

**关键 CSS**：
- `#unifiedUserDropdown` — 统一按钮样式
- `.dropdown-menu` — Material Design 阴影，圆角 12px
- `#userListContainer` — 用户列表容器，最大高度 200px，美化滚动条

---

## 数据范围说明

### 健康分析数据查询
- SQL 条件：`timestamp > datetime('now', '-N days')`
- 包含当天数据（从N天前此刻到现在）
- 使用 `>` 不包含边界时刻
- 界面显示"基于近N天数据（含当天）"

### 收集的数据类型
1. **血糖**：空腹/餐后，排除预测值 (`is_predicted = 0`)
2. **血压**：收缩压/舒张压/脉搏
3. **运动**：距离/时长/心率/卡路里
4. **饮食**：卡路里/饮食分析
5. **用药**：当前有效用药计划
6. **体重**：体重(kg)/BMI

---

## 技术栈

### 后端
- Python 3.x + Flask（Blueprint 模块化路由）
- SQLite 数据库（生产环境建议启用 WAL）
- AI 提供商链：Google Gemini / ZhipuAI / DeepSeek / SenseNova / ModelScope，按任务类型自动降级

### 前端
- Bootstrap 5 + Bootstrap Icons
- Chart.js（趋势图表）+ chartjs-plugin-annotation（参考线）
- FullCalendar（月历组件）
- Marked.js（Markdown 解析）
- html2canvas + jsPDF（PDF 导出）

### 设计规范
- Material Design 风格
- CSS3 Transitions 动画
- 响应式布局

---

## 未来计划

具体优先级与待办事项见 [`plans/260616-next-steps.md`](plans/260616-next-steps.md)。

当前重点方向：

- **安全与稳定性**：SQLite 并发访问保护、AI 调用超时、解析错误显式处理、Agent Token 鉴权明确化
- **交互与性能**：时间线渲染统一、图表工厂化、空元素保护、长按槽位状态竞态消除
- **工程化**：统一错误处理、移除默认 `user_id`、生产环境隐藏堆栈、头像上传校验
- **适老化后续**：底部固定保存按钮、操作震动反馈、更高对比度选项

---

**项目名称**：蜜蜂控糖 (Manage Diligently, Live Sweetly)
**当前版本**：v2.3
**最后更新**：2026-06-16
