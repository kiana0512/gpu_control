#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: $0 [--image IMAGE:TAG] [--output FILE.tar.gz]"; }
image="${COMFY_IMAGE:-registry.local:5000/gpu-control/comfyui:projects-0.2.5}"; output="comfyui-image.tar.gz"
while (($#)); do case "$1" in --image) image="$2"; shift 2;; --output) output="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
docker image inspect "${image}" >/dev/null
docker save "${image}" | gzip -1 > "${output}"
sha256sum "${output}" > "${output}.sha256"
echo "已导出 ${output}；传输 tar.gz 和 .sha256 两个文件。"
