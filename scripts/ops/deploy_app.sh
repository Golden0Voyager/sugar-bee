#!/usr/bin/env bash
# 本地 → 服务器 的代码发布（唯一入口）。
#
# 为什么要有它：以前 rsync 命令只存在于人脑里。本地工作树躺着 glucose.db(7M)
# 和 backups/(90M)，一次手滑就能把**线上库覆盖成旧副本** —— 部署前的空库闸门
# 拦不住"非空旧库覆盖"，恢复时才发现数据倒退几天。
#
# 这个脚本把两件事写死：
#   1. 数据路径硬排除：db_data/ backups/ .env .garmin_tokens/ avatars/ user_data/
#      以及任何 *.db —— 代码归代码，数据归数据，永不同框传输；
#   2. 推送前后比对线上库指纹（行数/大小/mtime）：任何变化立即中止，
#      因为"部署代码"不该动数据库，动了就说明排除漏了。
#
# 用法:
#   scripts/ops/deploy_app.sh --dry-run      只打印将要做什么（不连服务器也不改任何东西）
#   scripts/ops/deploy_app.sh                推送代码 + 重建容器 + 探活
#   scripts/ops/deploy_app.sh --no-restart   只推代码，不重启容器
#
# 环境变量:
#   SB_HOST       必填，user@host（例如 root@1.2.3.4）
#   SB_PORT       SSH 端口，默认 22
#   SB_OPS_DIR    服务器上的运维目录，默认 /opt/sugar_bee
#   SB_APP_DIR    服务器上的应用目录，默认 $SB_OPS_DIR/app
#   SB_DB         服务器上的库路径，默认 $SB_APP_DIR/db_data/glucose.db
#   SB_ALLOW_DB_CHANGE=1  显式允许"推送期间线上库指纹变化"（一般不要设）
set -uo pipefail

SB_HOST="${SB_HOST:-$(cat "$HOME/.sugar_bee_host" 2>/dev/null || true)}"
SB_PORT="${SB_PORT:-22}"
SB_KEY="${SB_KEY:-$HOME/.ssh/sugar_bee_new_agent}"
SB_OPS_DIR="${SB_OPS_DIR:-/opt/sugar_bee}"
SB_APP_DIR="${SB_APP_DIR:-$SB_OPS_DIR/app}"
SB_DB="${SB_DB:-$SB_APP_DIR/db_data/glucose.db}"

LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

DRY=0
RESTART=1
for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY=1 ;;
    --no-restart) RESTART=0 ;;
    -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "未知参数: ${arg}（--help 看用法）" >&2; exit 2 ;;
  esac
done

ts() { date '+%F %T'; }
say() { echo "[$(ts)] $*"; }
die() { echo "[$(ts)] !! $*" >&2; exit 1; }

# ---------- 1. 预检 ----------
[ -n "$SB_HOST" ] || [ "$DRY" = 1 ] || die "未设置 SB_HOST（例如 SB_HOST=root@1.2.3.4 $0）"
command -v rsync >/dev/null || die "本机没有 rsync"
[ -f "$LOCAL_DIR/app.py" ] || die "看起来不是仓库根目录: $LOCAL_DIR"

SSH_OPTS=(-p "$SB_PORT" -o ConnectTimeout=10 -o BatchMode=yes -o IdentitiesOnly=yes)
[ -f "$SB_KEY" ] && SSH_OPTS+=(-i "$SB_KEY")
remote() { ssh "${SSH_OPTS[@]}" "$SB_HOST" "$@"; }

SSH_E="ssh -p $SB_PORT -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes"
[ -f "$SB_KEY" ] && SSH_E="$SSH_E -i $SB_KEY"

# 绝不允许跨机传输的路径（相对仓库根）。新增数据目录时必须加到这里。
EXCLUDES=(
  --exclude=.git/
  --exclude=.env
  --exclude=.env.*
  --exclude=db_data/
  --exclude=backups/
  --exclude=.garmin_tokens/
  --exclude=user_data/
  --exclude=static/avatars/
  --exclude='*.db'
  --exclude='*.db-wal'
  --exclude='*.db-shm'
  --exclude='*.db-journal'
  --exclude=.venv/
  --exclude=__pycache__/
  --exclude='*.pyc'
  --exclude=.pytest_cache/
  --exclude=.pytest_tmp/
  --exclude=.ruff_cache/
  --exclude=.coverage
  --exclude=htmlcov/
  --exclude=.worktrees/
  --exclude=.workbuddy-ai/
  --exclude=.DS_Store
  --exclude=tests/
  --exclude='test_*.py'
  --exclude=docs/
  --exclude='*.md'
)

RSYNC_CMD=(rsync -az --delete --human-readable --stats "${EXCLUDES[@]}"
           -e "$SSH_E"
           "$LOCAL_DIR/" "$SB_HOST:$SB_APP_DIR/")

if [ "$DRY" = 1 ]; then
  say "[dry-run] 不会连接服务器、不会改任何文件。将执行:"
  echo "  排除规则:"
  for e in "${EXCLUDES[@]}"; do [ "$e" != "--exclude" ] && echo "    $e"; done
  echo "  同步命令:"
  echo "    ${RSYNC_CMD[*]}"
  echo "  线上库指纹比对: $SB_HOST:$SB_DB"
  [ "$RESTART" = 1 ] && echo "  重建容器: ssh $SB_HOST $SB_OPS_DIR/run_container.sh"
  echo
  say "[dry-run] 顺便列出本地会被排除的大文件（确认没混进推送）:"
  find "$LOCAL_DIR" -maxdepth 1 \( -name '*.db' -o -name 'backups' -o -name 'db_data' \) \
    -exec du -sh {} \; 2>/dev/null || true
  exit 0
fi

# ---------- 2. 推送前：记录线上库指纹 ----------
# 指纹 = records 行数 + 文件大小 + mtime。任何一个变了都说明数据被动过。
db_fingerprint() {
  remote "python3 - <<'PY'
import os, sqlite3
p = '$SB_DB'
if not os.path.exists(p):
    print('MISSING')
    raise SystemExit(0)
try:
    conn = sqlite3.connect(p, timeout=10)
    n = conn.execute('select count(*) from records').fetchone()[0]
    conn.close()
except Exception as e:
    print('ERROR:' + type(e).__name__)
    raise SystemExit(0)
st = os.stat(p)
print(f'COUNT={n} SIZE={st.st_size} MTIME={int(st.st_mtime)}')
PY"
}

say "读取线上库指纹…"
BEFORE="$(db_fingerprint)" || die "无法读取线上库指纹（ssh 不通或库不可读）"
say "推送前: $BEFORE"
case "$BEFORE" in
  MISSING) say "线上库不存在（首次部署？），跳过指纹比对" ;;
  ERROR:*) die "线上库读不了: $BEFORE —— 先修数据库，再谈部署" ;;
esac

# ---------- 3. 推代码 ----------
say "推送代码到 $SB_HOST:$SB_APP_DIR …"
"${RSYNC_CMD[@]}" || die "rsync 失败"

# ---------- 4. 推送后：指纹必须一模一样 ----------
if [ "${SB_ALLOW_DB_CHANGE:-0}" != "1" ]; then
  AFTER="$(db_fingerprint)"
  say "推送后: $AFTER"
  if [ "$BEFORE" != "$AFTER" ]; then
    die "线上库指纹变了！代码推送不该动数据，说明排除规则漏了：
  推送前: $BEFORE
  推送后: $AFTER
立即检查 rsync 的 --exclude 列表，并从备份恢复（见 docs/RUNBOOK.md）"
  fi
  say "指纹一致（${AFTER}）—— 数据没被动过"
else
  say "SB_ALLOW_DB_CHANGE=1，跳过指纹比对（你确定？）"
fi

# ---------- 5. 重建容器 + 探活 ----------
if [ "$RESTART" = 1 ]; then
  # 必须先 docker build：run_container.sh 只重建容器、不重建镜像。
  # 少了这一步，代码推上去了但容器还在跑旧镜像 —— 探活照样绿（旧代码也健康），
  # 就是典型的"部署假成功"。（2026-10-30 真实踩过：WAL 代码同步到磁盘后
  # 容器里 journal_mode 仍是 delete，直到手动 build 才生效。）
  say "重建镜像（docker build）…"
  remote "cd ${SB_APP_DIR} && TS=\$(date +%Y%m%d-%H%M) && docker build -q -t sugar-bee:\$TS -t sugar-bee:latest . >/dev/null && echo \"  镜像 sugar-bee:\$TS\"" \
    || die "镜像构建失败（容器未动，线上还是旧版本）"

  say "重建容器（run_container.sh 自带探活 + 15 秒自动回滚）…"
  remote "$SB_OPS_DIR/run_container.sh" || die "容器重建失败（已由 run_container.sh 回滚，去看 /var/log 与 docker logs sugar-bee）"
  # 防"部署假成功"：容器内 app.py 必须与宿主机 rsync 上去的那份一致。
  # 不一致说明镜像没重建（或 build 缓存吃掉了新代码），此时探活仍会绿。
  say "校验容器内代码与宿主机一致…"
  HOST_MD5=$(remote "md5sum ${SB_APP_DIR}/app.py | cut -d' ' -f1")
  CONT_MD5=$(remote "docker exec sugar-bee md5sum /app/app.py | cut -d' ' -f1")
  [ -n "$HOST_MD5" ] && [ "$HOST_MD5" = "$CONT_MD5" ] \
    || die "容器内 app.py(${CONT_MD5}) 与宿主机(${HOST_MD5}) 不一致——镜像没吃进新代码，本次部署无效"

  say "容器已起，最后确认一次线上指纹没变…"
  FINAL="$(db_fingerprint)"
  [ "$FINAL" = "$BEFORE" ] || say "注意：容器启动后指纹为 ${FINAL}（与推送前不同，通常是后台任务写入，非覆盖）"
else
  say "按 --no-restart 跳过容器重建（代码已就位，下次重启生效）"
fi

say "完成。回滚办法：git checkout <上一个 commit> 后重跑本脚本。"
