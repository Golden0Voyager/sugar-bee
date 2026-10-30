#!/usr/bin/env bash
# 重建 sugar-bee 容器。环境变量全部来自 /opt/sugar_bee/app/.env（唯一事实来源）。
# 启动失败或健康检查不通过会自动回滚到上一个容器。
#
# 为什么这个脚本要进仓库：它此前只存在于服务器 /opt/sugar_bee/，不在 git 里。
# 历史教训——时间戳护栏补丁只存在于线上容器，重部署差点丢失。
# 现在仓库是唯一事实来源，改完代码跑一次 scripts/ops/install_ops.sh 即可同步。
#
# 用法:
#   ./run_container.sh --dry-run   只打印将执行的命令
#   ./run_container.sh             实际执行
set -uo pipefail

# 与 health_guard.sh 共用互斥锁：部署/守卫互不干扰。
# 不加锁时，部署窗口（停容器→改名→起新容器，最长 90s）内 cron 撞进来会
# 误判"容器不存在"或误重启（review 实测评估每次部署 5~15% 概率撞上）。
exec 9>/var/run/sugar_bee.deploy.lock
if ! flock -n 9; then
  echo "!! 另一个部署/守卫正在执行（/var/run/sugar_bee.deploy.lock），本次退出"
  exit 1
fi

APP=/opt/sugar_bee/app
NAME=sugar-bee
PREV="${NAME}-prev"
IMAGE=sugar-bee:latest
HEALTH_URL=http://127.0.0.1:5000/health
HEALTH_BUDGET="${HEALTH_BUDGET:-90}"   # 健康检查总预算（秒）

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

run_cmd=(docker run -d --name "$NAME"
  --restart unless-stopped
  --network app_default
  -u appuser -w /app
  --log-opt max-size=10m --log-opt max-file=3
  -p 127.0.0.1:5000:5000
  --env-file "$APP/.env"
  -v "$APP/backups:/app/backups:rw"
  -v "$APP/static/avatars:/app/static/avatars:rw"
  -v "$APP/.garmin_tokens:/app/.garmin_tokens:rw"
  -v "$APP/user_data:/app/user_data:rw"
  -v "$APP/db_data:/app/db_data:rw"
  "$IMAGE")

if [ "$DRY" = 1 ]; then
  echo "[dry-run] 将执行:"
  printf '  %q \\\n' "${run_cmd[@]}"
  echo "[dry-run] 未做任何变更"
  exit 0
fi

container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]
}

# 部署前的硬闸门：挂载的数据库目录里必须有 .db 文件。
#
# 教训（回滚演练实测）：把 .env 里的 SUGAR_BEE_DB_PATH 指到一个不存在的路径，
# 应用启动时会自动建一个空库并把表全建好——探活全绿、部署"成功"，
# 但家人的数据其实连不上了（看起来像数据全没了）。
# 比服务挂掉更危险，因为一切都是绿的。必须在启动新容器之前就拦住。
if [ ! -s "$APP/db_data/glucose.db" ]; then
  echo "!! 中止：$APP/db_data/glucose.db 不存在或为空" >&2
  echo "!! 继续下去应用会自己建一个空库，家人会看到数据全没了。" >&2
  echo "!! 请检查挂载点与 .env 的 SUGAR_BEE_DB_PATH。" >&2
  exit 1
fi

# 健康检查：按截止时间等，不按"次数 × sleep"。
#
# 教训（回滚演练实测）：旧写法是 for i in $(seq 1 40) + sleep 2，
# 一次完整失败要等满 80 秒纯轮询。两个问题：
#   1) 容器启动即崩（比如代码语法错误）时，curl 一直 connection refused，
#      白等 40 轮 —— 而此时 docker inspect 一下就知道容器已经 exited。
#   2) 每次迭代的实际耗时是"curl 超时 + sleep"，不是 sleep，
#      按次数算出来的预算和真实耗时对不上。
# 现在：容器一退出就立刻失败；预算按墙钟时间算，间隔压到 1 秒。
health_ok() {
  local budget="${1:-$HEALTH_BUDGET}" deadline out code
  deadline=$(( $(date +%s) + budget ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    # 容器已经退出 = 根本起不来，继续等没有意义
    if ! container_running; then
      return 1
    fi
    out=$(curl -sS --max-time 3 -w '\n%{http_code}' "$HEALTH_URL" 2>/dev/null) || true
    code=$(printf '%s' "$out" | tail -1)
    if [ "$code" = "200" ]; then
      return 0
    fi
    # 应用能应答但状态不对（503）：/health 已经告诉我们是哪一项坏了，
    # 重试同一个容器不会有帮助（空的库不会自己长出数据，坏的模板也不会自己好），
    # 立刻判失败，把时间留给回滚。
    if printf '%s' "$out" | grep -q '"checks"'; then
      return 1
    fi
    sleep 1
  done
  return 1
}

rollback() {
  echo "!! $1，开始回滚"
  docker logs --tail 25 "$NAME" 2>&1 | sed 's/^/   /' || true
  # rename 失败不能吞：否则 docker start 起的可能是"没删掉的新容器"，
  # 打印"已回滚成功"实际跑的是坏版本（review 发现的静默吞错路径）
  if ! docker rm -f "$NAME" >/dev/null 2>&1; then
    echo "!!! 无法删除失败的容器 $NAME（可能卡在 removing），请人工介入" >&2
    exit 1
  fi
  if ! docker rename "$PREV" "$NAME"; then
    echo "!!! rename $PREV -> $NAME 失败，旧容器以 $PREV 名义保留，请人工恢复" >&2
    exit 1
  fi
  docker start "$NAME" >/dev/null 2>&1 || true
  if health_ok; then
    echo "!! 已回滚，旧容器运行正常"
  else
    echo "!!! 回滚后健康检查仍失败，需人工介入"
  fi
  exit 1
}

docker rm -f "$PREV" >/dev/null 2>&1 || true
# -t 35：gunicorn graceful_timeout=30s，默认 10s 会 SIGKILL 腰斩在途请求（Garmin 同步类最长 120s）
docker stop -t 35 "$NAME" >/dev/null 2>&1 || { echo "找不到容器 $NAME"; exit 1; }
docker rename "$NAME" "$PREV" || { echo "重命名失败"; exit 1; }

echo "启动新容器..."
"${run_cmd[@]}" >/dev/null || rollback "docker run 失败"

if health_ok; then
  echo "健康检查通过"
  # 容器 /app 代码烤在镜像里，重建后会退回原版；重新贴上本地补丁。
  # 说明：镜像内的 writer.py 已自带时间戳护栏（mcp_adapter/core/tsguard.py），
  # 这个脚本现在是幂等的冗余保险，保留是为了防止有人用旧镜像构建。
  if [ -f /root/health_tools/apply_sugar_bee_patch.sh ]; then
    bash /root/health_tools/apply_sugar_bee_patch.sh \
      || echo "  !! 时间戳护栏同步失败，请手动执行 bash /root/health_tools/apply_sugar_bee_patch.sh"
  fi
  docker rm -f "$PREV" >/dev/null 2>&1 || true
  echo "旧容器已清理"
  docker ps --filter "name=$NAME" --format "  {{.Names}}  {{.Status}}  {{.Ports}}"
  exit 0
fi

rollback "健康检查未通过"
