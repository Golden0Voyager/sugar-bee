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

# 主机优先级：SB_HOST 环境变量 > ~/.sugar_bee_host 文件 > 内置默认
# 迁移换服务器时只需改 ~/.sugar_bee_host 一行，不用动脚本：
#   echo 1.2.3.4 > ~/.sugar_bee_host
SSH_HOST="${SB_HOST:-$(cat "$HOME/.sugar_bee_host" 2>/dev/null || echo 124.220.181.225)}"
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

log() { case "$1" in ERROR|WARN) ;; *) [ "$QUIET" = "1" ] && return 0 ;; esac; printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"; }

# 文件大小（跨平台：Linux 用 -c，macOS/BSD 用 -f）
fsize() { stat -c %s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null; }

# 可移植超时（macOS 无 GNU timeout）。perl 不可用时退化为不设超时。
DL_TIMEOUT="${SB_DL_TIMEOUT:-180}"
if command -v perl >/dev/null 2>&1; then
  TIMEOUT_CMD=(perl -e 'alarm shift @ARGV; exec @ARGV' "$DL_TIMEOUT")
else
  TIMEOUT_CMD=()
fi

SSH_CMD=(ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$SSH_USER@$SSH_HOST")

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
# 注意：不要直接用 scp 拖 6MB+ 的原库。实测该链路在约 2MB 处会静默停滞
# （表现为 scp 挂起十几分钟、最终只落盘一个截断文件，integrity_check 全空）。
# 改为「服务器 gzip 流式压缩 + ssh 管道」，实际传输量降到 ~0.9MB，并做
# 大小比对与重试，避免半个库被当成有效备份落位。
ts=$(date '+%Y%m%d')
target="$LOCAL_DIR/glucose_${ts}.db"
tmp="$target.downloading"

remote_size=$("${SSH_CMD[@]}" "stat -c %s '$remote_path'" 2>/dev/null | tr -d '[:space:]')

download_ok=0
for attempt in 1 2 3; do
  rm -f "$tmp.gz" "$tmp"
  if [ ${#TIMEOUT_CMD[@]} -gt 0 ]; then
    "${TIMEOUT_CMD[@]}" ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
        "$SSH_USER@$SSH_HOST" "gzip -6 -c '$remote_path'" > "$tmp.gz" 2>/dev/null
  else
    ssh -i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no \
        -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 \
        "$SSH_USER@$SSH_HOST" "gzip -6 -c '$remote_path'" > "$tmp.gz" 2>/dev/null
  fi
  if gunzip -t "$tmp.gz" 2>/dev/null && gunzip -c "$tmp.gz" > "$tmp" 2>/dev/null && [ -s "$tmp" ]; then
    if [ -z "$remote_size" ] || [ "$(fsize "$tmp")" = "$remote_size" ]; then
      download_ok=1
      break
    fi
    log WARN "第 $attempt 次下载大小不符（本机 $(fsize "$tmp") / 服务器 $remote_size），重试"
  else
    log WARN "第 $attempt 次下载失败或超时（${DL_TIMEOUT}s），重试"
  fi
  sleep 5
done
rm -f "$tmp.gz"

if [ "$download_ok" != "1" ]; then
  log ERROR "下载失败（已重试 3 次，远程大小 ${remote_size:-未知}）"
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
# 与服务器快照自报的记录数交叉核对，数量对不上说明拿到的是不一致/半截的库
if [ -n "$records" ] && [ "$n" != "$records" ]; then
  log ERROR "记录数与服务器不符（本机 $n / 服务器 $records），已丢弃"
  rm -f "$tmp"
  exit 1
fi

mv "$tmp" "$target"
ln -sfh "$(basename "$target")" "$LOCAL_DIR/glucose_latest.db" 2>/dev/null || ln -sf "$(basename "$target")" "$LOCAL_DIR/glucose_latest.db"
log INFO "已保存到 $(basename "$target")（records=$n, $(du -h "$target" | cut -f1)）"

# ---- 4. 清理：每日校验，仅周日归档 ----
# 策略：每天照常拉取并做完整性校验（链路故障当天就能发现），
# 但磁盘只保留周日的归档，非周日快照校验通过后即删除，不累积日备份。
# 星期取自文件名里的日期，不用 mtime —— mtime 会被拷贝/恢复操作污染。
weekday() { date -j -f "%Y%m%d" "$1" "+%u" 2>/dev/null || date -d "$1" "+%u" 2>/dev/null; }

if [ "$(weekday "$ts")" != "7" ]; then
  rm -f "$target"
  log INFO "非周日快照 $(basename "$target") 已校验并清理（仅周日归档）"
fi

# 清掉历史遗留的非周日备份
for f in "$LOCAL_DIR"/glucose_2*.db; do
  [ -e "$f" ] || continue
  _b=$(basename "$f"); _d=${_b#glucose_}; _d=${_d%.db}
  [ "$(weekday "$_d")" = "7" ] || rm -f "$f"
done

# 周日归档同样受 KEEP_DAYS 约束
find "$LOCAL_DIR" -name 'glucose_2*.db' -mtime +"$KEEP_DAYS" -delete 2>/dev/null

# latest 始终指向现存最新的一份，避免指向已被清理的文件
# 按文件名里的日期排序（YYYYMMDD 字典序即时间序），不依赖可能被污染的 mtime
newest=$(ls -1 "$LOCAL_DIR"/glucose_2*.db 2>/dev/null | sort | tail -1)
if [ -n "$newest" ]; then
  ln -sfh "$(basename "$newest")" "$LOCAL_DIR/glucose_latest.db" 2>/dev/null || ln -sf "$(basename "$newest")" "$LOCAL_DIR/glucose_latest.db"
fi
log INFO "完成，本地保留 $KEEP_DAYS 天内的周日归档（当前 $(ls -1 "$LOCAL_DIR" | grep -c '^glucose_2.*\.db$') 份）"
