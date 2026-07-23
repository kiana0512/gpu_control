#!/usr/bin/env bash
set -Eeuo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
imageclip_root="${IMAGECLIP_ROOT:-/opt/imageclip}"
modelview_root="${MODELVIEW_ROOT:-/opt/modelviewcreator}"

echo "校验 ImageClip 模型"
"${root}/scripts/verify_models.sh" \
  --manifest "${imageclip_root}/models/models.manifest.yaml" \
  --root "${imageclip_root}/models"

echo "校验 ModelViewCreator 模型"
"${root}/scripts/verify_models.sh" \
  --manifest "${root}/configs/modelviewcreator.models.manifest.yaml" \
  --root "${modelview_root}/model"
