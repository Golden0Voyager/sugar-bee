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
```
