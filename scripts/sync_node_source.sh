#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "用法: $0 --host HOST [--user USER] [--port PORT] [--known-hosts-file PATH] [--host-key-alias ALIAS] [--dry-run]"
}

host=""
user="${USER}"
dry=()
ssh_options=()
port="22"
while (($#)); do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --user) user="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --known-hosts-file) ssh_options+=(-o "UserKnownHostsFile=$2" -o StrictHostKeyChecking=yes); shift 2 ;;
    --host-key-alias) ssh_options+=(-o "HostKeyAlias=$2"); shift 2 ;;
    --dry-run) dry=(--dry-run); shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n "${host}" ]] || { usage >&2; exit 2; }
[[ "${port}" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || { usage >&2; exit 2; }
ssh_options=(-p "${port}" "${ssh_options[@]}")

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${user}@${host}:/opt/gpu-control/"

echo "同步 GPU Control 当前工作树到 ${target}"
echo "不会传输 .env、密钥、证书、构建缓存、数据库、任务或模型。"
ssh "${ssh_options[@]}" "${user}@${host}" "mkdir -p /opt/gpu-control"
rsync -a --human-readable --progress "${dry[@]}" \
  -e "ssh ${ssh_options[*]}" \
  --exclude='/.git/' \
  --exclude='/.env' \
  --exclude='/.agents/' \
  --exclude='/.codex/' \
  --exclude='/deploy/control-plane/nginx/certs/' \
  --exclude='/.idea/' \
  --exclude='/.vscode/' \
  --exclude='/gpu_control/' \
  --exclude='/output/' \
  --exclude='/runtime/' \
  --exclude='/build/' \
  --exclude='/artifacts/' \
  --exclude='/.venv/' \
  --exclude='**/node_modules/' \
  --exclude='**/dist/' \
  --exclude='**/*.tsbuildinfo' \
  --exclude='**/__pycache__/' \
  --exclude='**/.pytest_cache/' \
  --exclude='**/.ruff_cache/' \
  --exclude='**/.mypy_cache/' \
  --exclude='**/*.egg-info/' \
  --exclude='**/*.pyc' \
  --exclude='/.coverage' \
  "${root}/" "${target}"

if ((${#dry[@]})); then
  echo "dry-run 完成：没有写入远端。"
  exit 0
fi

ssh "${ssh_options[@]}" "${user}@${host}" \
  'chmod +x /opt/gpu-control/scripts/*.sh /opt/gpu-control/scripts/gpuctl /opt/gpu-control/scripts/gpu-node-ctl /opt/gpu-control/docker/comfyui/entrypoint.sh'

files=(
  deploy/gpu-node/compose.yaml
  deploy/gpu-node/compose.wsl.yaml
  scripts/deploy_node.sh
  scripts/verify_comfy_projects.sh
  docker/comfyui/custom_nodes.lock.yaml
)
local_digest="$(cd "${root}" && sha256sum "${files[@]}" | sha256sum | awk '{print $1}')"
remote_digest="$(ssh "${ssh_options[@]}" "${user}@${host}" "cd /opt/gpu-control && sha256sum ${files[*]} | sha256sum | awk '{print \$1}'")"
[[ "${local_digest}" == "${remote_digest}" ]] || {
  echo "错误：远端源码关键文件指纹不一致。" >&2
  echo "本机: ${local_digest}" >&2
  echo "远端: ${remote_digest}" >&2
  exit 1
}
echo "源码同步完成，关键文件指纹：${local_digest}"
