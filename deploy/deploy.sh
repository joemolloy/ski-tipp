#!/usr/bin/env bash
# Runs on the server from the repo checkout. Usage: deploy/deploy.sh <systemd-service-name>
set -euo pipefail

SERVICE="${1:?usage: deploy.sh <systemd-service-name>}"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

cd "$(dirname "$0")/.."

git pull --ff-only
"$UV" sync --no-dev --frozen
"$UV" run --no-sync manage.py collectstatic --noinput

sudo systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || { sudo journalctl -u "$SERVICE" -n 30 --no-pager; exit 1; }
echo "deployed $(git rev-parse --short HEAD)"
