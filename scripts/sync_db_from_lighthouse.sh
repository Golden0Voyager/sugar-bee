#!/bin/bash
# Sugar Bee —— 服务器数据库拉回本机（异地留存）
#
# 用法：
#   ./sync_db_from_lighthouse.sh          # 立即同步一次
#   ./sync_db_from_lighthouse.sh --quiet  # 只在出错时输出
#
# 设计要点：
#   1. 不在服务器上直接 scp 正在写的 glucose.db，而是先触发一次
#      sqlite Online Backup 快照再下载，避免拿到半写状态的库。
#   2. 下载后本机做一次完整性校验 + 记录数统计，坏快照不覆盖好备份。
#   3. 默认保留 60 天历史，可按环境变量调整。

set -uo pipefail

SSH_HOST="${SB_HOST:-124.220.181.225}"
SSH_USER="${SB_USER:-root}"
SSH_KEY="${SB_KEY:-$HOME/.ssh/lighthouse_sh}"

REMOTE_DIR="${SB_REMOTE_DIR:-/opt/sugar_bee/app/backups}"
REMOTE_PY="${SB_REMOTE_PY:-/opt/sugar_bee/backup_db.py}"
# 容器内路径 -> 宿主机路径的映射前缀（compose 把 /opt/sugar_bee/app 挂到 /app）
CONTAINER_PREFIX="${SB_CONTAINER_PREFIX:-/app/}"
HOST_PREFIX="${SB_HOST_PREFIX:-/opt/sugar_bee/app/}"

LOCAL_DIR="${SB_LOCAL_DIR:-$HOME/Code/sugar_bee/backups/from_lighthouse}"
KEEP_DAYS="${SB_KEEP_DAYS:-60}"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

log() { [ "$QUIET" = "1" ] && [ "$1" != "ERROR" ] && return 0; printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"; }

SSH_CMD=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$SSH_USER@$SSH_HOST")
SCP_CMD=(scp -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15)

mkdir -p "$LOCAL_DIR"

# ---- 1. 在服务器上生成一致性快照 ----
line=$("${SSH_CMD[@]}" "docker exec -i sugar-bee python - < $REMOTE_PY" 2>&1)
if [[ "$line" != SNAPSHOT=* ]]; then
  log ERROR "服务器快照失败：$line"
  exit 1
fi

remote_path=$(printf '%s' "$line" | sed -n 's/.*SNAPSHOT=\([^ ]*\).*/\1/p')
records=$(printf '%s' "$line" | sed -n 's/.*RECORDS=\([0-9]*\).*/\1/p')
# 容器路径换成宿主机路径，scp 才取得到
remote_path="${remote_path/#$CONTAINER_PREFIX/$HOST_PREFIX}"
log INFO "服务器快照就绪 $(basename "$remote_path")，records=$records"

# ---- 2. 拉回本机 ----
ts=$(date '+%Y%m%d')
target="$LOCAL_DIR/glucose_${ts}.db"
tmp="$target.downloading"
"${SCP_CMD[@]}" "$SSH_USER@$SSH_HOST:$remote_path" "$tmp" >/dev/null 2>&1
if [ ! -s "$tmp" ]; then
  log ERROR "下载失败"
  rm -f "$tmp"
  exit 1
fi

# ---- 3. 完整性校验，通过后才落位 ----
check=$(sqlite3 "$tmp" 'pragma integrity_check;' 2>/dev/null)
n=$(sqlite3 "$tmp" 'select count(*) from records;' 2>/dev/null)
if [ "$check" != "ok" ] || [ -z "$n" ]; then
  log ERROR "下载文件校验失败（integrity=$check, records=$n），已丢弃"
  rm -f "$tmp"
  exit 1
fi

mv "$tmp" "$target"
ln -sfh "$(basename "$target")" "$LOCAL_DIR/glucose_latest.db" 2>/dev/null || ln -sf "$(basename "$target")" "$LOCAL_DIR/glucose_latest.db"
log INFO "已保存到 $(basename "$target")（records=$n, $(du -h "$target" | cut -f1)）"

# ---- 4. 清理旧备份 ----
find "$LOCAL_DIR" -name 'glucose_2*.db' -mtime +"$KEEP_DAYS" -delete 2>/dev/null
log INFO "完成，本地保留 $KEEP_DAYS 天（当前 $(ls -1 "$LOCAL_DIR" | grep -c '^glucose_2.*\.db$') 份）"
