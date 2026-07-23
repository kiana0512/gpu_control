#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "用法: sudo $0 --role control|node"; }
role=""
while (($#)); do case "$1" in --role) role="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
[[ "${EUID}" -eq 0 && "${role}" =~ ^(control|node)$ ]] || { usage >&2; exit 2; }
apt-get update
apt-get install -y ca-certificates curl git git-lfs jq rsync ufw gnupg lsb-release openssl \
  python3 python3-venv ubuntu-drivers-common
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if id gpucontrol >/dev/null 2>&1; then
  [[ "$(id -u gpucontrol)" == "10001" ]] || {
    echo "错误：gpucontrol 用户已存在，但 UID 不是容器要求的 10001。请先调整该账号 UID。" >&2
    exit 1
  }
else
  useradd --system --uid 10001 --user-group --create-home --groups docker gpucontrol
fi

deploy_user="${SUDO_USER:-root}"
deploy_group="$(id -gn "${deploy_user}")"
usermod -aG docker "${deploy_user}"

# 应用运行目录归固定 UID 10001；仓库和模型目录归现场部署账号，确保其可以
# 生成 .env、更新版本和使用 rsync 同步模型。容器仅以只读方式挂载模型目录。
install -d -o gpucontrol -g gpucontrol /srv/gpu-control
install -d -o "${deploy_user}" -g "${deploy_group}" \
  /srv/comfyui/models /srv/comfyui/runtime /srv/comfyui/runtime/input \
  /srv/comfyui/runtime/output /srv/comfyui/runtime/temp /srv/comfyui/runtime/user \
  /opt/gpu-control /opt/imageclip /opt/modelviewcreator
git lfs install --system
echo "公共依赖安装完成；继续安装 NVIDIA 驱动与 Container Toolkit。"
