#!/usr/bin/env bash
set -Eeuo pipefail

usage(){ echo "用法: sudo $0 --lan-cidr 192.168.10.0/24 --worker-a 192.168.10.11 --worker-b 192.168.10.12 [--ssh-cidr 192.168.10.0/24]"; }
lan_cidr=""; worker_a=""; worker_b=""; ssh_cidr=""
while (($#)); do
  case "$1" in
    --lan-cidr) lan_cidr="$2"; shift 2 ;;
    --worker-a) worker_a="$2"; shift 2 ;;
    --worker-b) worker_b="$2"; shift 2 ;;
    --ssh-cidr) ssh_cidr="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "${EUID}" -eq 0 && -n "${lan_cidr}" && -n "${worker_a}" && -n "${worker_b}" ]] || { usage >&2; exit 2; }

ufw default deny incoming
ufw default allow outgoing
[[ -n "${ssh_cidr}" ]] && ufw allow from "${ssh_cidr}" to any port 22 proto tcp
for port in 80 443; do ufw allow from "${lan_cidr}" to any port "${port}" proto tcp; done
for worker in "${worker_a}" "${worker_b}"; do ufw allow from "${worker}" to any port 3100 proto tcp; done
# API containers call the host Node Agent through the host/LAN address.
ufw allow from 172.16.0.0/12 to any port 9201 proto tcp
ufw allow from 127.0.0.1 to any port 9201 proto tcp
ufw --force enable
ufw status numbered
