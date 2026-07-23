#!/usr/bin/env bash
set -Eeuo pipefail

usage() { echo "用法: $0 [--tag IMAGE:TAG] [--no-cache]"; }
tag="${COMFY_IMAGE:-registry.local:5000/gpu-control/comfyui:projects-0.2.2}"
cache=()
while (($#)); do case "$1" in --tag) tag="$2"; shift 2;; --no-cache) cache=(--no-cache); shift;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a; source "${root}/configs/versions.lock.env"; set +a
lock_sha="$(
  cd "${root}"
  find docker/comfyui -maxdepth 1 -type f \
    \( -name '*.lock.txt' -o -name 'custom_nodes.lock.yaml' \) -print0 \
    | sort -z \
    | xargs -0 sha256sum \
    | sha256sum \
    | awk '{print $1}'
)"
build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
vcs_ref="$(git -C "${root}" rev-parse HEAD 2>/dev/null || echo unversioned)"
echo "主机: $(hostname)"; echo "镜像: ${tag}"; echo "ComfyUI: ${COMFYUI_COMMIT}"
docker build "${cache[@]}" --file "${root}/docker/comfyui/Dockerfile" --tag "${tag}" --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" --build-arg "COMFYUI_REPOSITORY=${COMFYUI_REPOSITORY}" --build-arg "COMFYUI_COMMIT=${COMFYUI_COMMIT}" --build-arg "BUILD_DATE=${build_date}" --build-arg "VCS_REF=${vcs_ref}" --build-arg "LOCK_SHA256=${lock_sha}" "${root}"
mkdir -p "${root}/build"
docker image inspect "${tag}" --format '{{json .}}' > "${root}/build/comfyui-image-metadata.json"
docker image inspect "${tag}" --format '{{.Id}}' | sha256sum > "${root}/build/comfyui-image.sha256"
echo "构建完成: ${tag}"
