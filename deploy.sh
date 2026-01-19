#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env.deploy}"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

REPO_URL="${REPO_URL:-}"
APP_DIR="${APP_DIR:-$HOME/webdev}"
PROXY_USER="${PROXY_USER:-}"
PROXY_HOST="${PROXY_HOST:-}"
PROXY_PORT="${PROXY_PORT:-1080}"

if [ -z "$REPO_URL" ] || [ -z "$PROXY_USER" ] || [ -z "$PROXY_HOST" ]; then
  echo "REPO_URL, PROXY_USER, PROXY_HOST are required"
  exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

if ! command -v docker >/dev/null 2>&1; then
  $SUDO apt update
  $SUDO apt install -y docker.io docker-compose
  $SUDO systemctl enable --now docker
elif ! command -v docker-compose >/dev/null 2>&1; then
  $SUDO apt update
  $SUDO apt install -y docker-compose
fi

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull --rebase
fi

if ! ss -lntp | grep -q ":${PROXY_PORT}"; then
  ssh -fN -D "0.0.0.0:${PROXY_PORT}" -g "${PROXY_USER}@${PROXY_HOST}"
fi

HOST_GATEWAY_IP="$(ip -4 addr show docker0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 || true)"
if [ -z "$HOST_GATEWAY_IP" ]; then
  HOST_GATEWAY_IP="$(ip -4 route show default | awk '{print $3}' | head -n1 || true)"
fi
if [ -z "$HOST_GATEWAY_IP" ]; then
  echo "Cannot determine host gateway IP"
  exit 1
fi

cd "$APP_DIR"

if [ ! -f backend/GGM/credient.txt ]; then
  touch backend/GGM/credient.txt
fi

if grep -q "GEMINI_PROXY=" docker-compose.yml; then
  sed -i "s|GEMINI_PROXY=.*|GEMINI_PROXY=socks5h://${HOST_GATEWAY_IP}:${PROXY_PORT}|g" docker-compose.yml
fi

curl -I -x "socks5h://${HOST_GATEWAY_IP}:${PROXY_PORT}" https://www.google.com/generate_204 >/dev/null

$SUDO docker-compose build
$SUDO docker-compose up -d
$SUDO docker-compose ps
curl -s http://127.0.0.1:8000/health || true

echo "done"
