#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: $0 [--image IMAGE:TAG]"; }
image="${COMFY_IMAGE:-registry.local:5000/gpu-control/comfyui:projects-0.2.2}"
while (($#)); do case "$1" in --image) image="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
docker image inspect "${image}" >/dev/null
docker push "${image}"
