#!/usr/bin/env bash
# Sugar Bee 容器健康守卫（cron 每 10 分钟一次）。
#
# 为什么需要：容器 --restart unless-stopped 只能处理「进程退出」，
# 但 gunicorn 假死、DB 锁死、磁盘写满导致的 502 都属于「进程还在、服务已死」，
# Docker 不会自己重启。旧机曾出现服务不可用而无人知晓，靠人工看日志才发现。
#
# 判定与动作：
#   容器不在 running      → docker start（容器被停掉的情况）
#   /health 连续 3 次失败  → 分类处置：
#                            · DB 项失败（模板正常）→ 只告警不重启
#                            · 其他（模板坏 / 连不上 / 响应不是 JSON）→ docker restart
#   重启后 90s 仍失败      → 记 CRIT + 附容器日志尾部，等人工
#   限流：30 分钟内最多重启 1 次，避免把真正的故障刷成重启风暴
#
# 为什么 DB 故障不重启：SQLite 的库文件和锁都在挂载卷上，重启容器解决不了
# （文件被删、磁盘满、库损坏、锁死——重启后照样坏），
# 只会每 30 分钟打断一次线上使用。所以只告警，等人处理。
#
# 健康时静默不写日志，保证日志里只剩异常。
#
# 部署位置：/opt/sugar_bee/health_guard.sh
# 定时：    */10 * * * * /opt/sugar_bee/health_guard.sh >/dev/null 2>&1
set -u

# 与 run_container.sh 共用互斥锁：部署窗口（最长 90s）内不跑守卫，
# 否则会把"容器改名中/启动中"误判为故障并误重启（review 评估 5~15% 概率撞上）。
exec 9>/var/run/sugar_bee.deploy.lock
if ! flock -n 9; then
  exit 0   # 部署/另一守卫在跑，本轮静默跳过
fi

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

healthy() { curl -fsS --max-time 4 -o /dev/null "$HEALTH_URL" 2>/dev/null; }

# 取最近一次探活的完整响应（状态码 + body），失败时用来判断是哪一项坏了。
# 注意不能用 curl -f：-f 会在 4xx/5xx 时丢掉 body，而我们要的正是失败时的 body。
probe_detail() {
  curl -sS --max-time 4 -w ' [HTTP %{http_code}]' "$HEALTH_URL" 2>&1 || true
}

# body 里能不能看出「模板正常但 DB 坏了」？
# /health 在本机直连时会返回完整 checks 明细（公网只返回状态）。
# 用 python3 解析而不是 grep：grep 匹配 JSON 文本，键名/取值类型一变
# （比如 "ok" 改成布尔 true）就静默走错分支且方向是"永不再自动重启"。
# 解析失败按"未知故障"返回假 → 走重启分支（宁可多重启，不可静默失效）。
db_broken_only() {
  local detail="$1"
  printf '%s' "$detail" | python3 -c '
import json, sys
raw = sys.stdin.read()
start = raw.find("{")
if start < 0:
    sys.exit(1)
try:
    d = json.loads(raw[start:])
except Exception:
    sys.exit(1)
checks = d.get("checks") or {}
# 只有「模板明确正常 + db 明确是错误描述字符串」才判 DB 故障（只告警）。
# 其他形状（值类型变了/键没了/解析失败）一律走重启：宁可多重启，不可静默丧失自愈。
db_val = checks.get("db")
sys.exit(0 if (checks.get("template") == "ok" and isinstance(db_val, str) and db_val != "ok") else 1)
' 2>/dev/null
}

# 按截止时间等，不按"次数×sleep"。
# 教训（假死演练实测）：容器 paused/卡死时每次 curl 都要耗满超时，
# 迭代式循环会变成 20×(3+8)=220 秒，恢复总耗时从 1 分钟拖到 4 分半。
wait_healthy() {
  local budget="$1" deadline
  deadline=$(( $(date +%s) + budget ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    healthy && return 0
    sleep 2
  done
  return 1
}

# ---- Bark 推送 ----
# key 只存在服务器 /opt/sugar_bee/.bark_key（600），绝不进 git。
# 限流：同标题 30 分钟内只推一次——故障持续时 cron 每 10 分钟一轮，
# 不限流会把手机刷屏，那跟没有告警一样。
BARK_KEY=$(cat /opt/sugar_bee/.bark_key 2>/dev/null || true)

notify() {
  local title="$1" body="$2" level="${3:-active}"
  [ -n "$BARK_KEY" ] || return 0
  local key_id stamp last=0
  key_id=$(printf '%s' "$title" | md5sum | cut -c1-8)
  stamp="/var/run/sugar_bee_notify_$key_id"
  [ -f "$stamp" ] && last=$(cat "$stamp" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $(( $(date +%s) - last )) -lt 1800 ] && return 0
  date +%s >"$stamp" 2>/dev/null || true
  python3 - "$BARK_KEY" "$title" "$body" "$level" <<'PYBARK' 2>/dev/null || true
import json, sys, urllib.request
key, title, body, level = sys.argv[1:5]
payload = json.dumps({"title": title, "body": body[:400], "level": level, "group": "sugar-bee"}).encode()
req = urllib.request.Request("https://api.day.app/" + key, data=payload,
                             headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=10)
PYBARK
}

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
  notify "Sugar Bee 容器不存在" "容器被误删，需 bash /opt/sugar_bee/run_container.sh 重建" critical
  exit 2
fi

# 1) 容器不在运行
#    paused 是特例：进程被 SIGSTOP 冻住，docker start 对它是空操作（会白等一轮），
#    必须先 unpause 再判断健康。
if [ "$st" = "paused" ]; then
  log "WARN 容器状态=paused，执行 docker unpause"
  docker unpause "$NAME" >/dev/null 2>&1
  if wait_healthy 60; then
    log "OK unpause 后服务恢复"
    exit 0
  fi
  log "CRIT unpause 后 60s 仍不健康，转重启流程"
elif [ "$st" != "running" ]; then
  log "WARN 容器状态=$st，尝试 docker start"
  docker start "$NAME" >/dev/null 2>&1
  if wait_healthy 60; then
    log "OK docker start 后服务恢复"
    exit 0
  fi
  log "CRIT docker start 后 60s 仍不健康，转重启流程"
fi

# 2) 三次探活（间隔 3s），健康就静默退出
for _ in 1 2 3; do
  healthy && exit 0
  sleep 3
done

log "CRIT /health 连续 3 次探测失败（$HEALTH_URL）"
DETAIL="$(probe_detail)"
log "DETAIL 最近一次响应: ${DETAIL:0:400}"

# 3) DB 故障：重启无用，只告警
if db_broken_only "$DETAIL"; then
  log "WARN 判定为数据库故障（模板正常），重启容器解决不了，本次只告警不重启"
  notify "Sugar Bee 数据库故障" "重启解决不了，需人工介入。响应: ${DETAIL:0:200}" timeSensitive
  dump_logs
  exit 5
fi

# 4) 重启，带限流
if can_restart; then
  log "ACTION docker restart $NAME"
  date +%s >"$STATE"
  _t0=$(date +%s)
  docker restart "$NAME" >/dev/null 2>&1
  if wait_healthy 90; then
    log "OK 重启后恢复，耗时约 $(( $(date +%s) - _t0 ))s"
    exit 0
  fi
  log "CRIT 重启后 90s 仍不健康，需人工介入"
  notify "Sugar Bee 重启后仍不健康" "自动恢复失败，需人工登录服务器排查（日志: /var/log/sugar_bee_health.log）" critical
  dump_logs
  exit 3
fi

log "CRIT 30 分钟内已重启过，本次只告警不重启"
dump_logs
exit 4
