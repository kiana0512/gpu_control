#!/usr/bin/env bash
set -Eeuo pipefail

usage() { echo "用法: $0 --comfy-url URL"; }

comfy_url=""
while (($#)); do
  case "$1" in
    --comfy-url) comfy_url="${2%/}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n "${comfy_url}" ]] || { usage >&2; exit 2; }

temporary="$(mktemp -d /tmp/modelview-truev3-preflight.XXXXXX)"
cleanup() { rm -rf -- "${temporary}"; }
trap cleanup EXIT

curl --fail --show-error --silent --max-time 30 \
  "${comfy_url}/object_info" > "${temporary}/object_info.json"
for class_type in \
  CherryAlignReference \
  CherryInferenceSizeBucket \
  'ImageResize+' \
  'easy imageColorMatch'; do
  jq -e --arg class_type "${class_type}" 'has($class_type)' \
    "${temporary}/object_info.json" >/dev/null || {
      echo "错误：${comfy_url} 缺少节点 ${class_type}" >&2
      exit 1
    }
done

check_model() {
  local category="$1" filename="$2"
  curl --fail --show-error --silent --max-time 30 \
    "${comfy_url}/api/models/${category}" \
    | jq -e --arg filename "${filename}" 'index($filename) != null' >/dev/null || {
      echo "错误：${comfy_url} 缺少模型 ${category}/${filename}" >&2
      exit 1
    }
}
check_model diffusion_models 'Flux2-Klein-9B-True-V3-int8mixedrow.safetensors'
check_model text_encoders 'qwen_3_8b_fp8mixed.safetensors'
check_model vae 'flux2-vae.safetensors'
check_model loras 'baimo_shangcaizhi_klein_v1_000005500.safetensors'

names=(
  'c67b0fab153890a6225a371dc7a8a911bc2f4c3933b9399fc4470b19f047654e.jpg'
  'img_v3_0214l_5c6a7e7e-e76c-4a82-86c1-b8f7cfe87b4g.png'
)
hashes=(
  'c67b0fab153890a6225a371dc7a8a911bc2f4c3933b9399fc4470b19f047654e'
  '87e5b68de4461655711216274f67116dd496032a65776776437a0dd503ff1bc3'
)
for index in "${!names[@]}"; do
  name="${names[$index]}"
  encoded="$(python3 - "${name}" <<'PY'
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"
  curl --fail --show-error --silent --max-time 30 \
    --output "${temporary}/${name}" \
    "${comfy_url}/view?filename=${encoded}&type=input"
  actual="$(sha256sum "${temporary}/${name}" | awk '{print $1}')"
  [[ "${actual}" == "${hashes[$index]}" ]] || {
    echo "错误：${comfy_url} 静态输入 ${name} 哈希不一致" >&2
    exit 1
  }
done

echo "${comfy_url} Flux2 Klein TrueV3 节点、模型与固定输入预检通过。"
