#!/usr/bin/env bash
set -Eeuo pipefail

usage(){ echo "用法: sudo $0 [--branch 580-server]；不指定时使用 Ubuntu 自动推荐的计算驱动"; }
branch=""
while (($#)); do
  case "$1" in
    --branch) branch="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "${EUID}" -eq 0 ]] || { usage >&2; exit 2; }
apt-get update
apt-get install -y ubuntu-drivers-common
echo "本机可用的计算驱动："
ubuntu-drivers list --gpgpu
if [[ -n "${branch}" ]]; then
  ubuntu-drivers install --gpgpu "nvidia:${branch}"
else
  ubuntu-drivers install --gpgpu
fi
touch /var/run/reboot-required
echo "驱动安装完成。现在必须执行 sudo reboot；重启后先运行 nvidia-smi。"
