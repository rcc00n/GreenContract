#!/usr/bin/env bash
set -euo pipefail

# Simple rsync-based deploy to the server used in this repo.
# Usage:
#   ./scripts/deploy.sh
#   ./scripts/deploy.sh --build
#
# Env overrides:
#   DEPLOY_HOST="root@89.111.171.91"
#   DEPLOY_SSH_KEY="$HOME/.ssh/greencrm_agent"
#   DEPLOY_REMOTE_DIR="/opt/car_rental_tool"

BUILD=0
if [[ "${1:-}" == "--build" ]]; then
  BUILD=1
elif [[ -n "${1:-}" ]]; then
  echo "Unknown argument: $1" >&2
  exit 2
fi

DEPLOY_HOST="${DEPLOY_HOST:-root@89.111.171.91}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:-$HOME/.ssh/greencrm_agent}"
DEPLOY_REMOTE_DIR="${DEPLOY_REMOTE_DIR:-/opt/car_rental_tool}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SSH_OPTS=(
  -i "${DEPLOY_SSH_KEY}"
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
)

RSYNC_EXCLUDES=(
  --exclude ".git"
  --exclude ".env"
  --exclude "__pycache__"
  --exclude "*.pyc"
  # Data exports/imports must never be synced to prod via code deploys.
  --exclude "*.xls"
  --exclude "*.xlsx"
  --exclude "*:Zone.Identifier"
  # Generated/static/data dirs should never be overwritten (or deleted) by deploys.
  --exclude "staticfiles"
  --exclude "media"
  --exclude "uploads"
  --exclude "ocr_uploads"
)

echo "Syncing ${LOCAL_DIR} -> ${DEPLOY_HOST}:${DEPLOY_REMOTE_DIR}"
rsync -az --delete "${RSYNC_EXCLUDES[@]}" -e "ssh ${SSH_OPTS[*]}" "${LOCAL_DIR}/" "${DEPLOY_HOST}:${DEPLOY_REMOTE_DIR}/"

if [[ "${BUILD}" == "1" ]]; then
  echo "Rebuilding and restarting via docker compose..."
  ssh "${SSH_OPTS[@]}" "${DEPLOY_HOST}" "cd '${DEPLOY_REMOTE_DIR}' && docker compose up -d --build && docker compose restart web"
else
  echo "Restarting web container..."
  # Run `up -d` first so compose file changes (command, ports, new services, etc.) apply.
  ssh "${SSH_OPTS[@]}" "${DEPLOY_HOST}" "cd '${DEPLOY_REMOTE_DIR}' && docker compose up -d && docker compose restart web"
fi

echo "Done."
