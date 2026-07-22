#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "用法: sudo $0"; }
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { usage; exit 0; }
[[ "${EUID}" -eq 0 ]] || { usage >&2; exit 2; }
command -v nvidia-smi >/dev/null || { echo "先按 NVIDIA 官方方式安装兼容驱动并重启。" >&2; exit 1; }
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
toolkit_version="${NVIDIA_CONTAINER_TOOLKIT_VERSION:-1.19.1-1}"
apt-get install -y \
  "nvidia-container-toolkit=${toolkit_version}" \
  "nvidia-container-toolkit-base=${toolkit_version}" \
  "libnvidia-container-tools=${toolkit_version}" \
  "libnvidia-container1=${toolkit_version}"
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
