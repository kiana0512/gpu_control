#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: $0"; exit 0; }
[[ -f .env ]] || { echo "缺少 .env" >&2; exit 1; }
set -a
source .env
set +a
model_root="${MODEL_ROOT:-/opt/imageclip/models}"
for directory in checkpoints clip embeddings ipadapter pulid insightface facexlib; do
  sudo install -d -m 0755 "${model_root}/${directory}"
done
sudo install -d -m 0755 -o 10001 -g 10001 /srv/comfyui/runtime/user/default/workflows
scripts/verify_comfy_projects.sh
docker compose -f deploy/gpu-node/compose.yaml config --quiet
docker compose -f deploy/gpu-node/compose.yaml up -d
docker compose -f deploy/gpu-node/compose.yaml ps
