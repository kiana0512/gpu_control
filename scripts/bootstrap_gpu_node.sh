#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: sudo $0"; exit 0; }
"$(dirname "$0")/bootstrap_common_ubuntu.sh" --role node
install -d -o gpucontrol -g gpucontrol /srv/comfyui/runtime/{input,output,temp,user} /srv/gpu-control/images
deploy_user="${SUDO_USER:-root}"
chown -R 10001:10001 /srv/comfyui/runtime
chown -R "${deploy_user}:${deploy_user}" /srv/comfyui/models /srv/gpu-control/images
echo "GPU 节点目录已创建。下一步配置 .env、UFW、镜像和模型。"
