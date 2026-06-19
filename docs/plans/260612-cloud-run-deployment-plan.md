# Sugar Bee → Google Cloud Run 部署方案

> ~~最后更新：2026-06-12~~
> **⚠️ 此方案已被废弃** — 详见下方说明

> ~~目标：将 Sugar Bee（蜜蜂控糖）部署到 Google Cloud Run，供国内家人访问~~

---

## ⛔ 重要：此方案已被废弃（2026-06-16）

~~本计划描述的是 **SQLite + GCS（Cloud Storage）持久化**方案，已被以下方案取代：~~

**实际采用的方案：SQLite → PostgreSQL 迁移 + Cloud SQL + Cloud Run**

| 旧方案（已废弃） | 新方案（已实施） |
|--|--|
| SQLite 本地文件数据库 | PostgreSQL（Cloud SQL db-f1-micro） |
| GCS 定期备份/恢复做持久化 | Cloud SQL 全托管，数据天然持久 |
| 每 5 分钟 GCS 上传，启动时从 GCS 恢复 | 直接连接 Cloud SQL，无需额外的备份/恢复逻辑 |
| 仅支持单 worker（SQLite 写锁） | 支持多 worker 并发 |
| `services/gcs_sync.py` + `wsgi.py` 恢复逻辑 | `utils/db.py` 双模式（SQLite 本地 / PostgreSQL 生产） |

**废弃原因**：SQLite 在 Cloud Run 的临时文件系统上不可靠，GCS 备份/恢复胶水代码复杂且易出错。改用 Cloud SQL 后数据安全、并发支持、运维成本都大幅改善。详见 [`docs/infra/google-cloud-deployment-learning-path.md`](../../../docs/infra/google-cloud-deployment-learning-path.md)。

**注意**：以下内容仅作为历史参考保留，代码中 GCS 相关实现（`services/gcs_sync.py`、`routes/api_internal.py` 等）已不再使用。

---



## 目录

1. [架构概览](#1-架构概览)
2. [核心挑战与解决方案](#2-核心挑战与解决方案)
3. [代码审查发现的问题](#3-代码审查发现的问题)
4. [需要改动的文件](#4-需要改动的文件)
5. [Cloud Run 部署配置](#5-cloud-run-部署配置)
6. [国内访问方案](#6-国内访问方案)
7. [分步实施计划](#7-分步实施计划)
8. [费用估算](#8-费用估算)
9. [回滚方案](#9-回滚方案)

---

## 1. 架构概览

### 目标架构

```
        国内家人手机/电脑
              │
              ▼
     Cloudflare DNS + CDN (免费)
              │
              ▼
     Cloud Run (asia-east1, 单实例)
        Flask + Gunicorn
              │
      ┌───────┼───────┐
      │       │       │
  SQLite   GCS 存储桶  Cloud Scheduler
 (本地盘)  (持久化)    (定时任务)
```

### 为什么选这个方案

| 决策 | 选择 | 原因 |
|------|------|------|
| 数据库 | SQLite 保留 + GCS 备份 | 避免 SQLite→PostgreSQL 迁移，项目 8 张表全部用原生 SQL |
| 存储 | Cloud Storage | 头像、备份、Garmin token 持久化 |
| 访问 | Cloudflare 自定义域名 | 国内直连 `run.app` 不可靠，Cloudflare 免费且国内可达 |
| 实例模式 | 按需启停（min=0） | 免费额度有限，空闲时缩容到 0 |
| 区域 | `asia-east1`（台湾） | 离国内最近，延迟最低 |

---

## 2. 核心挑战与解决方案

### 挑战 1：SQLite 文件数据库

**问题**：Cloud Run 是无状态的。实例重启/新部署时，本地文件系统会被清空，`glucose.db` 会丢失。

**解决方案**：定期备份（每 5 分钟）+ 启动时恢复 + 停机时备份三重保障。

```python
# 启动时恢复（在 wsgi.py 中，init_db() 之前执行）
def restore_db_from_gcs():
    """从 GCS 下载最新的数据库备份到本地"""
    bucket = get_gcs_bucket()
    blobs = list(bucket.list_blobs(prefix="db/glucose"))
    if not blobs:
        return  # 首次运行，使用 init_db() 创建空库
    latest = max(blobs, key=lambda b: b.time_created)
    latest.download_to_filename(DB_NAME)
    print(f"[GCS] 从 {latest.name} 恢复数据库")

# 定期备份（每 5 分钟，后台线程）
def periodic_gcs_backup():
    """定期将本地数据库上传到 GCS"""
    if not os.environ.get('GCS_BUCKET_NAME'):
        return  # 非 Cloud Run 环境跳过
    bucket = get_gcs_bucket()
    today = datetime.date.today().strftime('%Y%m%d')
    blob = bucket.blob(f"db/glucose_{today}.db")
    blob.upload_from_filename(DB_NAME)
    print(f"[GCS] 数据库已备份到 db/glucose_{today}.db")
```

### 挑战 2：文件存储

**问题**：以下文件在 Cloud Run 上是临时的，重启即丢失：

| 文件 | 用途 |
|------|------|
| `static/avatars/` | 用户头像 |
| `backups/` | 自动备份 |
| `.garmin_tokens/garmin_tokens.json` | Garmin 登录 token |

**解决方案**：用 GCS 存储桶统一管理，启动时同步到本地。

```
GCS 存储桶结构：
sugar-bee-data/
├── db/
│   ├── glucose.db            # 当前数据库
│   └── glucose_20260612.db   # 定期备份
├── backups/
│   └── glucose_auto_*.db     # 每日自动备份（保留 30 天）
├── avatars/
│   ├── avatar_1234.png
│   └── avatar_5678.jpg
└── garmin_tokens/
    └── garmin_tokens.json
```

### 挑战 3：后台定时任务

**问题**：`threading.Timer` 做的自动备份和 Garmin 同步，在 Cloud Run 上不可靠——实例可能随时被回收。

**解决方案**：保留 threading.Timer 作为运行时机制，Cloud Scheduler 作为补充保障。

| 任务 | 运行时机制 | Cloud Scheduler 补充 |
|------|-----------|---------------------|
| 数据库备份 | `auto_backup()` 每 24h 本地 + `periodic_gcs_backup()` 每 5 分钟上传 GCS | 每天 2:00 触发 `/internal/backup` |
| Garmin 同步 | `auto_garmin_sync()` 每 2h（需 GARMIN_EMAIL 环境变量） | 每 2 小时触发 `/internal/garmin-sync` |

### 挑战 4：Gunicorn 多 Worker

**问题**：当前 `gunicorn.conf.py` 默认启动 `2*CPU+1` 个 worker，SQLite 不支持并发写入。

**解决方案**：环境变量优先，默认 1 个 worker。

```python
# gunicorn.conf.py 修改
workers = int(os.environ.get("GUNICORN_WORKERS", 1))  # 默认 1，Cloud Run 必须
```

### 挑战 5：端口绑定

**问题**：Cloud Run 要求应用监听 `PORT` 环境变量指定的端口，不是固定的 5000。

**解决方案**：环境变量优先级链：`PORT` > `GUNICORN_BIND` > 默认 5000。

```python
# gunicorn.conf.py 修改
port = os.environ.get("PORT", os.environ.get("GUNICORN_PORT", "5000"))
bind = f"0.0.0.0:{port}"
```

### 挑战 6：内部 API 端点安全

**问题**：`/internal/backup` 和 `/internal/garmin-sync` 需要保护，不能让外部访问。

**解决方案**：Bearer Token 验证。Cloud Scheduler 请求带 `INTERNAL_API_TOKEN` 环境变量值。

```python
# 内部端点保护
INTERNAL_API_TOKEN = os.environ.get('INTERNAL_API_TOKEN', '')

@app.route('/internal/backup', methods=['POST'])
def internal_backup():
    if request.headers.get('Authorization') != f'Bearer {INTERNAL_API_TOKEN}':
        return api_error('Unauthorized', status_code=401)
    # 执行备份逻辑...
```

---

## 3. 代码审查发现的问题

### 问题 1：GCS 恢复时序（严重）

**现状**：`wsgi.py:8-13` 的执行顺序：
```python
from app import app          # ① 加载 app.py 模块
from utils.db import init_db  # ② 导入 init_db
with app.app_context():
    init_db()                 # ③ 执行迁移
```

**风险**：如果 GCS 恢复加在 `init_db()` 之后，会先创建空库再被 GCS 数据覆盖，迁移丢失。

**修复**：GCS 恢复必须在 `init_db()` 之前执行：
```python
# wsgi.py 修复后
from app import app
from utils.db import init_db
from services.gcs_sync import restore_db_from_gcs

# 1. 先从 GCS 恢复数据库
restore_db_from_gcs()

# 2. 再执行迁移
with app.app_context():
    init_db()

application = app
```

### 问题 2：停机时备份不可靠

**现状**：
- `gunicorn.conf.py:72` 的 `on_exit` 钩子只打印日志，**没有数据库备份**
- `app.py:179` 的 `atexit` 只取消 timer，**没有备份逻辑**

**修复**：在 `on_exit` 和 `atexit` 中添加 GCS 备份：
```python
# gunicorn.conf.py
def on_exit(server):
    from services.gcs_sync import backup_db_to_gcs
    backup_db_to_gcs()  # 停机时备份
    print("[Gunicorn] 服务器已关闭")
```

### 问题 3：Gunicorn 端口硬编码

**现状**：`gunicorn.conf.py:11` 写死了 `0.0.0.0:5000`。

**修复**：已包含在挑战 5 的解决方案中。

### 问题 4：Worker 数量不适配 Cloud Run

**现状**：`gunicorn.conf.py:16` 默认 `2*CPU+1`。

**修复**：已包含在挑战 4 的解决方案中。

### 问题 5：Dockerfile 依赖缺失

**现状**：`Dockerfile` 没有安装 `google-cloud-storage`。

**修复**：
```dockerfile
# Dockerfile 修改
RUN pip install --no-cache-dir -r requirements.txt google-cloud-storage gunicorn
```

### 问题 6：内部 API 端点不存在

**现状**：文档提到 `/internal/backup` 和 `/internal/garmin-sync`，但代码中还没有。

**修复**：在 `app.py` 中新增端点，或在 `routes/` 下新建 `api_internal.py` Blueprint。

---

## 4. 需要改动的文件

### 4.1 `gunicorn.conf.py`

改动点：
1. 端口绑定改为使用 `PORT` 环境变量
2. Worker 数量默认改为 1
3. `on_exit` 钩子添加 GCS 备份

### 4.2 `app.py`

改动点：
1. `auto_backup()` 增加 GCS 上传逻辑
2. `atexit` 注册 GCS 备份
3. 新增 `/internal/backup` 和 `/internal/garmin-sync` 端点
4. 新增 `periodic_gcs_backup()` 定时器（每 5 分钟）

### 4.3 `wsgi.py`

改动点：
1. 在 `init_db()` 之前调用 `restore_db_from_gcs()`

### 4.4 `core/config.py`

改动点：
1. 添加 GCS 相关配置（存储桶名称、路径前缀）
2. 添加 `INTERNAL_API_TOKEN` 配置

### 4.5 `services/gcs_sync.py`（新建）

GCS 同步工具函数：
- `get_gcs_bucket()`：获取 GCS 存储桶客户端
- `restore_db_from_gcs()`：从 GCS 恢复数据库
- `backup_db_to_gcs()`：备份数据库到 GCS
- `sync_file_to_gcs(local_path, gcs_path)`：同步文件到 GCS
- `sync_file_from_gcs(gcs_path, local_path)`：从 GCS 下载文件

### 4.6 `Dockerfile`

改动点：
1. 安装 `google-cloud-storage` 依赖
2. 设置 GCP 服务账号环境变量

### 4.7 `routes/api_internal.py`（新建）

内部 API Blueprint：
- `POST /internal/backup`：触发数据库备份
- `POST /internal/garmin-sync`：触发 Garmin 同步

### 4.8 `deploy/cloud-run.yaml`（新建）

Cloud Run 部署配置文件。

---

## 5. Cloud Run 部署配置

### 5.1 `deploy/cloud-run.yaml`

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: sugar-bee
  namespace: ''
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        run.googleapis.com/cpu-throttling: 'true'
        run.googleapis.com/execution-environment: gen2
    spec:
      containerConcurrency: 1
      timeoutSeconds: '300'
      containers:
        - image: gcr.io/YOUR_PROJECT_ID/sugar-bee
          ports:
            - containerPort: 8080
          env:
            - name: FLASK_ENV
              value: production
            - name: SECRET_KEY
              value: 'YOUR_SECRET_KEY'
            - name: GCS_BUCKET_NAME
              value: sugar-bee-data
            - name: GCS_DB_PATH
              value: db/glucose.db
            - name: GUNICORN_WORKERS
              value: '1'
            - name: INTERNAL_API_TOKEN
              value: 'YOUR_INTERNAL_TOKEN'
          resources:
            limits:
              cpu: 1
              memory: 512Mi
          startupProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 15
            periodSeconds: 10
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 30
            failureThreshold: 3
      serviceAccountName: sugar-bee@YOUR_PROJECT_ID.iam.gserviceaccount.com
  traffic:
    - latestRevision: true
      percent: 100
```

### 5.2 部署命令

```bash
# 1. 设置项目
gcloud config set project YOUR_PROJECT_ID

# 2. 构建镜像
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/sugar-bee

# 3. 部署到 Cloud Run
gcloud run deploy sugar-bee \
  --image gcr.io/YOUR_PROJECT_ID/sugar-bee \
  --platform managed \
  --region asia-east1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 1 \
  --set-env-vars="FLASK_ENV=production,GCS_BUCKET_NAME=sugar-bee-data,INTERNAL_API_TOKEN=YOUR_TOKEN" \
  --service-account sugar-bee@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### 5.3 GCP 资源清单

| 资源 | 用途 | 免费额度 |
|------|------|----------|
| Cloud Run | 运行 Flask 应用 | 200 万请求/月 |
| Cloud Storage | 持久化存储 | 5 GB/月（美国区域）|
| IAM Service Account | 应用身份认证 | 免费 |
| Cloud Scheduler | 定时触发备份 | 3 个免费作业 |

---

## 6. 国内访问方案

### 方案：Cloudflare DNS + CDN

Cloud Run 的原生域名 `*.run.app` 在国内部分运营商可直连，但不保证稳定。推荐用 Cloudflare 作为中间层。

### 步骤

1. **注册 Cloudflare**（免费计划即可）
2. **添加自定义域名**（如 `sugar.yourdomain.com`）
3. **DNS 配置**：
   - 类型：CNAME
   - 名称：`sugar`
   - 目标：`sugar-bee-xxx-uc.a.run.app`（Cloud Run 分配的域名）
   - 代理状态：已代理（橙色云朵）
4. **SSL/TLS 设置**：
   - 模式：Full (Strict)
   - 启用 HSTS
5. **Cloud Run 域名映射**（可选）：
   ```bash
   gcloud run domain-mappings create \
     --service sugar-bee \
     --domain sugar.yourdomain.com \
     --region asia-east1
   ```

### 为什么 Cloudflare 可行

- Cloudflare 在国内有合作节点（与京东云合作）
- `*.yourdomain.com` 的 DNS 解析走 Cloudflare 代理，不受 GFW 干扰
- Cloudflare 免费计划支持无限流量、SSL、DDoS 防护

---

## 7. 分步实施计划

### Phase 1：GCS 存储层（无代码改动）

- [ ] 创建 GCS 存储桶 `sugar-bee-data`（`asia-east1` 区域）
- [ ] 创建 IAM 服务账号 `sugar-bee`，授予 `Storage Object Admin` 角色
- [ ] 生成服务账号 JSON 密钥
- [ ] 手动上传当前 `glucose.db` 到 GCS `db/` 目录

### Phase 2：代码适配（最小改动）

- [ ] 新增 `services/gcs_sync.py`：GCS 上传/下载工具函数
- [ ] 修改 `gunicorn.conf.py`：适配 `PORT` 环境变量，默认 1 worker
- [ ] 修改 `app.py`：`auto_backup()` 增加 GCS 上传逻辑
- [ ] 修改 `wsgi.py`：在 `init_db()` 之前调用 `restore_db_from_gcs()`
- [ ] 新增 `routes/api_internal.py`：内部 API 端点
- [ ] 修改 `core/config.py`：添加 GCS 和 INTERNAL_API_TOKEN 配置

### Phase 3：Docker 镜像

- [ ] 修改 `Dockerfile`：安装 `google-cloud-storage`
- [ ] 构建并本地测试镜像
- [ ] 推送镜像到 GCR

### Phase 4：Cloud Run 部署

- [ ] 创建 `deploy/cloud-run.yaml`
- [ ] 部署到 Cloud Run（`asia-east1`）
- [ ] 验证健康检查端点
- [ ] 验证数据持久化（重启后数据不丢失）

### Phase 5：Cloudflare 域名

- [ ] 配置 Cloudflare DNS
- [ ] 绑定自定义域名到 Cloud Run
- [ ] 测试国内访问

### Phase 6：Cloud Scheduler（定时任务）

- [ ] 生成 `INTERNAL_API_TOKEN`（`openssl rand -hex 32`）
- [ ] 创建 Cloud Scheduler 作业
  - 每天凌晨 2:00 触发 `/internal/backup`
  - 每 2 小时触发 `/internal/garmin-sync`
- [ ] 配置 IAM 权限（Cloud Scheduler → Cloud Run）

---

## 8. 费用估算

### 场景 A：按需启停（推荐）

| 项目 | 用量 | 费用 |
|------|------|------|
| Cloud Run | min=0，每天活跃 2h | 免费（18 万 vCPU-秒内） |
| Cloud Storage | 5 GB 内 | 免费 |
| Cloud Scheduler | 2 个作业 | 免费（3 个免费） |
| Cloudflare | 免费计划 | 免费 |
| **月合计** | | **$0** |

> 注：免费额度每月重置。如果某个月用量超过 18 万 vCPU-秒（约 50 小时），超出部分按 $0.00002400/vCPU-秒 计费，预计 $1-2。

### 场景 B：24h 运行

| 项目 | 用量 | 费用 |
|------|------|------|
| Cloud Run | min=1，24h×30 天 | ~$7.30（1 个 vCPU × 720h） |
| Cloud Storage | 5 GB 内 | 免费 |
| Cloud Scheduler | 2 个作业 | 免费 |
| Cloudflare | 免费计划 | 免费 |
| **月合计** | | **~$7.30** |

---

## 9. 回滚方案

### 快速回滚（5 分钟内）

```bash
# 回滚到上一个版本
gcloud run services update sugar-bee \
  --image gcr.io/YOUR_PROJECT_ID/sugar-bee:previous-tag \
  --region asia-east1

# 或直接删除 Cloud Run 服务
gcloud run services delete sugar-bee --region asia-east1
```

### 数据恢复

```bash
# 从 GCS 下载数据库
gsutil cp gs://sugar-bee-data/db/glucose.db ./glucose.db

# 或从备份恢复
gsutil ls gs://sugar-bee-data/backups/
gsutil cp gs://sugar-bee-data/backups/glucose_YYYYMMDD.db ./glucose.db
```

### 本地 Docker 兼容

部署期间不影响本地 Docker 使用。Cloud Run 和本地 Docker Compose 可以并行运行，使用同一个 GCS 存储桶同步数据。

---

## 附录

### A. 环境变量清单

| 变量 | 必需 | 说明 |
|------|------|------|
| `PORT` | Cloud Run 自动注入 | 应用监听端口 |
| `SECRET_KEY` | 是 | Flask session 密钥 |
| `FLASK_ENV` | 是 | 设为 `production` |
| `GCS_BUCKET_NAME` | 是 | GCS 存储桶名称 |
| `GCS_DB_PATH` | 否 | 数据库在 GCS 中的路径，默认 `db/glucose.db` |
| `GCP_PROJECT_ID` | 是 | GCP 项目 ID |
| `GCP_SERVICE_ACCOUNT_KEY` | 是 | 服务账号 JSON 路径（Cloud Run 自动挂载） |
| `INTERNAL_API_TOKEN` | 是 | 内部 API 验证 token（用于 Cloud Scheduler） |
| `GEMINI_API_KEY` | 推荐 | AI 功能 |
| `GARMIN_EMAIL` | 可选 | Garmin 同步 |
| `GARMIN_TOKEN_DIR` | 否 | Garmin token 目录，默认 `.garmin_tokens` |

### B. 相关文档

- [Google Cloud Free Tier 指南](../../docs/infra/google-cloud-free-tier-guide.md)
- [Cloud Run 定价](https://cloud.google.com/run/pricing)
- [Cloud Storage 定价](https://cloud.google.com/storage/pricing)
- [Cloudflare 免费计划](https://www.cloudflare.com/plans/)
