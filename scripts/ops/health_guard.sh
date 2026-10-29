#!/usr/bin/env bash
# Sugar Bee 容器健康守卫（cron 每 10 分钟一次）。
#
# 为什么需要：容器 --restart unless-stopped 只能处理「进程退出」，
# 但 gunicorn 假死、DB 锁死、磁盘写满导致的 502 都属于「进程还在、服务已死」，
# Docker 不会自己重启。旧机曾出现服务不可用而无人知晓，靠人工看日志才发现。
#
# 判定与动作：
#   容器不在 running      → docker start（容器被停掉的情况）
#   /health 连续 3 次失败  → docker restart
#   重启后 90s 仍失败      → 记 CRIT + 附容器日志尾部，等人工
#   限流：30 分钟内最多重启 1 次，避免把真正的故障刷成重启风暴
#
# 健康时静默不写日志，保证日志里只剩异常。
#
# 部署位置：/opt/sugar_bee/health_guard.sh
# 定时：    */10 * * * * /opt/sugar_bee/health_guard.sh >/dev/null 2>&1
set -u

NAME=sugar-bee
HEALTH_URL=http://127.0.0.1:5000/health
LOG=/var/log/sugar_bee_health.log
STATE=/var/run/sugar_bee_health.last_restart
MAX_LOG=1048576   # 1MB

log() { printf '%s %s\n' "$(date '+%F %T %Z')" "$*" >>"$LOG"; }

rotate_log() {
  [ -f "$LOG" ] || return 0
  local size
  size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_LOG" ]; then
    tail -n 400 "$LOG" >"$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
  fi
}

healthy() { curl -fsS --max-time 8 -o /dev/null "$HEALTH_URL" 2>/dev/null; }

state_of() { docker inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null; }

can_restart() {
  local last=0 now
  [ -f "$STATE" ] && last=$(cat "$STATE" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  now=$(date +%s)
  [ $((now - last)) -ge 1800 ]
}

dump_logs() {
  docker logs --tail 30 "$NAME" 2>&1 | sed 's/^/    /' >>"$LOG"
}

rotate_log

# 0) 容器根本不存在（被误删）——不自动重建，重建要走 run_container.sh
st=$(state_of)
if [ -z "$st" ]; then
  log "CRIT 容器 $NAME 不存在，跳过自动处理（需 bash /opt/sugar_bee/run_container.sh）"
  exit 2
fi

# 1) 容器被停掉（不是崩溃，比如手工 docker stop）
if [ "$st" != "running" ]; then
  log "WARN 容器状态=$st，尝试 docker start"
  docker start "$NAME" >/dev/null 2>&1
  for _ in $(seq 1 20); do
    sleep 3
    healthy && { log "OK docker start 后服务恢复"; exit 0; }
  done
  log "CRIT docker start 后 60s 仍不健康，转重启流程"
fi

# 2) 三次探活（间隔 5s），健康就静默退出
for _ in 1 2 3; do
  healthy && exit 0
  sleep 5
done

log "CRIT /health 连续 3 次探测失败（$HEALTH_URL）"

# 3) 重启，带限流
if can_restart; then
  log "ACTION docker restart $NAME"
  date +%s >"$STATE"
  docker restart "$NAME" >/dev/null 2>&1
  for i in $(seq 1 30); do
    sleep 3
    healthy && { log "OK 重启后恢复，耗时约 $((i * 3))s"; exit 0; }
  done
  log "CRIT 重启后 90s 仍不健康，需人工介入"
  dump_logs
  exit 3
fi

log "CRIT 30 分钟内已重启过，本次只告警不重启"
dump_logs
exit 4
