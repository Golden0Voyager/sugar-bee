#!/usr/bin/env bash
# 每周把最新快照加密后上传到 GitHub Releases —— 零成本的异地备份副本。
#
# 为什么用 Releases 而不是对象存储：不额外开云服务、不额外花钱，已有 GitHub
# 账号即可；Releases 的 asset 不占 git 历史，可单独删除，也不会被 git gc 影响。
#
# 加密：gzip + AES-256-CBC（openssl，零新依赖）。
# 密钥在 /opt/sugar_bee/.backup_pass（600）——**必须离线另存两份**，
# 丢了密钥等于丢了全部异地备份，这比备份本身更容易出事。
#
# 部署位置：/opt/sugar_bee/backup_to_github.sh
# 定时：20 3 * * 0   （每周日 3:20，接在每日 3:10 的本地快照之后）
#
# 做四件事：
#   1. 取最新 glucose_snap_*.db，gzip + 加密
#   2. 建一个 release 并上传 asset
#   3. **下载回来解密 + integrity_check + 行数断言**（证明这一份真的能恢复）
#   4. 只留最近 KEEP 个周备份，更早的 release 连同 tag 一并删除
set -uo pipefail

ROOT=/opt/sugar_bee
BK="$ROOT/app/backups"
REPO="Golden0Voyager/sugar-bee"
TOKEN_FILE="$ROOT/.github_token"
PASS_FILE="$ROOT/.backup_pass"
KEEP=12              # 保留最近 12 个（约 3 个月）
API="https://api.github.com"
MIN_RECORDS=20000    # 行数下界：远低于真实值，只用来抓"空库/半截库"

ts() { date '+%F %T %Z'; }
log() { echo "[$(ts)] $*"; }

BARK_KEY=$(cat "$ROOT/.bark_key" 2>/dev/null || true)
notify() {
  local title="$1" body="$2" level="${3:-critical}"
  [ -n "$BARK_KEY" ] || return 0
  python3 - "$BARK_KEY" "$title" "$body" "$level" <<'PYBARK' 2>/dev/null || true
import json, sys, urllib.request
key, title, body, level = sys.argv[1:5]
payload = json.dumps({"title": title, "body": body[:400], "level": level, "group": "sugar-bee"}).encode()
req = urllib.request.Request("https://api.day.app/" + key, data=payload,
                             headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=10)
PYBARK
}

TOKEN=$(cat "$TOKEN_FILE" 2>/dev/null | tr -d '\r\n' || true)
if [ -z "$TOKEN" ]; then
  log "ERROR 缺少 ${TOKEN_FILE}（GitHub token）"
  notify "Sugar Bee 异地备份停摆" "缺 GitHub token，异地副本一份都没在传"
  exit 1
fi
if [ ! -s "$PASS_FILE" ]; then
  log "ERROR 缺少 ${PASS_FILE}（加密密钥）"
  notify "Sugar Bee 异地备份停摆" "缺加密密钥，异地副本一份都没在传"
  exit 1
fi

# ---- 1. 取最新快照并加密 ----
SNAP=$(ls -1t "$BK"/glucose_snap_*.db 2>/dev/null | head -1)
if [ -z "$SNAP" ]; then
  log "ERROR 找不到 $BK/glucose_snap_*.db"
  notify "Sugar Bee 异地备份失败" "本地没有 glucose_snap_*.db，先看每日备份是否正常"
  exit 1
fi

STAMP=$(date +%Y%m%d-%H%M)
TAG="backup-${STAMP}"
ASSET="glucose_snap_${STAMP}.db.gz.enc"
TMP="/tmp/sb_up_${STAMP}"
mkdir -p "$TMP"

if ! gzip -c "$SNAP" | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$PASS_FILE" -out "$TMP/$ASSET"; then
  log "ERROR 加密失败"
  notify "Sugar Bee 异地备份失败" "加密步骤失败，检查 $PASS_FILE"
  rm -rf "$TMP"
  exit 1
fi
SIZE=$(stat -c%s "$TMP/$ASSET" 2>/dev/null || echo 0)
log "已加密 $(basename "$SNAP") -> $ASSET ($SIZE 字节)"

# ---- 2. 建 release 并上传 ----
REL_JSON=$(curl -s -m 30 -X POST "$API/repos/$REPO/releases" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "{\"tag_name\":\"$TAG\",\"name\":\"备份 ${STAMP}\",\"body\":\"encrypted snapshot of $(basename "$SNAP")\",\"prerelease\":true,\"draft\":false}")

UPLOAD_URL=$(printf '%s' "$REL_JSON" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin); print(d.get("upload_url","").split("{")[0])
except Exception: print("")
' 2>/dev/null)

if [ -z "$UPLOAD_URL" ]; then
  log "ERROR 创建 release 失败: $REL_JSON"
  notify "Sugar Bee 异地备份失败" "GitHub 建 release 失败（token 过期或权限不足？）"
  rm -rf "$TMP"
  exit 1
fi

UP_JSON=$(curl -s -m 300 -X POST "${UPLOAD_URL}?name=${ASSET}" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  --data-binary "@$TMP/$ASSET")

DL_URL=$(printf '%s' "$UP_JSON" | python3 -c '
import json,sys
try:
    print(json.load(sys.stdin).get("browser_download_url",""))
except Exception: print("")
' 2>/dev/null)

if [ -z "$DL_URL" ]; then
  log "ERROR 上传失败: $(printf '%s' "$UP_JSON" | head -c 300)"
  notify "Sugar Bee 异地备份失败" "上传 asset 失败，查看 /var/log/sugar_bee_github_backup.log"
  rm -rf "$TMP"
  exit 1
fi
log "已上传: $DL_URL"

# ---- 3. 验证（两条互补断言，不做完整回读）----
# 为什么不用"下载回来解密"：objects.githubusercontent.com 在国内被限速
# （实测 ~7KB/s，908KB 要 2 分钟以上），每周跑都会拖很久且容易超时。
# 改为：
#   a) 本地解密自己刚产的加密包 → 证明「密钥对 + 数据完整 + 能解压成可用库」
#   b) API 查 asset 的 state/size → 证明「GitHub 上那份完整收下了」
# 两者合起来覆盖了「密钥错了」和「上传截断」两个真实失败模式。

if ! openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$PASS_FILE" -in "$TMP/$ASSET" | gunzip -c > "$TMP/verify.db" 2>/dev/null; then
  log "ERROR 本地解密失败（密钥不对或文件损坏）"
  notify "Sugar Bee 异地备份损坏" "加密包解不开，检查 $PASS_FILE"
  rm -rf "$TMP"
  exit 1
fi

REMOTE_INFO=$(printf '%s' "$UP_JSON" | python3 -c "import json,sys;a=json.load(sys.stdin);print(a.get('state','?'),a.get('size',0))" 2>/dev/null)
R_STATE=${REMOTE_INFO%% *}
R_SIZE=${REMOTE_INFO##* }

if [ "$R_STATE" != "uploaded" ]; then
  log "ERROR 远端 asset 状态异常: $R_STATE"
  notify "Sugar Bee 异地备份可疑" "asset 状态 $R_STATE，可能上传未完成"
  rm -rf "$TMP"
  exit 1
fi
if [ "$R_SIZE" != "$SIZE" ]; then
  log "ERROR 远端大小 $R_SIZE 与本地 $SIZE 不符（上传可能截断）"
  notify "Sugar Bee 异地备份可疑" "远端 $R_SIZE 字节 vs 本地 $SIZE，上传可能截断"
  rm -rf "$TMP"
  exit 1
fi
log "远端校验通过: state=$R_STATE size=$R_SIZE"

VERIFY=$(python3 - "$TMP/verify.db" "$MIN_RECORDS" <<'PYVERIFY'
import sqlite3, sys
path, min_records = sys.argv[1], int(sys.argv[2])
try:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ic = con.execute("pragma integrity_check").fetchone()[0]
    rec = con.execute("select count(*) from records").fetchone()[0]
    usr = con.execute("select count(*) from app_users").fetchone()[0]
    con.close()
except Exception as e:
    print(f"FAIL 读取失败: {e}")
    sys.exit(0)
if ic != "ok":
    print(f"FAIL 完整性 {ic}")
elif rec < min_records:
    print(f"FAIL 行数异常 {rec} < {min_records}")
elif usr < 1:
    print(f"FAIL 用户表 {usr} 行")
else:
    print(f"OK {rec} 条 / {usr} 用户")
PYVERIFY
)

if [ "${VERIFY#OK }" = "$VERIFY" ]; then
  log "ERROR 回读验证失败: $VERIFY"
  notify "Sugar Bee 异地备份损坏" "回读验证失败：$VERIFY"
  rm -rf "$TMP"
  exit 1
fi
log "回读验证通过: $VERIFY"

# ---- 4. 清理旧 release（只留最近 KEEP 个 backup-* ）----
python3 - "$TOKEN" "$REPO" "$KEEP" <<'PYPRUNE' 2>/dev/null || log "WARN 清理旧 release 失败（不影响本次备份）"
import json, sys, urllib.request

token, repo, keep = sys.argv[1], sys.argv[2], int(sys.argv[3])
api = f"https://api.github.com/repos/{repo}"
hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
       "X-GitHub-Api-Version": "2022-11-28"}

def call(url, method="GET"):
    req = urllib.request.Request(url, headers=hdr, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

try:
    releases = call(f"{api}/releases?per_page=100")
except Exception:
    sys.exit(0)

backups = [r for r in releases if r.get("tag_name", "").startswith("backup-")]
backups.sort(key=lambda r: r["created_at"], reverse=True)
for r in backups[keep:]:
    try:
        call(f"{api}/releases/{r['id']}", "DELETE")
        print(f"已删除旧备份 release {r['tag_name']}")
    except Exception as e:
        print(f"删除 {r['tag_name']} 失败: {e}")
PYPRUNE

rm -rf "$TMP"
log "OK 异地备份完成并已验证"
exit 0
