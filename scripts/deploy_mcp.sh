#!/bin/bash
# Sugar Bee —— 两个 MCP server 一起发布到云服务器（统一目录 /root/health_tools）
#
# 用法：
#   ./deploy_mcp.sh --dry-run        # 只看会传什么，不动服务器
#   ./deploy_mcp.sh                  # 发布（reader + writer），不重启 Octop
#   ./deploy_mcp.sh --restart        # 发布并重启 Octop
#   ./deploy_mcp.sh --only reader    # 只发布只读分析服务
#
# 服务器布局（两个功能，一个包，两个入口）：
#   /root/health_tools/
#   ├── mcp_reader.py        只读门牌 → mcp_adapter.servers.reader（12 工具）
#   ├── mcp_writer.py        写入门牌 → mcp_adapter.servers.writer（11 工具）
#   ├── mcp_adapter/{core,servers}
#   ├── utils/{timezone,db,sql_dialect}.py
#   ├── settings.py          纯常量模块，writer 的血糖达标判定/emoji 映射依赖它
#   └── core/config.py       utils/db.py 依赖它
#
# 为什么 writer 也要搬过来：
#   原来它跑在 /opt/sugar_bee/app（Flask 源码目录），_db() 默认打开
#   PROJECT_ROOT/glucose.db，而那个文件不存在 —— SQLite 会静默新建空库，
#   batch_record / today_summary 等 6 个工具实际是坏的。搬到 health_tools 并显式
#   指定 DB_PATH 后，读服务和写服务用的是同一个库、同一份 core 规则。
#
# 主机优先级：SB_HOST 环境变量 > ~/.sugar_bee_host 文件 > 内置默认

set -uo pipefail

SSH_HOST="${SB_HOST:-$(cat "$HOME/.sugar_bee_host" 2>/dev/null || echo 124.220.181.225)}"
SSH_USER="${SB_USER:-root}"
SSH_KEY="${SB_KEY:-$HOME/.ssh/lighthouse_sh}"
REMOTE_DIR="${SB_REMOTE_DIR:-/root/health_tools}"
REMOTE_PY="${SB_REMOTE_PY:-/root/.octop/venv/bin/python}"
REMOTE_DB="${SB_REMOTE_DB:-/opt/sugar_bee/app/db_data/glucose.db}"
REMOTE_PYLIBS="${SB_REMOTE_PYLIBS:-$REMOTE_DIR/pylibs}"
REMOTE_ENV="${SB_REMOTE_ENV:-/opt/sugar_bee/app/.env}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
RESTART=0
VERIFY=1
ONLY="both"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --restart) RESTART=1 ;;
    --no-verify) VERIFY=0 ;;
    --only) shift; ONLY="${1:-both}" ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "未知参数：$1"; exit 2 ;;
  esac
  shift
done
case "$ONLY" in both|reader|writer) ;; *) echo "--only 只能是 reader / writer / both"; exit 2 ;; esac

log() { printf '%s [%s] %s\n' "$(date '+%H:%M:%S')" "$1" "$2"; }
die() { log ERROR "$2"; exit 1; }

SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15)
RSYNC_SSH="ssh ${SSH_OPTS[*]}"
RSYNC_OPTS=(-az --exclude __pycache__ --exclude '*.pyc')
[ "$DRY_RUN" = "1" ] && RSYNC_OPTS+=(--dry-run --itemize-changes)

# ---- 1. 本地预检 ----
log INFO "仓库根：$REPO_ROOT"
NEED=(
  mcp_adapter/__init__.py
  mcp_adapter/core/domain.py mcp_adapter/core/timefmt.py mcp_adapter/core/analytics.py
  utils/timezone.py utils/db.py utils/sql_dialect.py core/config.py
)
# settings.py 必须一起传：writer.py 里有 5 处 `from settings import ...`
# （check_glucose_compliance / EMOJI_USER_MAP / USER_EMOJI_MAP）。
# 它是纯常量模块，只依赖 utils.timezone，不 import Flask，可以安全独立部署。
[ "$ONLY" != "reader" ] && NEED+=(settings.py)
[ "$ONLY" != "writer" ] && NEED+=(mcp_adapter/servers/reader.py mcp_reader.py)
[ "$ONLY" != "reader" ] && NEED+=(mcp_adapter/servers/writer.py mcp_writer.py)
for f in "${NEED[@]}"; do
  [ -f "$REPO_ROOT/$f" ] || die x "缺少文件：$f"
done

if command -v uv >/dev/null 2>&1; then
  ( cd "$REPO_ROOT" && uv run python -c "
import ast, pathlib
for p in ['mcp_adapter/core/domain.py','mcp_adapter/core/timefmt.py',
          'mcp_adapter/core/analytics.py','mcp_adapter/servers/reader.py',
          'mcp_adapter/servers/writer.py','utils/timezone.py','utils/db.py',
          'utils/sql_dialect.py','core/config.py','mcp_writer.py','mcp_reader.py',
          'settings.py']:
    ast.parse(pathlib.Path(p).read_text(encoding='utf-8'))
print('syntax ok')
" ) || die x "本地语法检查失败，已中止"
else
  log WARN "未找到 uv，跳过语法检查"
fi

# ---- 2. 远端建目录 + 备份旧入口 ----
ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" \
    "mkdir -p '$REMOTE_DIR/mcp_adapter' '$REMOTE_DIR/utils' '$REMOTE_DIR/core'" \
    || die x "无法连接服务器或创建目录失败"

if [ "$DRY_RUN" = "0" ]; then
  STAMP=$(date +%Y%m%d_%H%M%S)
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" \
      "for f in mcp_writer.py mcp_reader.py; do \
         [ -f '$REMOTE_DIR/'\$f ] && cp '$REMOTE_DIR/'\$f '$REMOTE_DIR/'\$f.bak_$STAMP && echo \"backup \$f\"; \
       done; true" 2>&1 | tail -3
fi

# ---- 3. 同步代码 ----
rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
  --exclude 'tests' --exclude '*.md' --exclude 'server.py' \
  "$REPO_ROOT/mcp_adapter/" "$SSH_USER@$SSH_HOST:$REMOTE_DIR/mcp_adapter/" \
  || die x "同步 mcp_adapter 失败"

for f in utils/timezone.py utils/db.py utils/sql_dialect.py; do
  rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
    "$REPO_ROOT/$f" "$SSH_USER@$SSH_HOST:$REMOTE_DIR/$f" \
    || die x "同步 $f 失败"
done

rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/core/config.py" "$SSH_USER@$SSH_HOST:$REMOTE_DIR/core/config.py" \
  || die x "同步 core/config.py 失败"

[ "$ONLY" != "reader" ] && { rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/settings.py" "$SSH_USER@$SSH_HOST:$REMOTE_DIR/settings.py" \
  || die x "同步 settings.py 失败"; }

[ "$ONLY" != "writer" ] && { rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/mcp_reader.py" "$SSH_USER@$SSH_HOST:$REMOTE_DIR/mcp_reader.py" \
  || die x "同步 reader 入口失败"; }

[ "$ONLY" != "reader" ] && { rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/mcp_writer.py" "$SSH_USER@$SSH_HOST:$REMOTE_DIR/mcp_writer.py" \
  || die x "同步 writer 入口失败"; }

log INFO "文件同步完成"
[ "$DRY_RUN" = "1" ] && { log INFO "dry-run 结束，未改动服务器"; exit 0; }

# ---- 4. 远端自检：两个入口都用 Octop 的 venv 真跑一次 ----
if [ "$VERIFY" = "1" ]; then
  check=$(ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" "
cd '$REMOTE_DIR' || exit 1
TOKEN=\$(grep '^AGENT_API_TOKEN' '$REMOTE_ENV' 2>/dev/null | head -1 | cut -d= -f2-)
export PYTHONPATH='$REMOTE_PYLIBS'
export API_BASE=http://127.0.0.1:5000
export AGENT_API_TOKEN=\"\$TOKEN\"
export DB_PATH='$REMOTE_DB'
'$REMOTE_PY' - <<'PYEOF'
import importlib.util, json

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

out = {}
if __import__('os').path.exists('mcp_reader.py'):
    shim = load('shim_reader', 'mcp_reader.py')
    from mcp_adapter.servers import reader
    out['reader'] = {'entry': shim.main.__module__,
                     'tools': len([n for n in dir(reader) if n.startswith('sbhealth_')]),
                     'db': reader.DB_PATH}
if __import__('os').path.exists('mcp_writer.py'):
    shim = load('shim_writer', 'mcp_writer.py')
    from mcp_adapter.servers import writer
    names = [n for n in dir(writer) if n.startswith('record_') or n in
             ('parse_and_record','batch_record','batch_parse_and_record','undo_last_record',
              'today_summary','list_today_records','get_user_info')]
    info = __import__('asyncio').run(writer.get_user_info(1))
    # 这几个是之前漏同步 settings.py 时挂掉的路径，必须显式验证
    import settings as _s
    summary = __import__('asyncio').run(writer.today_summary(1))
    out['writer'] = {'entry': shim.main.__module__, 'tools': len(names),
                     'db': writer.DB_PATH, 'user_info_ok': bool(info),
                     'settings_ok': bool(_s.EMOJI_USER_MAP),
                     'compliance_ok': bool(_s.check_glucose_compliance(6.0, '空腹')['is_compliant']),
                     'summary_ok': 'Error executing tool' not in summary}
print(json.dumps(out, ensure_ascii=False))
PYEOF
" 2>&1 | tail -3)
  if printf '%s' "$check" | grep -Eq '"reader"|"writer"'; then
    log INFO "远端自检通过：$check"
  else
    die x "远端自检失败：$check"
  fi
fi

# ---- 5. 可选重启 ----
if [ "$RESTART" = "1" ]; then
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$SSH_HOST" \
      "systemctl restart octop && systemctl is-active octop" 2>&1 | tail -2
  log INFO "Octop 已重启"
else
  log INFO "未重启 Octop（新会话会拉起新进程；改 connector 路径后建议加 --restart）"
fi
