# Sugar Bee main 分支 → Cloud Run + PostgreSQL 部署方案

> 目标：将 `main` 分支部署到 Google Cloud Run（香港 `asia-east2`），使用 Cloud SQL for PostgreSQL 作为生产数据库，同时保留 SQLite 用于本地开发和测试。

---

## 1. 架构概览

```
        国内家人手机/电脑
              │
              ▼
     Cloudflare DNS + CDN (免费)
              │
              ▼
     Cloud Run (asia-east2, 香港)
        Flask + Gunicorn
              │
      ┌───────┴───────┐
      │               │
  Cloud SQL      Cloud Storage
  (PostgreSQL)   (头像 / Garmin token)
```

### 双模式数据库

| 环境 | 数据库 | 说明 |
|------|--------|------|
| 本地开发 / pytest | SQLite | `SUGAR_BEE_DATABASE_URL` 未设置时默认 `sqlite:///glucose.db` |
| Cloud Run 生产 | PostgreSQL | 通过 Secret Manager 注入 `SUGAR_BEE_DATABASE_URL` |

代码层通过 `core.config.DB_TYPE` 自动推导数据库类型，并在 `utils/db.py` / `utils/sql_dialect.py` 中完成方言适配。

---

## 2. 前置准备

### 2.1 GCP 资源

| 资源 | 推荐配置 | 用途 |
|------|----------|------|
| Cloud Run | `asia-east2`（香港） | 运行 Flask 应用 |
| Cloud SQL | PostgreSQL 15+, `db-f1-micro` 或更高 | 生产数据库 |
| Cloud Storage | `asia-east2` 区域存储桶 | 头像、Garmin token 持久化 |
| Secret Manager | 3 个 secret | 数据库 URL、Flask 密钥、内部 API token |
| IAM Service Account | `sugar-bee@YOUR_PROJECT_ID.iam.gserviceaccount.com` | 应用身份认证 |

### 2.2 本地工具

```bash
# 确认已安装并认证
gcloud --version
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

---

## 3. Cloud SQL 配置

### 3.1 创建 PostgreSQL 实例

```bash
gcloud sql instances create sugar-bee-postgres \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=asia-east2 \
  --storage-type=HDD \
  --storage-size=10GB \
  --availability-type=zonal \
  --backup-start-time=02:00 \
  --maintenance-window-day=SUN \
  --maintenance-window-hour=3
```

### 3.2 创建数据库和用户

```bash
# 创建数据库
gcloud sql databases create sugar_bee --instance=sugar-bee-postgres

# 创建用户（自动生成密码并保存到 Secret Manager）
gcloud sql users create sugar_bee_user \
  --instance=sugar-bee-postgres \
  --password="$(openssl rand -base64 32)"
```

### 3.3 获取连接名称

```bash
gcloud sql instances describe sugar-bee-postgres --format='value(connectionName)'
# 输出示例：your-project-id:asia-east2:sugar-bee-postgres
```

---

## 4. Secret Manager 配置

### 4.1 数据库连接 URL

```bash
DB_PASSWORD="YOUR_DB_PASSWORD"
CONNECTION_NAME="YOUR_PROJECT_ID:asia-east2:sugar-bee-postgres"

# Cloud Run 通过 Unix socket 连接 Cloud SQL
DATABASE_URL="postgresql+psycopg2://sugar_bee_user:${DB_PASSWORD}@/sugar_bee?host=/cloudsql/${CONNECTION_NAME}"

echo -n "$DATABASE_URL" | gcloud secrets create SUGAR_BEE_DATABASE_URL --data-file=-
```

### 4.2 Flask Secret Key

```bash
openssl rand -hex 32 | gcloud secrets create SUGAR_BEE_SECRET_KEY --data-file=-
```

### 4.3 内部 API Token

```bash
openssl rand -hex 32 | gcloud secrets create SUGAR_BEE_INTERNAL_API_TOKEN --data-file=-
```

### 4.4 为 Cloud Run 服务账号授权读取 Secret

```bash
SERVICE_ACCOUNT="sugar-bee@YOUR_PROJECT_ID.iam.gserviceaccount.com"

gcloud secrets add-iam-policy-binding SUGAR_BEE_DATABASE_URL \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding SUGAR_BEE_SECRET_KEY \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding SUGAR_BEE_INTERNAL_API_TOKEN \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor"
```

---

## 5. Cloud Storage 配置

```bash
# 创建存储桶（用于头像和 Garmin token）
gcloud storage buckets create gs://sugar-bee-data --location=asia-east2

# 为服务账号授权
gcloud storage buckets add-iam-policy-binding gs://sugar-bee-data \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/storage.objectAdmin"
```

---

## 6. Cloud Run 部署

### 6.1 修改 `deploy/cloud-run.yaml`

将以下占位符替换为实际值：

- `YOUR_PROJECT_ID`：GCP 项目 ID
- `YOUR_INSTANCE_NAME`：Cloud SQL 实例名称（如 `sugar-bee-postgres`）
- 镜像地址（可选，也可通过命令行覆盖）

### 6.2 构建并部署

```bash
# 构建镜像
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/sugar-bee

# 使用 YAML 部署
gcloud run services replace deploy/cloud-run.yaml --region=asia-east2

# 或命令行部署（等效）
gcloud run deploy sugar-bee \
  --image gcr.io/YOUR_PROJECT_ID/sugar-bee \
  --platform managed \
  --region asia-east2 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 1 \
  --set-env-vars="FLASK_ENV=production,GCS_BUCKET_NAME=sugar-bee-data" \
  --update-secrets="SUGAR_BEE_DATABASE_URL=SUGAR_BEE_DATABASE_URL:latest,SECRET_KEY=SUGAR_BEE_SECRET_KEY:latest,INTERNAL_API_TOKEN=SUGAR_BEE_INTERNAL_API_TOKEN:latest" \
  --add-cloudsql-instances="YOUR_PROJECT_ID:asia-east2:sugar-bee-postgres" \
  --service-account sugar-bee@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### 6.3 验证部署

```bash
# 获取服务 URL
SERVICE_URL=$(gcloud run services describe sugar-bee --region=asia-east2 --format='value(status.url)')

# 健康检查
curl "${SERVICE_URL}/health"

# 内部备份端点（PostgreSQL 下应返回 backed_up: false）
curl -X POST "${SERVICE_URL}/internal/backup" \
  -H "Authorization: Bearer $(gcloud secrets versions access latest --secret=SUGAR_BEE_INTERNAL_API_TOKEN)"
```

---

## 7. 数据迁移（可选）

如需将本地 SQLite 数据迁移到 Cloud SQL PostgreSQL：

```bash
# 1. 本地导出为 CSV/SQL
uv run python -c "
import sqlite3, csv
conn = sqlite3.connect('glucose.db')
# 按表导出...
"

# 2. 通过 Cloud SQL Auth Proxy 连接并导入
gcloud sql connect sugar-bee-postgres --user=sugar_bee_user --database=sugar_bee
```

> 注意：PostgreSQL schema 由 `utils/db.py` 的 `init_db()` 自动创建，首次启动时会执行幂等 CREATE TABLE。

---

## 8. 定时任务（Cloud Scheduler）

### 8.1 Garmin 同步

```bash
gcloud scheduler jobs create http sugar-bee-garmin-sync \
  --schedule="0 */2 * * *" \
  --uri="${SERVICE_URL}/internal/garmin-sync" \
  --http-method=POST \
  --headers="Authorization=Bearer $(gcloud secrets versions access latest --secret=SUGAR_BEE_INTERNAL_API_TOKEN)" \
  --time-zone="Asia/Shanghai" \
  --location=asia-east2
```

### 8.2 数据库备份（SQLite 模式才需要）

PostgreSQL 模式下 `/internal/backup` 会返回 `backed_up: false`，因为数据由 Cloud SQL 自动备份。无需再创建 Cloud Scheduler 备份作业。

---

## 9. 费用估算（香港 asia-east2）

### 场景 A：按需启停

| 项目 | 用量 | 费用 |
|------|------|------|
| Cloud Run | min=0，每天活跃 2h | 免费额度内 |
| Cloud SQL | db-f1-micro，按需 | ~$7-9/月 |
| Cloud Storage | < 1 GB | 免费额度内 |
| Secret Manager | 3 个 secret | 免费额度内 |
| **月合计** | | **~$7-9** |

### 场景 B：24h 运行

| 项目 | 用量 | 费用 |
|------|------|------|
| Cloud Run | min=1，24h×30 天 | ~$7-10/月 |
| Cloud SQL | db-f1-micro，24h×30 天 | ~$7-9/月 |
| Cloud Storage | < 1 GB | 免费额度内 |
| **月合计** | | **~$14-19** |

---

## 10. 回滚方案

### 10.1 回滚到上一个 Cloud Run Revision

```bash
gcloud run services update-traffic sugar-bee \
  --to-revisions="PREVIOUS_REVISION=100" \
  --region=asia-east2
```

### 10.2 切换到 SQLite 模式（紧急回退）

将 `SUGAR_BEE_DATABASE_URL` 更新为空或 SQLite 路径，并移除 `--add-cloudsql-instances`：

```bash
gcloud run deploy sugar-bee \
  --image gcr.io/YOUR_PROJECT_ID/sugar-bee \
  --region asia-east2 \
  --set-env-vars="SUGAR_BEE_DATABASE_URL=sqlite:////tmp/glucose.db" \
  --remove-cloudsql-instances="YOUR_PROJECT_ID:asia-east2:sugar-bee-postgres"
```

> 注意：回退到 SQLite 后需重新启用 GCS 数据库备份/恢复逻辑。

---

## 11. 环境变量清单

| 变量 | 来源 | 必需 | 说明 |
|------|------|------|------|
| `PORT` | Cloud Run 自动注入 | 是 | 应用监听端口 |
| `FLASK_ENV` | 环境变量 | 是 | 设为 `production` |
| `SUGAR_BEE_DATABASE_URL` | Secret Manager | 是 | PostgreSQL 连接 URL（生产） |
| `SECRET_KEY` | Secret Manager | 是 | Flask session 密钥 |
| `INTERNAL_API_TOKEN` | Secret Manager | 是 | 内部 API 鉴权 |
| `GCS_BUCKET_NAME` | 环境变量 | 是 | GCS 存储桶（头像/Garmin token） |
| `GCP_PROJECT_ID` | 环境变量 | 否 | GCP 项目 ID |
| `GEMINI_API_KEY` | 环境变量/Secret | 推荐 | AI 功能 |
| `GARMIN_EMAIL` | 环境变量 | 可选 | Garmin 同步 |
| `GARMIN_USER_ID` | 环境变量 | 可选 | Garmin 同步目标用户 |
| `GARMIN_TOKEN_DIR` | 环境变量 | 否 | Garmin token 目录 |

---

## 12. 相关文档

- [`docs/plans/260612-cloud-run-deployment-plan.md`](./260612-cloud-run-deployment-plan.md) — 旧版 SQLite + GCS 方案（已废弃）
- [`utils/db.py`](../../utils/db.py) — 双模式数据库连接层
- [`utils/sql_dialect.py`](../../utils/sql_dialect.py) — SQL 方言适配助手
- [`core/config.py`](../../core/config.py) — 数据库配置
