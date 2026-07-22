#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: $0"; exit 0; }
[[ -f .env ]] || { echo "缺少 .env" >&2; exit 1; }
scripts/verify_models.sh
docker compose -f deploy/gpu-node/compose.yaml config --quiet
docker compose -f deploy/gpu-node/compose.yaml up -d
docker compose -f deploy/gpu-node/compose.yaml ps
