#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'TXT'
用法: scripts/deploy_node.sh --build-worker-only

本脚本只构建 Blender Worker，不运行全栈 compose up，也不会重建、重启或清理
ComfyUI。生产 Worker 激活必须由控制面确认对应节点 DRAINING、Worker 当前任务为 0，
并在记录 ComfyUI container ID/StartedAt/RestartCount 后仅更新 blender-worker 服务。
TXT
}

[[ "${1:-}" == -h || "${1:-}" == --help ]] && { usage; exit 0; }
[[ "${1:-}" == --build-worker-only && $# -eq 1 ]] || { usage >&2; exit 2; }
[[ -f .env ]] || { echo "缺少 .env" >&2; exit 1; }

compose=(docker compose --env-file .env -f deploy/gpu-node/compose.yaml --profile asset-plane)
"${compose[@]}" config --quiet
"${compose[@]}" build blender-worker

cat <<'TXT'
BUILD_ONLY_COMPLETE：未启动、停止、重建或重启 blender-worker、ComfyUI 或监控容器。
TXT
