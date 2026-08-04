#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'TXT'
用法: scripts/deploy_control.sh --build-only

本脚本只构建控制面四镜像和 control-4090 Blender Worker 镜像，不激活服务。
生产激活必须按滚动发布手册执行：先冻结新提交并将节点置为 DRAINING，确认
GPU/批次/Asset/Worker/Windows Baker 全部为 0，再按四个 Windows v6 Agent ->
Asset API 1.5.9 -> 三台 Linux Worker 1.2.5 -> API/Web/Scheduler 顺序逐项更新。
禁止用全栈 compose up 触碰 ComfyUI。
TXT
}

[[ "${1:-}" == -h || "${1:-}" == --help ]] && { usage; exit 0; }
[[ "${1:-}" == --build-only && $# -eq 1 ]] || { usage >&2; exit 2; }
[[ -f .env ]] || { echo "缺少 .env" >&2; exit 1; }

compose=(docker compose --env-file .env -f deploy/control-plane/compose.yaml --profile asset-plane)
"${compose[@]}" config --quiet
"${compose[@]}" build api scheduler asset-api web asset-worker-control

cat <<'TXT'
BUILD_ONLY_COMPLETE：未启动、停止、重建或重启任何生产容器。
请继续使用 docs/83_2026-08-03_CONTROL_PLANE_1_5_9_RELEASE_AND_SIX_API_ACCEPTANCE.md
中的排空、逐服务滚动和回滚流程。
TXT
