#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_COMMIT="c58249a29c2cc1b1e0cdeef5d26f27265ca28220"
EXPECTED_WORKFLOW_SHA="eec3a66ded9290b8d7f5c2eb1cbfdeaeec7acd5d5260c08266a8430750d0eaaf"
EXPECTED_DIT_SHA="7aed800ac4eb8e0d18569a954c0ff35f5a1caa3ed5d920e66cc31405f75b6e69"
EXPECTED_VAE_SHA="20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1"

repo="/opt/modelviewcreator"
model_root="/opt/imageclip/models"
comfy_url="http://127.0.0.1:8188"

usage() {
  echo "用法: $0 [--repo PATH] [--model-root PATH] [--comfy-url URL]"
}

while (($#)); do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --model-root) model_root="$2"; shift 2 ;;
    --comfy-url) comfy_url="${2%/}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

workflow="$repo/flux_fill_inpaint.json"
seedvr2_source="$repo/model/SEEDVR2"
seedvr2_link="$model_root/SEEDVR2"
dit_model="$model_root/SEEDVR2/seedvr2_ema_7b_sharp-Q4_K_M.gguf"
vae_model="$model_root/SEEDVR2/ema_vae_fp16.safetensors"

failures=0
check_equal() {
  local label="$1" actual="$2" expected="$3"
  if [[ "$actual" == "$expected" ]]; then
    printf 'PASS %-24s %s\n' "$label" "$actual"
  else
    printf 'FAIL %-24s actual=%s expected=%s\n' "$label" "$actual" "$expected" >&2
    failures=$((failures + 1))
  fi
}

if [[ ! -d "$repo/.git" || ! -f "$workflow" ]]; then
  echo "FAIL ModelViewCreator 仓库或工作流不存在: $repo" >&2
  exit 1
fi

check_equal "git_commit" "$(git -C "$repo" rev-parse HEAD)" "$EXPECTED_COMMIT"
check_equal "workflow_sha256" "$(sha256sum "$workflow" | awk '{print $1}')" "$EXPECTED_WORKFLOW_SHA"

if [[ -L "$seedvr2_link" ]]; then
  check_equal "seedvr2_symlink" "$(readlink -f "$seedvr2_link")" "$seedvr2_source"
else
  echo "FAIL SeedVR2 必须通过软链接指向业务模型目录: $seedvr2_link" >&2
  failures=$((failures + 1))
fi

if [[ -f "$dit_model" ]]; then
  check_equal "seedvr2_dit_sha256" "$(sha256sum "$dit_model" | awk '{print $1}')" "$EXPECTED_DIT_SHA"
else
  echo "FAIL SeedVR2 DiT 模型不存在: $dit_model" >&2
  failures=$((failures + 1))
fi

if [[ -f "$vae_model" ]]; then
  check_equal "seedvr2_vae_sha256" "$(sha256sum "$vae_model" | awk '{print $1}')" "$EXPECTED_VAE_SHA"
else
  echo "FAIL SeedVR2 VAE 模型不存在: $vae_model" >&2
  failures=$((failures + 1))
fi

object_info="$(mktemp /tmp/modelview-object-info.XXXXXX.json)"
cleanup() {
  case "$object_info" in
    /tmp/modelview-object-info.*.json) rm -f -- "$object_info" ;;
  esac
}
trap cleanup EXIT

if curl --fail --silent --show-error --max-time 30 "$comfy_url/object_info" >"$object_info"; then
  if ! python3 - "$object_info" <<'PY'
import json
import sys

required = {
    "Qwen3 VL Plus",
    "SeedVR2LoadDiTModel",
    "SeedVR2LoadVAEModel",
    "SeedVR2VideoUpscaler",
}
with open(sys.argv[1], encoding="utf-8") as source:
    available = set(json.load(source))
missing = sorted(required - available)
if missing:
    print("FAIL object_info missing=" + ",".join(missing), file=sys.stderr)
    raise SystemExit(1)
print("PASS object_info nodes=" + ",".join(sorted(required)))
PY
  then
    failures=$((failures + 1))
  fi
else
  echo "FAIL ComfyUI object_info 不可访问: $comfy_url/object_info" >&2
  failures=$((failures + 1))
fi

if ((failures)); then
  echo "modelview_seedvr2_preflight=FAILED failures=$failures" >&2
  exit 1
fi

echo "modelview_seedvr2_preflight=PASSED"
