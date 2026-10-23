#!/usr/bin/env bash
#
# Sugar Bee 迁移 — 新服务器导入
#
# 在【新服务器】以 root 运行：
#   sudo bash import_server.sh /root/sugar_bee_migrate_20261019_143000.tar.gz
#
# 做的事：
#   1. 校验归档 sha256 + MANIFEST 内每个文件的 sha256
#   2. 装 docker / docker compose / nginx / git（已装则跳过）
#   3. 从 git bundle 还原代码到指定 commit（不依赖 GitHub 可达）
#   4. 还原数据库、头像、用户数据、Garmin token、.env
#   5. 修正挂载目录属主（新机器 appuser 的 UID 很可能和旧机器不同！）
#   6. 构建镜像、启动、等健康检查通过
#   7. 装 nginx 反代、装每日热备 cron
#
# 全程幂等：重复执行会覆盖还原，不会重复安装服务。
#
set -euo pipefail

ARCHIVE="${1:-}"
APP_DIR="${APP_DIR:-/opt/sugar_bee/app}"
OPS_DIR="${OPS_DIR:-/opt/sugar_bee}"
CONTAINER="${CONTAINER:-sugar-bee}"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()  { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m  !\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root 运行"
[ -n "$ARCHIVE" ] || die "用法: bash import_server.sh <归档路径>"
[ -f "$ARCHIVE" ] || die "归档不存在: $ARCHIVE"

WORK="$(mktemp -d /tmp/sb_import.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# ---------------------------------------------------------------- 校验
log "校验归档完整性"
if [ -f "$ARCHIVE.sha256" ]; then
  ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" ) || die "归档 sha256 不匹配，传输可能损坏"
  ok "归档 sha256 匹配"
else
  warn "找不到 .sha256 文件，跳过整体校验"
fi

log "解包"
tar -C "$WORK" -xzf "$ARCHIVE"
STAGE="$WORK"
[ -f "$STAGE/MANIFEST.txt" ] || die "归档里没有 MANIFEST.txt，不是合法迁移包"

log "校验包内每个文件"
( cd "$STAGE" && grep -A100000 '## 文件 sha256' MANIFEST.txt | tail -n +2 | sha256sum -c --quiet - ) \
  || die "包内文件校验失败"
ok "全部文件 sha256 通过"

GIT_COMMIT="$(grep '^git commit' "$STAGE/MANIFEST.txt" | awk '{print $NF}')"
RECORDS="$(grep '^数据库记录' "$STAGE/MANIFEST.txt" | awk '{print $NF}')"
echo
echo "  源提交   : $GIT_COMMIT"
echo "  源记录数 : $RECORDS"
echo

# ---------------------------------------------------------------- 依赖
log "检查系统依赖"
need=()
command -v docker >/dev/null 2>&1 || need+=(docker.io)
docker compose version >/dev/null 2>&1 || need+=(docker-compose-v2)
command -v nginx >/dev/null 2>&1 || need+=(nginx)
command -v git   >/dev/null 2>&1 || need+=(git)
command -v curl  >/dev/null 2>&1 || need+=(curl)

if [ ${#need[@]} -gt 0 ]; then
  log "安装: ${need[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "${need[@]}"
  systemctl enable --now docker
  ok "依赖已安装"
else
  ok "依赖齐全"
fi

# ---------------------------------------------------------------- 代码
# 优先从 GitHub 克隆（仓库公开，commit 可复现）；网络不通时退回到归档内源码。
REPO_URL="https://github.com/Golden0Voyager/sugar-bee.git"
log "还原代码"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin 2>/dev/null || warn "fetch 失败，沿用本地已有提交"
  git -C "$APP_DIR" checkout --quiet "$GIT_COMMIT"
  ok "已有仓库，切到 $GIT_COMMIT"
elif git clone --quiet "$REPO_URL" "$APP_DIR" 2>/dev/null; then
  git -C "$APP_DIR" checkout --quiet "$GIT_COMMIT"
  ok "从 GitHub 克隆并切到 $GIT_COMMIT"
else
  warn "GitHub 不可达，改用归档内源码（无 git 历史，但内容与线上一致）"
  mkdir -p "$APP_DIR"
  tar -C "$APP_DIR" -xzf "$STAGE/repo/source.tar.gz"
  ok "已从 source.tar.gz 还原"
fi

# ---------------------------------------------------------------- 数据
log "还原数据"
mkdir -p "$APP_DIR/db_data" "$APP_DIR/backups" "$APP_DIR/static/avatars" "$APP_DIR/user_data" "$APP_DIR/.garmin_tokens"

cp -a "$STAGE/db/glucose.db" "$APP_DIR/db_data/glucose.db"
ok "数据库 $(du -h "$APP_DIR/db_data/glucose.db" | cut -f1)"

for rel in "static/avatars" "user_data" ".garmin_tokens"; do
  if [ -d "$STAGE/data/$rel" ]; then
    cp -a "$STAGE/data/$rel/." "$APP_DIR/$rel/"
    ok "$rel"
  fi
done

if [ -d "$STAGE/backups" ] && [ -n "$(ls -A "$STAGE/backups" 2>/dev/null)" ]; then
  cp -a "$STAGE/backups/." "$APP_DIR/backups/"
  ok "历史快照 $(ls -1 "$STAGE/backups" | wc -l | tr -d ' ') 份"
fi

cp -a "$STAGE/ops/env" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
ok ".env（600，$(grep -vcE '^\s*#|^\s*$' "$APP_DIR/.env") 项）"

shopt -s nullglob
for f in "$STAGE"/ops/.env.bak.*; do cp -a "$f" "$APP_DIR/$(basename "$f")"; done
shopt -u nullglob

if [ -f "$STAGE/ops/backup_db.py" ]; then
  cp -a "$STAGE/ops/backup_db.py" "$OPS_DIR/backup_db.py"
  ok "backup_db.py → $OPS_DIR/"
fi

# ---------------------------------------------------------------- 构建
log "构建镜像"
( cd "$APP_DIR" && docker compose build --quiet )
ok "镜像 sugar-bee:latest 就绪"

# ---------------------------------------------------------------- 属主修正
# 关键：Dockerfile 用 useradd -r 创建 appuser，UID 由系统分配，
# 新机器上很可能不是旧机器的 999。挂载目录若属主不对，SQLite 会直接报 readonly。
log "修正挂载目录属主"
IMG_UID="$(docker run --rm --entrypoint id sugar-bee:latest -u)"
IMG_GID="$(docker run --rm --entrypoint id sugar-bee:latest -g)"
chown -R "$IMG_UID:$IMG_GID" \
  "$APP_DIR/db_data" "$APP_DIR/backups" "$APP_DIR/static/avatars" \
  "$APP_DIR/user_data" "$APP_DIR/.garmin_tokens"
ok "容器用户 uid=$IMG_UID gid=$IMG_GID"

# ---------------------------------------------------------------- 启动
log "启动容器"
( cd "$APP_DIR" && docker compose up -d --quiet-pull )
ok "已启动"

log "等待健康检查（最长 90s）"
for i in $(seq 1 45); do
  STATE="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo starting)"
  [ "$STATE" = "healthy" ] && break
  sleep 2
done
[ "$STATE" = "healthy" ] || die "容器未变健康（当前 $STATE），看日志：docker logs --tail 50 $CONTAINER"
ok "healthy"

curl -fsS -m 5 http://127.0.0.1:5000/health >/dev/null || die "本机 /health 不通"
ok "本机 /health 200"

# ---------------------------------------------------------------- nginx
log "配置 nginx"
if [ -f "$STAGE/ops/nginx-sugar-bee.conf" ]; then
  cp "$STAGE/ops/nginx-sugar-bee.conf" /etc/nginx/sites-available/sugar-bee
else
  cat > /etc/nginx/sites-available/sugar-bee <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 20M;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
NGINX
  warn "用内置模板生成 nginx 配置"
fi
ln -sf /etc/nginx/sites-available/sugar-bee /etc/nginx/sites-enabled/sugar-bee
# Ubuntu 默认站点也占着 default_server，不删会启动失败
rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null 2>&1 || die "nginx 配置测试失败"
systemctl enable --now nginx >/dev/null 2>&1
systemctl reload nginx
ok "nginx 已生效"

# ---------------------------------------------------------------- cron
log "恢复每日热备任务"
BACKUP_LINE="10 3 * * * docker exec -i $CONTAINER python - < $OPS_DIR/backup_db.py >> /var/log/sugar_bee_backup.log 2>&1"
( crontab -l 2>/dev/null | grep -vF "backup_db.py" ; echo "$BACKUP_LINE" ) | crontab -
systemctl enable --now cron >/dev/null 2>&1 || true
ok "cron: 每天 03:10 热备"

# ---------------------------------------------------------------- 报告
PUBIP="$(curl -s -m 5 https://ifconfig.me 2>/dev/null || echo '<新服务器IP>')"
echo
log "导入完成"
cat <<EOF

  访问地址 : http://$PUBIP/
  健康检查 : http://$PUBIP/health
  容器     : docker ps --filter name=$CONTAINER
  日志     : docker logs -f $CONTAINER

  仍需人工处理：
    1. 云厂商控制台放行 80 端口（腾讯云轻量：防火墙 / 安全组）
    2. 确认记录数 = $RECORDS：
       docker exec $CONTAINER python -c "import sqlite3;print(sqlite3.connect('/app/db_data/glucose.db').execute('select count(*) from records').fetchone()[0])"
    3. 登录一次，确认 Garmin 同步、头像、历史曲线正常
    4. 改本机 scripts/sync_db_from_lighthouse.sh 里的旧 IP

EOF
