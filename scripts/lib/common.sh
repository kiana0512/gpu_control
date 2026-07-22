#!/usr/bin/env bash
set -Eeuo pipefail
root_dir() { cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd; }
die() { echo "错误: $*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "缺少命令 $1"; }
show_context() { echo "主机: $(hostname)"; echo "角色: ${GPU_CONTROL_ROLE:-未设置}"; echo "配置: ${GPU_CONTROL_ENV_FILE:-$(root_dir)/.env}"; }
confirm() { local prompt="$1"; [[ "${GPUCTL_YES:-false}" == true ]] && return 0; read -r -p "${prompt} [输入 yes]: " answer; [[ "${answer}" == yes ]]; }
