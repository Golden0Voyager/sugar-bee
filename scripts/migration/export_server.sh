#!/usr/bin/env bash
#
# Sugar Bee 迁移 — 旧服务器导出
#
# 在【旧服务器】以 root 运行，产出一个自校验的归档包：
#   /tmp/sugar_bee_migrate_<时间戳>.tar.gz
#   /tmp/sugar_bee_migrate_<时间戳>.tar.gz.sha256
#
# 归档内容（全部是 git 里没有、或者不能从 git 重建的东西）：
#   db/glucose.db        在线一致性快照（sqlite Online Backup API）
#   data/                头像、用户数据、Garmin token
#   ops/env              .env 及其历史备份
#   ops/backup_db.py     每日热备脚本（注意：它不在 git 仓库里）
#   ops/crontab.txt      当前 root 的定时任务
#   ops/nginx-sugar-bee.conf
#   backups/             最近 N 份数据库快照（默认 3 份，避免把 242M 全搬走）
#   repo/*.bundle        完整 git 历史（离线兜底，不依赖 GitHub 可达）
#   MANIFEST.txt         提交号、记录数、每个文件的 sha256
#
# 用法：
#   sudo bash export_server.sh              # 默认保留 3 份历史快照
#   sudo KEEP_BACKUPS=7 bash export_server.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/sugar_bee/app}"
OPS_DIR="${OPS_DIR:-/opt/sugar_bee}"
CONTAINER="${CONTAINER:-sugar-bee}"
KEEP_BACKUPS="${KEEP_BACKUPS:-3}"

TS="$(date +%Y%m%d_%H%M%S)"
WORK="$(mktemp -d "/tmp/sb_migrate_${TS}.XXXXXX")"
STAGE="$WORK/stage"
ARCHIVE="/tmp/sugar_bee_migrate_${TS}.tar.gz"

log() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()  { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m  !\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 预检
log "预检环境"
[ "$(id -u)" -eq 0 ] || die "需要 root 运行（要读 .env 和 /etc/nginx）"
[ -d "$APP_DIR" ] || die "找不到应用目录 $APP_DIR"
docker inspect "$CONTAINER" >/dev/null 2>&1 || die "容器 $CONTAINER 不存在"
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" = "true" ] \
  || die "容器未运行，无法做在线快照；请先 docker compose up -d"
ok "应用目录 $APP_DIR，容器 $CONTAINER 运行中"

mkdir -p "$STAGE"/{db,data,ops,backups,repo}

# ---------------------------------------------------------------- 数据库快照
# 为什么不用 cp：SQLite 有 -wal/-shm 附属文件，直接拷可能拿到写了一半的页。
log "在线快照数据库（应用不停机）"
docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3
src = sqlite3.connect('/app/db_data/glucose.db')
dst = sqlite3.connect('/tmp/_sb_migrate.db')
src.backup(dst)          # 官方 Online Backup API，事务一致
dst.close()
src.close()
print('  backup api done')
PY
docker cp "$CONTAINER:/tmp/_sb_migrate.db" "$STAGE/db/glucose.db" >/dev/null
docker exec "$CONTAINER" rm -f /tmp/_sb_migrate.db
ok "快照已取出 $(du -h "$STAGE/db/glucose.db" | cut -f1)"

log "校验快照完整性"
read -r INTEG RECORDS USERS < <(python3 - "$STAGE/db/glucose.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
integ = c.execute('PRAGMA integrity_check').fetchone()[0]
rec = c.execute('SELECT COUNT(*) FROM records').fetchone()[0]
usr = c.execute('SELECT COUNT(DISTINCT user_id) FROM records').fetchone()[0]
print(integ, rec, usr)
PY
)
[ "$INTEG" = "ok" ] || die "快照 integrity_check 失败：$INTEG"
ok "integrity_check=ok  records=$RECORDS  distinct_user=$USERS"

# ---------------------------------------------------------------- 业务数据
log "打包业务数据"
for rel in "static/avatars" "user_data" ".garmin_tokens"; do
  if [ -e "$APP_DIR/$rel" ]; then
    mkdir -p "$STAGE/data/$(dirname "$rel")"
    cp -a "$APP_DIR/$rel" "$STAGE/data/$rel"
    ok "$rel"
  else
    warn "$rel 不存在，跳过"
  fi
done

# ---------------------------------------------------------------- 配置与运维
log "打包配置与运维文件"
[ -f "$APP_DIR/.env" ] || die "找不到 $APP_DIR/.env"
cp -a "$APP_DIR/.env" "$STAGE/ops/env"
chmod 600 "$STAGE/ops/env"
ok ".env（12 项密钥，权限 600）"

# 历史 .env 备份也带上，成本极低，回滚时可能救命
shopt -s nullglob
for f in "$APP_DIR"/.env.bak.*; do
  cp -a "$f" "$STAGE/ops/$(basename "$f")"
  ok "$(basename "$f")"
done
shopt -u nullglob

if [ -f "$OPS_DIR/backup_db.py" ]; then
  cp -a "$OPS_DIR/backup_db.py" "$STAGE/ops/"
  ok "backup_db.py（不在 git 里，必须单独搬）"
else
  warn "$OPS_DIR/backup_db.py 不存在"
fi

crontab -l > "$STAGE/ops/crontab.txt" 2>/dev/null || warn "当前无 crontab"
ok "crontab.txt"

if [ -f /etc/nginx/sites-available/sugar-bee ]; then
  cp -a /etc/nginx/sites-available/sugar-bee "$STAGE/ops/nginx-sugar-bee.conf"
  ok "nginx 站点配置"
fi

# ---------------------------------------------------------------- 历史快照（裁剪）
log "挑选最近 $KEEP_BACKUPS 份数据库快照"
TOTAL_BK=$(ls -1 "$APP_DIR/backups"/glucose_*.db 2>/dev/null | wc -l | tr -d ' ')
if [ "$TOTAL_BK" -gt 0 ]; then
  ls -1t "$APP_DIR/backups"/glucose_*.db 2>/dev/null | head -n "$KEEP_BACKUPS" \
    | while read -r f; do cp -a "$f" "$STAGE/backups/"; ok "$(basename "$f")"; done
  if [ "$TOTAL_BK" -gt "$KEEP_BACKUPS" ]; then
    warn "另有 $((TOTAL_BK - KEEP_BACKUPS)) 份旧快照未打包（旧服务器到期前请勿删除）"
  fi
else
  warn "没有历史快照"
fi

# ---------------------------------------------------------------- 源码归档
# 为什么不用 git bundle：旧机是浅克隆（.git/shallow，只有 1 个 commit），
# bundle 会引用不存在的父提交，换台机器 clone 直接报
# "Failed to traverse parents of commit"。用 git archive 导出工作树才是自洽的。
log "导出源码归档"
git -C "$APP_DIR" archive --format=tar.gz -o "$STAGE/repo/source.tar.gz" HEAD
GIT_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
GIT_BRANCH="$(git -C "$APP_DIR" branch --show-current)"
ok "source.tar.gz $(du -h "$STAGE/repo/source.tar.gz" | cut -f1)  commit=$GIT_COMMIT  branch=$GIT_BRANCH"

if [ "$(git -C "$APP_DIR" rev-parse --is-shallow-repository)" = "true" ]; then
  warn "旧机是浅克隆，归档只含当前快照（不含历史），不影响运行"
fi

# ---------------------------------------------------------------- 清单
log "生成 MANIFEST"
{
  echo "# Sugar Bee 迁移包清单"
  echo
  echo "导出时间   : $(date -Is)"
  echo "源主机     : $(hostname)"
  echo "源公网 IP  : $(curl -s -m 3 https://ifconfig.me 2>/dev/null || echo unknown)"
  echo "git commit : $GIT_COMMIT"
  echo "git branch : $GIT_BRANCH"
  echo "数据库记录 : $RECORDS"
  echo "用户数     : $USERS"
  echo "integrity  : $INTEG"
  echo
  echo "## .env 键名（值不写入清单）"
  grep -vE '^\s*#|^\s*$' "$STAGE/ops/env" | sed -E 's/=.*/=***/' | sort
  echo
  echo "## 文件 sha256"
  ( cd "$STAGE" && find . -type f ! -name MANIFEST.txt -print0 \
      | sort -z | xargs -0 sha256sum )
} > "$STAGE/MANIFEST.txt"
ok "MANIFEST.txt"

# ---------------------------------------------------------------- 打包
log "生成归档"
tar -C "$STAGE" -czf "$ARCHIVE" .
( cd "$(dirname "$ARCHIVE")" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )

rm -rf "$WORK"

echo
log "完成"
echo "  归档 : $ARCHIVE  ($(du -h "$ARCHIVE" | cut -f1))"
echo "  校验 : $ARCHIVE.sha256"
echo "        $(cat "$ARCHIVE.sha256")"
echo
echo "下一步（在你本机执行）："
echo "  scp root@<旧服务器IP>:$ARCHIVE* ~/sugar_bee_migration/"
echo
echo "下载后请立刻在本地再算一次 sha256 比对，确认传输无损。"
