#!/usr/bin/env bash
# 生成自签 CA + 服务器证书（用于无备案域名的中国大陆服务器）
#
# 为什么需要它：大陆服务器上未备案的域名过不了 Let's Encrypt 的 HTTP-01 验证
# （会被 DNSPod 拦截页挡掉，返回 566）。唯一能拿到"浏览器可信任"证书的路是
# 自建 CA + 在每台设备上装一次根证书——加密强度与 LE 完全相同，差别只在担保方。
#
# 用法：
#   gen_selfsigned_cert.sh                 # 默认用本机公网 IP
#   gen_selfsigned_cert.sh 1.2.3.4         # 指定 IP
#   gen_selfsigned_cert.sh 1.2.3.4 my.host # IP + 域名（可多个 SAN）
#
# 产出（/etc/ssl/sugarbee/）：
#   ca.key      CA 私钥（600，绝不外传）
#   ca.crt      CA 证书（10 年，分发给设备安装信任）
#   server.key  服务器私钥（600）
#   server.crt  服务器证书（1 年）
#   fullchain.crt  server.crt + ca.crt（nginx ssl_certificate 指向这个）
#
# 续期：每年重跑一次 --renew（复用已有 CA，只重签服务器证书，
# 这样已装过 CA 的设备无需再操作）。
set -euo pipefail

SSL_DIR=/etc/ssl/sugarbee
CA_DAYS=3650
CERT_DAYS=365
MODE="create"
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --renew) MODE="renew"; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [ ${#ARGS[@]} -eq 0 ]; then
  IP=$(curl -s -m 5 https://ifconfig.me 2>/dev/null || true)
  [ -n "$IP" ] || { echo "无法自动获取公网 IP，请手动传入" >&2; exit 1; }
  ARGS=("$IP")
fi

# 构造 SAN 列表：纯 IPv4 用 IP:，其余用 DNS:
SAN=""
for a in "${ARGS[@]}"; do
  if [[ "$a" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SAN="${SAN}IP:${a},"
  else
    SAN="${SAN}DNS:${a},"
  fi
done
SAN="${SAN%,}"
CN="${ARGS[0]}"

mkdir -p "$SSL_DIR"
chmod 700 "$SSL_DIR"
cd "$SSL_DIR"

if [ "$MODE" = "create" ] || [ ! -f ca.key ] || [ ! -f ca.crt ]; then
  echo "[1/3] 生成 CA（${CA_DAYS} 天）"
  openssl genrsa -out ca.key 4096 2>/dev/null
  openssl req -x509 -new -nodes -key ca.key -sha256 -days "$CA_DAYS" -out ca.crt \
    -subj "/C=CN/O=SugarBee Home/CN=SugarBee Local CA" 2>/dev/null
else
  echo "[1/3] 复用已有 CA（已装过 CA 的设备无需重装）"
fi

echo "[2/3] 生成服务器证书（${CERT_DAYS} 天，SAN=${SAN}）"
openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -out server.csr -subj "/C=CN/O=SugarBee Home/CN=${CN}" 2>/dev/null
cat > san.cnf <<EOF
subjectAltName = ${SAN}
extendedKeyUsage = serverAuth
EOF
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days "$CERT_DAYS" -sha256 -extfile san.cnf 2>/dev/null

echo "[3/3] 生成 fullchain（nginx 要用完整链，否则客户端报 unable to verify）"
cat server.crt ca.crt > fullchain.crt

chmod 600 ca.key server.key
chmod 644 ca.crt server.crt fullchain.crt
rm -f server.csr san.cnf

echo
echo "完成。证书信息："
openssl x509 -in server.crt -noout -subject -dates -ext subjectAltName | sed 's/^/  /'
echo
echo "下一步："
echo "  1) nginx 的 ssl_certificate     -> ${SSL_DIR}/fullchain.crt"
echo "  2) nginx 的 ssl_certificate_key -> ${SSL_DIR}/server.key"
echo "  3) 把 ${SSL_DIR}/ca.crt 拷到每台要访问的设备上安装并设为「始终信任」"
echo "  4) 一年后续期：$0 --renew ${ARGS[*]}"
