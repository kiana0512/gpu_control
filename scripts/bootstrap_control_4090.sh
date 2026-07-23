#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: sudo $0"; exit 0; }
"$(dirname "$0")/bootstrap_common_ubuntu.sh" --role control
install -d -o gpucontrol -g gpucontrol /srv/gpu-control/{jobs,backups,images}
install -d -m 0700 -o gpucontrol -g gpucontrol /srv/gpu-control/secrets
install -d -o gpucontrol -g gpucontrol /srv/comfyui/4090/{input,output,temp,user}
install -d -o gpucontrol -g gpucontrol /srv/comfyui/4090/user/default/workflows
deploy_user="${SUDO_USER:-root}"
chown -R 10001:10001 /srv/gpu-control/jobs /srv/comfyui/4090
chown -R "${deploy_user}:${deploy_user}" /srv/comfyui/models /srv/gpu-control/backups /srv/gpu-control/images
echo "控制中心目录已创建。复制 .env.example 为 .env 并更换全部 CHANGE_ME。"
