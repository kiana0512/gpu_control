#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: $0"; exit 0; }
[[ -f .env ]] || { echo "缺少 .env" >&2; exit 1; }
alert_token="$(sed -n 's/^ALERTMANAGER_WEBHOOK_TOKEN=//p' .env | tail -n 1)"
[[ -n "${alert_token}" && "${alert_token}" != CHANGE_ME* ]] || {
  echo "ALERTMANAGER_WEBHOOK_TOKEN 未配置" >&2
  exit 1
}
install -d -m 0700 /srv/gpu-control/secrets
umask 077
printf '%s' "${alert_token}" > /srv/gpu-control/secrets/alertmanager_webhook_token
docker compose -f deploy/control-plane/compose.yaml config --quiet
docker compose -f deploy/control-plane/compose.yaml build api scheduler web
docker compose -f deploy/control-plane/compose.yaml up -d postgres redis
docker compose -f deploy/control-plane/compose.yaml run --rm api alembic upgrade head
docker compose -f deploy/control-plane/compose.yaml run --rm api \
  python scripts/bootstrap_nodes.py --config /app/configs/nodes.yaml
if [[ -f output/deploy/INITIAL_ADMIN_PASSWORD.txt ]]; then
  docker compose -f deploy/control-plane/compose.yaml run --rm -T api \
    python scripts/bootstrap_admin.py --username admin --password-stdin --ensure \
    < output/deploy/INITIAL_ADMIN_PASSWORD.txt
fi
docker compose -f deploy/control-plane/compose.yaml up -d
docker compose -f deploy/control-plane/compose.yaml ps
