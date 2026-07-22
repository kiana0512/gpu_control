#!/usr/bin/env bash
set -Eeuo pipefail

exec python3.11 main.py \
  --listen 0.0.0.0 \
  --port "${COMFY_PORT:-8188}" \
  --disable-auto-launch \
  --disable-api-nodes \
  "$@"
