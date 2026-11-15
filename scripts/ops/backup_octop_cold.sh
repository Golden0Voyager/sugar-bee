#!/usr/bin/env bash
# Octop 配置库本地加密冷备 —— 刻意「不上公网」。
#
# 为什么不并进 backup_to_github.sh：
#   Golden0Voyager/sugar-bee 是 **public** 仓库，而 octop.db 里有微信 iLink bot
#   token、飞书 app_secret、users.password_hash、secrets 表(8 行)、TOTP/WebAuthn
#   凭据。AES-256 加密虽强，但把活凭据放到任何人可永久下载、可离线爆破的位置，
#   泄露后果不是隐私外泄而是账号/通道被接管。2026-11-12 用户明确选择：本地留存。
#
# 能防 / 不能防（务必如实理解，别把它当异地备份）：
#   能防：误删配置、Octop 升级把配置改坏、手工改 settings 改崩、容器内回滚。
#   不能防：整机丢失、系统盘损坏、.backup_pass 丢失 —— 密钥与密文同机，
#           所以「异地容灾」仍然是空的。真要防整机丢失，需另走私有仓库方案。
#
# 加密：gzip + AES-256-CBC(pbkdf2)，复用 /opt/sugar_bee/.backup_pass（600）。
#   密钥必须离线另存；丢了密钥，本地冷备和 GitHub 周备会一起作废。
#   密钥文件本身绝不进冷备目录，也绝不打进任何备份包。
#
# 快照：调用 backup_db.py --cold（SQLite Online Backup API）。octop.db 是 WAL 活库，
#   -wal 常比主库还大，直接 cp 会拿到撕裂且缺事务的副本。明文快照只存在于
#   mktemp 临时目录，trap 保证任何退出路径都删除。
#
# 部署位置：/opt/sugar_bee/backup_octop_cold.sh
# 定时：25 3 * * * （接在 3:10 每日快照、3:20 周日异地备份之后，错开 IO）
#
# 用法：
#   backup_octop_cold.sh            正常执行（落盘 + 轮转 + 通知）
#   backup_octop_cold.sh --dry-run  完整跑快照/加密/解密验证，但不落盘、不轮转
#
# 环境变量（主要用于测试，生产保持默认）：
#   SB_ROOT / SB_OCTOP_DB / SB_PASS_FILE / SB_COLD_DIR / SB_KEEP / SB_MIN_USERS
set -uo pipefail

ROOT="${SB_ROOT:-/opt/sugar_bee}"
SRC="${SB_OCTOP_DB:-/root/.octop/octop.db}"
PASS_FILE="${SB_PASS_FILE:-$ROOT/.backup_pass}"
COLD_DIR="${SB_COLD_DIR:-$ROOT/octop_cold}"
KEEP="${SB_KEEP:-14}"
MIN_USERS="${SB_MIN_USERS:-1}"
BARK_KEY_FILE="${SB_BARK_KEY_FILE:-$ROOT/.bark_key}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

ts() { date '+%F %T %Z'; }
log() { echo "[$(ts)] $*"; }

BARK_KEY=$(cat "$BARK_KEY_FILE" 2>/dev/null || true)
notify() {
  local title="$1" body="$2" level="${3:-critical}"
  [ -n "$BARK_KEY" ] || return 0
  [ "$DRY" = 1 ] && return 0
  python3 - "$BARK_KEY" "$title" "$body" "$level" <<'PYBARK' 2>/dev/null || true
import json, sys, urllib.request
key, title, body, level = sys.argv[1:5]
payload = json.dumps({"title": title, "body": body[:400], "level": level,
                      "group": "sugar-bee"}).encode()
req = urllib.request.Request("https://api.day.app/" + key, data=payload,
                             headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=10)
PYBARK
}

fail() {
  log "ERROR $1"
  notify "Octop 本地冷备失败" "$1"
  exit 1
}

for bin in python3 gzip openssl; do
  command -v "$bin" >/dev/null 2>&1 || fail "缺少命令 $bin"
done
[ -s "$SRC" ] || fail "源库不存在或为空: $SRC"
[ -s "$PASS_FILE" ] || fail "缺少加密密钥: $PASS_FILE"
[ -f "$ROOT/backup_db.py" ] || fail "缺少一致性快照实现: $ROOT/backup_db.py"

STAMP=$(date +%Y%m%d-%H%M)
ASSET="octop_${STAMP}.db.gz.enc"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/sb_octop_cold.XXXXXX") || fail "无法创建临时目录"
# 明文快照和解密回读文件都只在 TMP 里，任何退出路径都必须删干净
trap 'rm -rf "$TMP"' EXIT INT TERM

log "源库 $(stat -c%s "$SRC") 字节，开始一致性快照$( [ "$DRY" = 1 ] && echo ' [dry-run]' || true )"

# ---- 1. 一致性快照（Online Backup API，不碰 WAL 活库） ----
if ! python3 "$ROOT/backup_db.py" --cold "$SRC" "$TMP/octop.db" users > "$TMP/snap.out" 2>&1; then
  fail "一致性快照失败: $(tail -3 "$TMP/snap.out" | tr '\n' ' ')"
fi
SNAP_ROWS=$(grep -oE 'ROWS=[0-9]+' "$TMP/snap.out" | cut -d= -f2)
[ -s "$TMP/octop.db" ] || fail "快照文件为空（backup_db.py 却返回成功）"
log "快照完成: $(stat -c%s "$TMP/octop.db") 字节, users=${SNAP_ROWS:-?} 行"

# ---- 2. gzip + 加密（明文不落盘，管道直连） ----
if ! gzip -c "$TMP/octop.db" | openssl enc -aes-256-cbc -pbkdf2 -salt \
       -pass "file:$PASS_FILE" -out "$TMP/$ASSET"; then
  fail "加密失败，检查 $PASS_FILE 是否可读"
fi
[ -s "$TMP/$ASSET" ] || fail "加密产物为空"
SIZE=$(stat -c%s "$TMP/$ASSET")
log "已加密 -> $ASSET ($SIZE 字节)"

# ---- 3. 本地解密回读：证明密钥对、数据完整、库真的可用 ----
# 不做「只检查文件存在」的假验证：必须解回一个能 integrity_check 的 SQLite 库。
if ! openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$PASS_FILE" \
       -in "$TMP/$ASSET" | gunzip -c > "$TMP/verify.db" 2>/dev/null; then
  fail "解密失败（密钥不对或文件损坏）"
fi

VERIFY=$(python3 - "$TMP/verify.db" "$MIN_USERS" <<'PYVERIFY'
import sqlite3, sys
path, min_users = sys.argv[1], int(sys.argv[2])
try:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ic = con.execute("pragma integrity_check").fetchone()[0]
    have = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    required = {'users', 'agents', 'channels', 'settings'}
    missing = sorted(required - have)
    users = (con.execute("select count(*) from users").fetchone()[0]
             if 'users' in have else 0)
    con.close()
except Exception as e:
    print(f"FAIL 读取失败: {e}")
    sys.exit(0)
if ic != "ok":
    print(f"FAIL 完整性 {ic}")
elif missing:
    print(f"FAIL 缺关键表 {missing}")
elif users < min_users:
    print(f"FAIL 用户表 {users} 行 < {min_users}")
else:
    print(f"OK {users} 用户 / 关键表齐全 / integrity_check=ok")
PYVERIFY
)
case "$VERIFY" in
  OK*) log "回读验证通过: $VERIFY" ;;
  *)   fail "回读验证失败: $VERIFY" ;;
esac

if [ "$DRY" = 1 ]; then
  log "[dry-run] 快照/加密/解密全链路通过，未落盘、未轮转、未推送"
  exit 0
fi

# ---- 4. 落盘：目录 700、密文 600（含活凭据，别让同机其他用户读到） ----
mkdir -p "$COLD_DIR" || fail "无法创建冷备目录 $COLD_DIR"
chmod 700 "$COLD_DIR"
mv -f "$TMP/$ASSET" "$COLD_DIR/$ASSET" || fail "落盘失败"
chmod 600 "$COLD_DIR/$ASSET"
log "已落盘: $COLD_DIR/$ASSET"

# ---- 5. 只留最近 KEEP 份 ----
REMOVED=0
while IFS= read -r old; do
  [ -n "$old" ] || continue
  rm -f "$old" && REMOVED=$((REMOVED + 1))
done < <(ls -1t "$COLD_DIR"/octop_*.db.gz.enc 2>/dev/null | tail -n +$((KEEP + 1)))
log "轮转完成: 删除 $REMOVED 份，保留最多 $KEEP 份"

# 成功不再推送（2026-11-15）：备份正常是默认预期，每天一条「完成」只会稀释
# 真告警的信噪比。失败分支仍会 notify。需要确认时看 /var/log/sugar_bee_octop_cold.log。
log "OK 本地冷备完成并已验证（注意：不是异地备份）"
exit 0
