# Sugar Bee 生产部署指南

## 前置要求

- 一台 Linux 服务器（Ubuntu 22.04 LTS 推荐）
- 一个域名（已解析到服务器 IP）
- Docker & Docker Compose 已安装

## 快速开始

### 1. 克隆代码并准备环境

```bash
cd /var/www
git clone <你的仓库地址> sugar-bee
cd sugar-bee

# 生成强随机密钥
export SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
echo "SECRET_KEY=$SECRET_KEY" >> .env

# 确保数据目录存在
mkdir -p db_data backups static/avatars .garmin_tokens user_data
```

### 2. 启动应用（Docker）

```bash
docker compose up -d
```

应用将运行在 `http://服务器IP:5000`

### 3. 配置 Nginx 反向代理

```bash
sudo apt update && sudo apt install -y nginx

# 编辑域名配置
sudo cp deploy/nginx.conf /etc/nginx/sites-available/sugar-bee
sudo nano /etc/nginx/sites-available/sugar-bee  # 修改 your-domain.com

sudo ln -sf /etc/nginx/sites-available/sugar-bee /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 4. 配置 HTTPS（Let's Encrypt）

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com

# 自动续期测试
sudo certbot renew --dry-run
```

### 5. 切换到 HTTPS 配置

```bash
sudo cp deploy/nginx-ssl.conf /etc/nginx/sites-available/sugar-bee
sudo nano /etc/nginx/sites-available/sugar-bee  # 再次确认域名
sudo nginx -t
sudo systemctl reload nginx
```

## 系统服务（可选）

创建 systemd 服务确保 Docker 容器开机自启：

```bash
sudo nano /etc/systemd/system/sugar-bee.service
```

内容：

```ini
[Unit]
Description=Sugar Bee Application
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/var/www/sugar-bee
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable sugar-bee
sudo systemctl start sugar-bee
```

## 备份策略

应用已内置自动备份（每天一次，保留 30 天）。额外建议：

```bash
# 添加 cron 任务异地备份
crontab -e
# 每天凌晨 3 点同步到远程服务器
0 3 * * * rsync -avz /var/www/sugar-bee/db_data/glucose.db user@backup-server:/backups/sugar-bee/
```

## 环境变量参考

| 变量 | 说明 | 示例 |
|------|------|------|
| `SECRET_KEY` | **必填** Flask session 加密密钥 | `hex256...` |
| `FLASK_ENV` | 运行环境 | `production` |
| `SUGAR_BEE_DB_PATH` | 数据库文件路径 | `/app/db_data/glucose.db` |
| `GUNICORN_WORKERS` | Worker 数量 | `4` |
| `GUNICORN_TIMEOUT` | 请求超时（秒） | `120` |
| `REDIS_URL` | 限速共享存储（多实例时） | `redis://localhost:6379` |
| `GARMIN_EMAIL` | Garmin 账号（可选） | `user@example.com` |
| `GARMIN_USER_ID` | 绑定 Garmin 的用户 ID | `1` |
| `MODELSCOPE_API_KEY` | AI 提供商 Key（可选） | `sk-...` |
| `VOLC_API_KEY` | 火山引擎 Key（可选） | `...` |
| `GEMINI_API_KEY` | Gemini Key（可选） | `...` |

## Cloud Run 部署（生产环境）

### 架构

- **Cloud Run** (asia-east2, 512Mi, 1 CPU, 1 concurrency) — Flask 应用
- **Cloud SQL** (PostgreSQL 17, db-f1-micro, 10GB, asia-east2) — 数据库
- **Artifact Registry** (asia-east2) — Docker 镜像仓库
- **Secret Manager** — API 密钥生产环境注入

### 部署流程

```bash
# 1. 本地构建推送
gcloud builds submit --tag asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee:latest

# 2. 获取新镜像 digest（从 Cloud Build 输出或查看）
gcloud artifacts docker images list \
  asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee \
  --include-tags --filter="tags:latest" --format="value(version)"

# 3. 更新 deploy/cloud-run.yaml 中的 digest（不要用 :latest 标签）
#    image: ...@sha256:NEW_DIGEST

# 4. 部署
gcloud run services replace deploy/cloud-run.yaml --region asia-east2

# 或快速部署（跳过 YAML 更新）
gcloud run deploy sugar-bee \
  --image asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee@sha256:NEW_DIGEST \
  --region asia-east2
```

### 项目信息

| 项目 | 值 |
|------|-----|
| GCP Project ID | `project-c0560c79-7c6a-4f31-a11` |
| Cloud Run URL | `https://sugar-bee-670879142538.asia-east2.run.app` |
| 镜像仓库 | `asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee/sugar-bee` |
| Cloud SQL 实例 | `sugar-bee-db-hk` (asia-east2) |
| Cloud Scheduler | `garmin-sync` — 每日 3/9/15/21 点触发 |

### 环境变量与 Secret

cloud-run.yaml 中已配置的 Secret Manager 引用：

| 环境变量 | Secret 名 | 用途 |
|----------|-----------|------|
| `SUGAR_BEE_DATABASE_URL` | `SUGAR_BEE_DATABASE_URL` | PostgreSQL 连接串 |
| `SECRET_KEY` | `SUGAR_BEE_SECRET_KEY` | Flask session 密钥 |
| `INTERNAL_API_TOKEN` | `SUGAR_BEE_INTERNAL_API_TOKEN` | 内部 API 鉴权 |
| `GARMIN_USER_ID` | `SUGAR_BEE_GARMIN_USER_ID` | Garmin 绑定用户 |
| `GARMIN_EMAIL` | `SUGAR_BEE_GARMIN_EMAIL` | Garmin 账号 |
| `MODELSCOPE_API_KEY` | `modelscope-api-key` | AI 视觉/文本模型 |
| `SENSENOVA_API_KEY` | `sensenova-api-key` | AI 文本模型兜底 |

### 注意事项

- YAML 中的镜像必须使用 **digest**（`@sha256:...`），不用 `:latest` 标签，否则 YAML 未变更时 Cloud Run 不会创建新 revision
- 更新 Secret 后不需要重新部署，Cloud Run 会在下次请求时加载新版本
- Artifact Registry 旧镜像不会自动清理，定期用以下命令删除：

```bash
# 列出所有版本
gcloud artifacts docker images list \
  asia-east2-docker.pkg.dev/project-c0560c79-7c6a-4f31-a11/sugar-bee \
  --include-tags

# 按 tag 删除旧版本
gcloud artifacts docker images delete IMAGE:tag --quiet
```

## 故障排查

```bash
# 查看应用日志
docker compose logs -f

# 查看 Nginx 日志
sudo tail -f /var/log/nginx/sugar-bee-error.log

# 检查容器状态
docker compose ps

# 重启应用
docker compose restart

# Cloud Run 查看实时日志
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=sugar-bee" --limit=50

# 查看 Cloud SQL 连接
gcloud sql instances describe sugar-bee-db-hk
```
