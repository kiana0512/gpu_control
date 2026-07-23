#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: $0"; exit 0; }
[[ -f .env ]] || { echo "缺少 .env" >&2; exit 1; }
compose=(docker compose --env-file .env -f deploy/control-plane/compose.yaml)
alert_token="$(sed -n 's/^ALERTMANAGER_WEBHOOK_TOKEN=//p' .env | tail -n 1)"
[[ -n "${alert_token}" && "${alert_token}" != CHANGE_ME* ]] || {
  echo "ALERTMANAGER_WEBHOOK_TOKEN 未配置" >&2
  exit 1
}
install -d -m 0700 /srv/gpu-control/secrets
umask 077
printf '%s' "${alert_token}" > /srv/gpu-control/secrets/alertmanager_webhook_token
"${compose[@]}" config --quiet
"${compose[@]}" build api scheduler web
"${compose[@]}" up -d postgres redis
"${compose[@]}" run --rm api alembic upgrade head
"${compose[@]}" run --rm api \
  python scripts/bootstrap_nodes.py --config /app/configs/nodes.yaml
if [[ -f output/deploy/INITIAL_ADMIN_PASSWORD.txt ]]; then
  "${compose[@]}" run --rm -T api \
    python scripts/bootstrap_admin.py --username admin --password-stdin --ensure \
    < output/deploy/INITIAL_ADMIN_PASSWORD.txt
fi
"${compose[@]}" up -d
"${compose[@]}" ps
