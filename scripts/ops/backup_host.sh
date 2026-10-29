#!/usr/bin/env bash
# Sugar Bee 每日快照（宿主机直连，不依赖 sugar-bee 容器是否在运行）。
#
# 为什么要有它：容器内部 app.py 的 auto_backup() 用 threading.Timer 跑，
# 容器一停就跟着停。旧机曾因容器停了 7 小时漏掉一整天的备份。
# 这个脚本直接读宿主机上的 db 文件，容器停着（维护窗口 / 异常退出）照样能备。
#
# 部署位置：/opt/sugar_bee/backup_host.sh
# 定时：    10 3 * * * /opt/sugar_bee/backup_host.sh >> /var/log/sugar_bee_backup.log 2>&1
#
# 干了三件事：
#   1. 用 Online Backup API 生成 glucose_snap_YYYYMMDD.db（业务时区命名）
#   2. 轮转 glucose_snap_*（保留 SB_KEEP_DAYS 天）
#   3. 手工备份 glucose_before_* 只留最新 SB_KEEP_MANUAL 份，防止越积越多
set -u

ROOT=/opt/sugar_bee
DB="$ROOT/app/db_data/glucose.db"
BK="$ROOT/app/backups"
KEEP_DAYS=14
KEEP_MANUAL=2

ts() { date '+%F %T %Z'; }

if [ ! -f "$DB" ]; then
  echo "[$(ts)] ERROR 数据库不存在: $DB"
  exit 1
fi

SB_DB="$DB" \
SB_BACKUP_DIR="$BK" \
SB_KEEP_DAYS="$KEEP_DAYS" \
python3 "$ROOT/backup_db.py"
rc=$?

# 容器以 999(appuser) 运行，备份目录要可写
chown -R 999:999 "$BK" 2>/dev/null

# 手工备份（部署前 / 改数据前临时拍的）只留最近 N 份，按 mtime 排序
pruned=0
if [ -d "$BK" ]; then
  mapfile -t olds < <(
    ls -1t "$BK"/glucose_before_*.db 2>/dev/null | tail -n +$((KEEP_MANUAL + 1))
  )
  for f in "${olds[@]:-}"; do
    [ -n "${f:-}" ] || continue
    rm -f "$f" && pruned=$((pruned + 1))
  done
fi

if [ "$rc" -eq 0 ]; then
  echo "[$(ts)] OK 快照完成 pruned_manual=$pruned"
else
  echo "[$(ts)] ERROR 快照失败 rc=$rc"
fi

exit "$rc"
