#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${DOMAIN:-}"
DOMAINS="${DOMAINS:-}"
EMAIL="${EMAIL:-}"
CF_API_TOKEN="${CF_API_TOKEN:-}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-/etc/letsencrypt/cloudflare.ini}"

if [ -z "$DOMAIN" ] && [ -z "$DOMAINS" ]; then
  echo "DOMAIN or DOMAINS is required"
  exit 1
fi

if [ -z "$EMAIL" ]; then
  echo "EMAIL is required"
  exit 1
fi

if [ -z "$CF_API_TOKEN" ]; then
  echo "CF_API_TOKEN is required"
  exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

if ! command -v certbot >/dev/null 2>&1; then
  $SUDO apt update
  $SUDO apt install -y certbot
fi

if ! dpkg -s python3-certbot-dns-cloudflare >/dev/null 2>&1; then
  $SUDO apt update
  $SUDO apt install -y python3-certbot-dns-cloudflare
fi

TMP_FILE="$(mktemp)"
printf "dns_cloudflare_api_token = %s\n" "$CF_API_TOKEN" > "$TMP_FILE"
$SUDO install -m 600 -o root -g root "$TMP_FILE" "$CREDENTIALS_FILE"
rm -f "$TMP_FILE"

if [ -z "$DOMAINS" ]; then
  DOMAINS="$DOMAIN"
fi

DOMAIN_ARGS=()
IFS=',' read -r -a DOMAIN_LIST <<< "$DOMAINS"
for d in "${DOMAIN_LIST[@]}"; do
  d="$(echo "$d" | xargs)"
  if [ -n "$d" ]; then
    DOMAIN_ARGS+=("-d" "$d")
  fi
done

if [ "${#DOMAIN_ARGS[@]}" -eq 0 ]; then
  echo "No valid domains provided"
  exit 1
fi

$SUDO certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials "$CREDENTIALS_FILE" \
  --agree-tos \
  --non-interactive \
  --email "$EMAIL" \
  "${DOMAIN_ARGS[@]}"

echo "Certificate issued. Update nginx.conf to use /etc/letsencrypt/live/<domain>/ and restart frontend."
