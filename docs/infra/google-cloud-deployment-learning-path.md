# Sugar Bee GCP 部署学习路径

> 目标：SQLite → PostgreSQL 迁移 + Cloud Run + Cloud SQL 部署
> 预算：$300 GCP 赠金覆盖
> 最后更新：2026-06-16

---

## 关键决策记录

| 时间 | 决策 | 说明 |
|------|------|------|
| 2026-06-12 | 原计划：**SQLite + GCS 持久化** | 保留 SQLite 数据库，通过 Cloud Storage 备份/恢复解决 Cloud Run 临时文件系统问题 |
| 2026-06-16 12:26 | 已编码 GCS 方案 | GCS sync service、备份/恢复逻辑、内部 API 端点均已提交代码 |
| **2026-06-16 13:59** | **撤销 GCS 方案，改为 PostgreSQL + Cloud SQL** | GCS 方案过于复杂且不可靠，当天下午转向 PostgreSQL |

### 为什么放弃 SQLite + GCS

| 问题 | SQLite + GCS | PostgreSQL + Cloud SQL |
|------|-------------|----------------------|
| **数据持久性** | 依赖 GCS 每5分钟备份 + 启动时恢复，窗口期数据可能丢失 | 数据库天然持久，无需额外备份逻辑 |
| **时序风险** | 启动时先恢复 GCS 还是先跑迁移？顺序错了就丢数据或丢迁移 | 无此问题 |
| **并发写入** | SQLite 写锁，只能跑 1 个 Gunicorn worker | 支持多 worker 并发 |
| **代码复杂度** | 需要 `gcs_sync.py`、`wsgi.py` 恢复逻辑、`api_internal.py` 端点、Cloud Scheduler | 原生 `psycopg2` 连接池，`utils/db.py` 统一管理 |
| **运维成本** | 备份是否成功？GCS 文件是否损坏？需要额外监控 | Cloud SQL 全托管，自动备份、PITR |

**结论**：GCS 方案本质上是"用胶带把 SQLite 粘到 Cloud Run 上"，复杂且脆弱。直接上 Cloud SQL 虽然每月多 ~$8.78，但 $300 赠金完全覆盖，且大幅降低维护成本。

**注意**：GCS 相关代码（`services/gcs_sync.py`、`routes/api_internal.py`、`deploy/cloud-run.yaml`）仍保留在仓库中作为参考，但不再是部署方案的一部分。

---

## Phase 0：准备环境 — ✅ 已完成

- [x] **GCP 项目创建与结算**（已有项目 `project-c0560c79-7c6a-4f31-a11`）
- [x] **gcloud CLI 安装与配置**（`gcloud init` + 登录 `yhn0535@gmail.com`）
- [x] **开启 API**（Cloud Run / Cloud SQL / Cloud Build / Secret Manager / Cloud Scheduler / Cloud Storage）
- [ ] **Docker 基础**（暂未系统学习，部署时补充）
- [x] **本地 PostgreSQL**（Homebrew 安装 PostgreSQL 18，创建 `sugar_bee` 数据库）

---

## Phase 1：代码改造 — ✅ 源码迁移完成，测试待更新

### 已完成（源码层）：

- [x] **`core/config.py`** — 新增 `DATABASE_URL` + `DB_TYPE` 配置项
- [x] **`utils/db.py`** — PostgreSQL 连接池（`ThreadedConnectionPool`）+ `_CompatRow`（兼容 `row[0]` 和 `row['name']`）
- [x] **`user_manager.py`** — 全部 22 处 `sqlite3.connect` 替换为 psycopg2
- [x] **所有路由文件** — SQL 占位符 `?` → `%s`
- [x] **SQLite 特有函数替换**：`datetime()` → `INTERVAL` 语法，`strftime()` → `TO_CHAR()` / `EXTRACT(EPOCH)`
- [x] **`c.lastrowid` 替换**：改为 `INSERT ... RETURNING id`
- [x] **`INSERT OR IGNORE` 替换**：改为 `INSERT ... ON CONFLICT DO NOTHING`
- [x] **验证**：Flask 应用成功启动连接 PostgreSQL，9 张表全部创建

### 测试状态（待更新）：
902 通过，128 失败。失败原因全是测试文件 mock `sqlite3.connect`，跟生产代码无关。

### 待续：
- [ ] **更新测试文件** — 把 mock `sqlite3.connect` 改为 mock 新的数据库层
- [ ] **Alembic 迁移** — 生成初始迁移文件
- [x] **数据迁移** — `scripts/migrate_data.py` 迁移了 14,462 条记录到 Cloud SQL（香港）

---

## Phase 2：IAM 与安全 — ✅ 已完成

- [x] **服务账号（Service Account）**
  - 创建 `sugar-bee-app`
- [x] **IAM 角色**
  - Cloud SQL Client ✅ / Secret Manager Secret Accessor ✅ / Storage Object Admin ✅
- [x] **Secret Manager**
  - `SECRET_KEY` 已存储
  - Cloud Run 挂载将在 Phase 4 配置

---

## Phase 3：Cloud SQL — ✅ 已完成

- [x] **Cloud SQL 概念** — 微型实例 db-f1-micro，Enterprise 版，$8.78/月
- [x] **创建 Cloud SQL 实例**（`sugar-bee-db-hk`, POSTGRES_17, asia-east2 香港）
- [x] **创建数据库和用户**（`sugar_bee` / `sugar_bee_app`）
- [x] **连接验证** — Cloud SQL Auth Proxy 通过，应用正常连接
- [x] **Init DB** — 9 张表在云端创建成功

---

## Phase 4：Cloud Run — ✅ 已完成（首次部署后删除，重新部署到香港）

- [x] **部署到 asia-east2（香港）** — 已成功部署
- [x] **环境变量配置** — `SUGAR_BEE_DATABASE_URL` 含新的 Cloud SQL 连接名
- [x] **验证** — 登录、数据查询、健康检查全部正常
- [ ] **Cloudflare CDN / 自定义域名** — 待实施

---

## Phase 5：构建部署（本地/Cloud Shell Docker build）— ✅ 已完成

> **决策：** 不使用 Cloud Build（每次构建约 $0.5-1.5）。改用本地 Docker 构建 + push 到 Artifact Registry，`gcloud run deploy` 不变。国内网络不通时切到 Cloud Shell 执行同样操作，也免费。

- [x] **Artifact Registry** — 仓库已存在（`cloud-run-source-deploy`）
- [x] **本地 Docker 构建** — 替代 `gcloud builds submit`
- [x] **Cloud Shell 回退方案** — 网络受限时使用

---

## Phase 6：持久化存储与定时任务 — 待开始

- [ ] **Cloud Storage**（头像、备份文件）
- [ ] **Cloud Scheduler**（定时备份、Garmin 同步）
- [ ] **移除本地 `threading.Timer`** 改用 Cloud Scheduler

---

## Phase 7：国内访问优化 — 待开始

- [ ] **CDN 选项**（Cloudflare / 阿里云 CDN）
- [ ] **域名与 ICP 备案**
- [ ] **Cloud Run 自定义域名**

---

## 当前总进度

```
Phase 0 ████████████████████ 100%  ❐ gcloud, API, PostgreSQL
Phase 1 ████████████████████ 100%  ❐ 源码迁移（测试除外）
Phase 2 ████████████████████ 100%  ❐ IAM + Secret Manager
Phase 3 ████████████████████ 100%  ❐ Cloud SQL 创建 + 连接
Phase 4 ████████████████████ 100%  ❐ 香港部署成功（待 CDN）
Phase 5 ████████████████████ 100%  ❐ 本地 Docker 构建取代 Cloud Build
Phase 6 ░░░░░░░░░░░░░░░░░░░░   0%
Phase 7 ░░░░░░░░░░░░░░░░░░░░   0%

---

## 关键信息（交接用）

| 项目 | 值 |
|------|-----|
| GCP Project ID | `project-c0560c79-7c6a-4f31-a11` |
| GCP Project Number | `670879142538` |
| Billing Account | `011055-8FD4BF-3618BA` |
| Cloud Run 区域 | `asia-east2`（香港） |
| Cloud SQL 实例 | `sugar-bee-db-hk`（asia-east2, db-f1-micro） |
| DB 名称 | `sugar_bee` |
| DB 用户 | `sugar_bee_app` |
| DB 密码 | `REDACTED` |
| Service Account | `sugar-bee-app@project-c0560c79-7c6a-4f31-a11.iam.gserviceaccount.com` |
| Git 分支 | `feat/cloud-run-deploy` |
| GitHub | `https://github.com/Golden0Voyager/sugar-bee` |

---

## Cloud Shell 操作备忘

所有 `gcloud` 命令必须在 [Cloud Shell](https://shell.cloud.google.com) 中执行（国内网络下 gcloud CLI 被 GFW 阻断）。

每次重连 Cloud Shell 后：
1. `gcloud config set project project-c0560c79-7c6a-4f31-a11`
2. `cd ~/sugar-bee && git pull origin feat/cloud-run-deploy`

### 完整部署命令（一键复制）

```bash
# ============================================
# 方案 A：本地构建（推荐，快且免费）
# ============================================

# 0. 设置项目 & 认证 Docker
gcloud config set project project-c0560c79-7c6a-4f31-a11
gcloud auth configure-docker asia-east2-docker.pkg.dev

# 1. 镜像标签
IMAGE=asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/cloud-run-source-deploy/sugar-bee:latest

# 2. 本地构建并推送（约 30s-2min，替代 gcloud builds submit，省钱）
docker build -t $IMAGE .
docker push $IMAGE

# 3. 部署到 Cloud Run（香港）
gcloud run deploy sugar-bee \
  --image=$IMAGE \
  --region=asia-east2 \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=1 \
  --concurrency=1 \
  --memory=1Gi \
  --cpu=1 \
  --add-cloudsql-instances=project-c0560c79-7c6a-4f31-a11:asia-east2:sugar-bee-db-hk \
  --service-account=sugar-bee-app@project-c0560c79-7c6a-4f31-a11.iam.gserviceaccount.com \
  --set-env-vars="SUGAR_BEE_DATABASE_URL=postgresql://sugar_bee_app:REDACTED@localhost:5432/sugar_bee?host=/cloudsql/project-c0560c79-7c6a-4f31-a11:asia-east2:sugar-bee-db-hk"

# ============================================
# 方案 B：Cloud Shell 构建（国内网络不通时的回退）
# 在 https://shell.cloud.google.com 中执行
# ============================================

gcloud config set project project-c0560c79-7c6a-4f31-a11
cd ~/sugar-bee && git pull origin feat/cloud-run-deploy

IMAGE=asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/cloud-run-source-deploy/sugar-bee:latest
docker build -t $IMAGE .
docker push $IMAGE

gcloud run deploy sugar-bee --image=$IMAGE --region=asia-east2 \
  --allow-unauthenticated --min-instances=0 --max-instances=1 \
  --concurrency=1 --memory=1Gi --cpu=1 \
  --add-cloudsql-instances=project-c0560c79-7c6a-4f31-a11:asia-east2:sugar-bee-db-hk \
  --service-account=sugar-bee-app@project-c0560c79-7c6a-4f31-a11.iam.gserviceaccount.com \
  --set-env-vars="SUGAR_BEE_DATABASE_URL=postgresql://sugar_bee_app:REDACTED@localhost:5432/sugar_bee?host=/cloudsql/project-c0560c79-7c6a-4f31-a11:asia-east2:sugar-bee-db-hk"
```

### 验证

部署成功后访问：`https://sugar-bee-670879142538.asia-east2.run.app`

---

## ⚠️ 安全提示

本文档包含数据库密码等敏感信息，仅用于交接。交接完成后建议：
- 将密码存入 Secret Manager，文档中只保留引用
- 或将本文档设为私有访问

---

## 2026-06-20 部署踩坑与修正

### 1. 实际镜像仓库与文档不一致

线上服务当前使用的镜像仓库是：

```
asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee
```

文档中写的 `cloud-run-source-deploy/sugar-bee` 是旧仓库。**后续部署请推送到 `sugar-bee/sugar-bee`**，否则相当于新建一条镜像流，Cloud Run 不会自动继承历史配置。

### 2. 本地 Docker 构建被代理 fake-ip 破坏 → 改用 Cloud Build

在 Apple Silicon (arm64) 上用 `--platform linux/amd64` 本地交叉构建时，Debian apt 阶段报错：

```
Connection failed [IP: 198.18.0.35 80]
404  Not Found [IP: 198.18.0.35 80]
```

`198.18.0.35` 是 Clash/Surge 等代理 **fake-ip 模式** 的虚拟地址，Docker 构建容器无法解析，导致 `apt-get update` 失败。

**解决**：改由 Cloud Build 在 Google 机器上原生 amd64 构建并推送，单次约 2 分钟，费用基本在免费额度内。

```bash
IMAGE=asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee:latest
gcloud builds submit --tag "$IMAGE" --region=asia-east2 .
```

已新增 `.gcloudignore`，确保 `*.db`、`.env`、`user_config.json`、`.garmin_tokens/`、`backups/` 等真实数据/凭据不会上传到 Cloud Build。

### 3. gcloud run deploy 被 `serviceusage.googleapis.com` SSL 中断

直接执行 `gcloud run deploy` 时，gcloud 会先访问 `serviceusage.googleapis.com` 检查 Cloud Run API 是否启用，该端点在代理下反复报：

```
SSLError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol
```

**解决**：跳过 API 启用预检（服务早已启用）：

```bash
CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API=false \
  gcloud run deploy sugar-bee --image="$IMAGE" --region=asia-east2 --quiet
```

### 4. 只换镜像，保留全部配置

首次部署文档使用 `--set-env-vars="SUGAR_BEE_DATABASE_URL=..."` 会：
- 把 Secret Manager 引用覆盖成明文值
- 导致真实密码出现在 Cloud Run 配置中

**正确做法**：只更新镜像，环境变量、Secret、Cloud SQL、service account 全部保留：

```bash
CLOUDSDK_CORE_SHOULD_PROMPT_TO_ENABLE_API=false \
  gcloud run deploy sugar-bee \
    --image="asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee@sha256:⟨新摘要⟩" \
    --region=asia-east2 \
    --quiet
```

### 5. Cloud SQL 备份与 Cloud Scheduler 已启用

- **Cloud SQL 备份**：已开启，每天 04:00（Asia/Shanghai），保留 7 天。
- **Cloud Scheduler / Garmin 同步**：`garmin-sync` 作业，cron `0 3,9,15,21 * * *`（北京时间 09:00、15:00、21:00、03:00），每 6 小时一次，调用 `/internal/garmin-sync`。

### 6. 创建用户失败：PostgreSQL `cursor.lastrowid` 兼容性 bug

**现象**：部署后登录、查询、添加记录都正常，但"创建新用户"报错。

**根因**：`UserManager.create_user` 使用 `INSERT INTO app_users ...` 后直接读取 `c.lastrowid`。psycopg2 的 `cursor.lastrowid` 不支持（返回 0/None），导致 `user_profiles.user_id` 写入非法值，触发主键/外键约束失败。

**修复**：所有需要获取新主键的 INSERT 统一加 `RETURNING id`，依赖 `utils/db.py` 中的 `_CompatCursor` / `CursorWrapper` 把 `c.lastrowid` 填对。

涉及文件：
- `user_manager.py`（创建用户）
- `mcp_adapter/server.py`（MCP 写入记录）
- `services/health_service.py`（保存健康分析）

已在本地通过 1151 个测试验证。

### 7. 当前服务信息（2026-06-20）

| 项 | 值 |
|---|---|
| 当前 revision | `sugar-bee-00034-zb8` |
| 服务 URL | `https://sugar-bee-670879142538.asia-east2.run.app` |
| 镜像仓库 | `asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee` |
| 流量 | 100% 切到最新 revision |
| 环境变量 | 11 个全部保留（`SUGAR_BEE_DATABASE_URL` 仍以 Secret 形式挂载） |
| DB 用户 | `sugar_bee_app`（密码维持原值，未轮换） |

