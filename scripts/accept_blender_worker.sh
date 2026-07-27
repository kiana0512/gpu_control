#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-li3d/blender-worker:1.0.0}"
ACCEPTANCE_DIR="$(mktemp -d /tmp/gpu-control-blender-acceptance.XXXXXX)"
cleanup() {
  case "$ACCEPTANCE_DIR" in
    /tmp/gpu-control-blender-acceptance.*)
      docker run --rm --network none --user 0 \
        --volume "$ACCEPTANCE_DIR:/acceptance" \
        --entrypoint chmod "$IMAGE" -R a+rwX /acceptance >/dev/null 2>&1 || true
      rm -rf -- "$ACCEPTANCE_DIR"
      ;;
  esac
}
trap cleanup EXIT
chmod 0777 "$ACCEPTANCE_DIR"

docker run --rm --network none \
  --volume "$ACCEPTANCE_DIR:/acceptance" \
  --entrypoint sh \
  "$IMAGE" \
  -lc '
    /opt/blender/blender --background --factory-startup \
      --python-expr "import bpy; bpy.ops.mesh.primitive_cube_add(); bpy.ops.wm.save_as_mainfile(filepath=\"/acceptance/cube.blend\")"
    /opt/blender/blender --background --factory-startup \
      --python /usr/local/lib/python3.11/site-packages/packages/asset_processing/blender_uv.py \
      -- \
      --input /acceptance/cube.blend \
      --output-dir /acceptance/result \
      --options-json "{\"resolution\":2048,\"padding_px\":10,\"hard_edge_angle_degrees\":75.0,\"hidden_axis\":\"auto\",\"texel_density_mode\":\"uniform\",\"qa_profile\":\"pbr-v1\"}"
  '

for artifact in \
  model_PBR_UV.blend \
  model_PBR_UV.fbx \
  model_report.json \
  model_QA.json
do
  test -s "$ACCEPTANCE_DIR/result/$artifact"
done

python3 - "$ACCEPTANCE_DIR/result/model_QA.json" <<'PY'
import json
import sys

qa = json.load(open(sys.argv[1], encoding="utf-8"))
if qa.get("passed") is not True or qa.get("hard_failures") != []:
    raise SystemExit(f"QA failed: {qa}")
print("qa_passed=true hard_failures=0")
PY

sha256sum "$ACCEPTANCE_DIR"/result/*
echo "blender_worker_acceptance=PASSED image=$IMAGE"
