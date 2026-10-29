#!/usr/bin/env bash
# 자체 서명 인증서 생성 → edge/certs/{cert.pem,key.pem}
# 사용법: ./scripts/make-cert.sh [PC의 LAN IP]
#   IP 를 생략하면 .env 의 PUBLIC_HOST 를 사용한다.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash 에서 "/CN=..." 이 경로로 변환되는 것 방지

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/edge/certs"

HOST="${1:-}"
if [[ -z "$HOST" && -f "$ROOT/.env" ]]; then
  HOST="$(grep -E '^PUBLIC_HOST=' "$ROOT/.env" | cut -d= -f2- | tr -d '\r"' || true)"
fi
if [[ -z "$HOST" ]]; then
  echo "usage: $0 <LAN IP>   (예: $0 192.168.0.10)" >&2
  exit 1
fi

if [[ "$HOST" =~ ^[0-9.]+$ ]]; then
  SAN="IP:$HOST,DNS:localhost,IP:127.0.0.1"
else
  SAN="DNS:$HOST,DNS:localhost,IP:127.0.0.1"
fi

mkdir -p "$OUT"
cd "$OUT"  # 상대 경로 사용 (Windows openssl 이 /c/... 경로를 못 여는 문제 회피)
openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout key.pem -out cert.pem \
  -subj "/CN=$HOST" \
  -addext "subjectAltName=$SAN" \
  -addext "extendedKeyUsage=serverAuth"

echo "생성 완료: $OUT (SAN: $SAN)"
