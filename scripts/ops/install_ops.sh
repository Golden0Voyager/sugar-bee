#!/usr/bin/env bash
# 把 scripts/ops/ 下的运维脚本装到服务器 /opt/sugar_bee/ 并装好 cron。
#
# 为什么要这个脚本：这三个脚本（备份 / 健康守卫）此前只存在于服务器上，
# 不在 git 里。历史教训——时间戳护栏补丁只存在于线上容器，重部署差点丢失。
# 现在仓库是唯一事实来源，改完代码跑一次本脚本即可同步。
#
# 用法：
#   ./scripts/ops/install_ops.sh              # 装到 ~/.sugar_bee_host 指向的机器
#   HOST=1.2.3.4 ./scripts/ops/install_ops.sh
#   ./scripts/ops/install_ops.sh --dry-run
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-$(cat "$HOME/.sugar_bee_host" 2>/dev/null || true)}"
KEY="${KEY:-$HOME/.ssh/sugar_bee_new_agent}"
USER_AT="root@${HOST}"
FILES=(backup_db.py backup_host.sh health_guard.sh run_container.sh)

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

if [ -z "$HOST" ]; then
  echo "ERROR: 不知道目标主机，设置 HOST=... 或写 ~/.sugar_bee_host" >&2
  exit 1
fi

SSH_OPTS=(-o IdentitiesOnly=yes -o ConnectTimeout=10)
[ -f "$KEY" ] && SSH_OPTS+=(-i "$KEY")

echo "目标: $USER_AT"
echo "将同步: ${FILES[*]}"

if [ "$DRY" = 1 ]; then
  scp "${SSH_OPTS[@]}" "${FILES[@]/#/$HERE/}" "$USER_AT:/opt/sugar_bee/" -v 2>&1 | head -3 || true
  echo "[dry-run] 未做任何变更"
  exit 0
fi

scp -q "${SSH_OPTS[@]}" "${FILES[@]/#/$HERE/}" "$USER_AT:/opt/sugar_bee/"

# nginx 站点配置也一起送（远程有 nginx -t 门禁与自动回滚）
NGX="$HERE/nginx/sugar-bee.site.conf"
if [ -f "$NGX" ]; then
  scp -q "${SSH_OPTS[@]}" "$NGX" "$USER_AT:/tmp/ops_nginx_site.conf"
fi

ssh "${SSH_OPTS[@]}" "$USER_AT" 'bash -s' <<'REMOTE'
set -eu
cd /opt/sugar_bee
# 用显式 755 而不是 chmod +x：scp 覆盖已有文件时会保留目标原有权限
# （服务器上历史遗留是 700），chmod +x 只会得到 711。
chmod 755 backup_host.sh health_guard.sh run_container.sh
chmod 644 backup_db.py

# 语法自检，别把坏脚本装进 cron
bash -n backup_host.sh
bash -n health_guard.sh
bash -n run_container.sh
python3 -m py_compile backup_db.py && rm -rf __pycache__

want_backup='10 3 * * * /opt/sugar_bee/backup_host.sh >> /var/log/sugar_bee_backup.log 2>&1'
want_health='*/10 * * * * /opt/sugar_bee/health_guard.sh >/dev/null 2>&1'

crontab -l >/tmp/ct.ops 2>/dev/null || true
grep -qF 'backup_host.sh' /tmp/ct.ops || echo "$want_backup" >>/tmp/ct.ops
grep -qF 'health_guard.sh' /tmp/ct.ops || echo "$want_health" >>/tmp/ct.ops
crontab /tmp/ct.ops && rm -f /tmp/ct.ops

echo "--- 已安装 ---"
ls -l /opt/sugar_bee/backup_db.py /opt/sugar_bee/backup_host.sh /opt/sugar_bee/health_guard.sh /opt/sugar_bee/run_container.sh
echo "--- crontab ---"
crontab -l | grep -E 'backup_host|health_guard'

# ---- nginx 站点配置（仓库为准，防"只改服务器"漂移）----
if [ -f /tmp/ops_nginx_site.conf ]; then
  CUR=/etc/nginx/sites-available/sugar-bee
  if [ -f "$CUR" ] && cmp -s /tmp/ops_nginx_site.conf "$CUR"; then
    echo "--- nginx: 配置一致，跳过 ---"
  else
    cp "$CUR" "/root/sugar-bee.site.bak_$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
    cp /tmp/ops_nginx_site.conf "$CUR"
    if nginx -t >/dev/null 2>&1; then
      nginx -s reload
      echo "--- nginx: 已更新并 reload ---"
    else
      echo "!! nginx -t 失败，已回滚" >&2
      cp "$(ls -t /root/sugar-bee.site.bak_* | head -1)" "$CUR"
      rm -f /tmp/ops_nginx_site.conf
      exit 1
    fi
  fi
  rm -f /tmp/ops_nginx_site.conf
fi
REMOTE

echo "完成。日志: /var/log/sugar_bee_backup.log  /var/log/sugar_bee_health.log"
