#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "用法: $0 --input-root DIR [--source-url URL]"
}

input_root=""
source_url="http://10.3.2.59:49255"
while (($#)); do
  case "$1" in
    --input-root) input_root="$2"; shift 2 ;;
    --source-url) source_url="${2%/}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

[[ -n "${input_root}" ]] || { usage >&2; exit 2; }
[[ -d "${input_root}" ]] || {
  echo "错误：ComfyUI input 目录不存在：${input_root}" >&2
  exit 1
}

names=(
  'c67b0fab153890a6225a371dc7a8a911bc2f4c3933b9399fc4470b19f047654e.jpg'
  'img_v3_0214l_5c6a7e7e-e76c-4a82-86c1-b8f7cfe87b4g.png'
)
sizes=(759586 1197050)
hashes=(
  'c67b0fab153890a6225a371dc7a8a911bc2f4c3933b9399fc4470b19f047654e'
  '87e5b68de4461655711216274f67116dd496032a65776776437a0dd503ff1bc3'
)

temporary="$(mktemp -d "${input_root%/}/.truev3-inputs.XXXXXX")"
cleanup() { rm -rf -- "${temporary}"; }
trap cleanup EXIT

for index in "${!names[@]}"; do
  name="${names[$index]}"
  size="${sizes[$index]}"
  digest="${hashes[$index]}"
  destination="${input_root%/}/${name}"

  if [[ -e "${destination}" ]]; then
    [[ -f "${destination}" && ! -L "${destination}" ]] || {
      echo "错误：已有目标不是普通文件：${destination}" >&2
      exit 1
    }
    actual_size="$(stat -c %s "${destination}")"
    actual_digest="$(sha256sum "${destination}" | awk '{print $1}')"
    [[ "${actual_size}" == "${size}" && "${actual_digest}" == "${digest}" ]] || {
      echo "错误：已有静态输入与批准版本不一致，拒绝覆盖：${destination}" >&2
      exit 1
    }
    echo "已存在且校验通过：${name}"
    continue
  fi

  encoded="$(python3 - "${name}" <<'PY'
import sys
import urllib.parse

print(urllib.parse.quote(sys.argv[1], safe=""))
PY
)"
  staged="${temporary}/${name}"
  curl --fail --show-error --silent --location --retry 3 \
    --output "${staged}" \
    "${source_url}/view?filename=${encoded}&type=input"
  actual_size="$(stat -c %s "${staged}")"
  actual_digest="$(sha256sum "${staged}" | awk '{print $1}')"
  [[ "${actual_size}" == "${size}" && "${actual_digest}" == "${digest}" ]] || {
    echo "错误：下载的 ${name} 校验失败" >&2
    echo "期望：${size} bytes ${digest}" >&2
    echo "实际：${actual_size} bytes ${actual_digest}" >&2
    exit 1
  }
  chmod 0644 "${staged}"
  mv -- "${staged}" "${destination}"
  echo "已原子安装并校验：${destination}"
done

echo "Flux2 Klein TrueV3 固定参考输入安装完成。"
