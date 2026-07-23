#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: sudo $0 --control-ip 192.168.10.10 [--ssh-cidr 192.168.10.0/24]"; }
control=""; ssh_cidr=""
while (($#)); do case "$1" in --control-ip) control="$2"; shift 2;; --ssh-cidr) ssh_cidr="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
[[ "${EUID}" -eq 0 && -n "${control}" ]] || { usage >&2; exit 2; }
ufw default deny incoming
ufw default allow outgoing
[[ -n "${ssh_cidr}" ]] && ufw allow from "${ssh_cidr}" to any port 22 proto tcp
for port in 8188 9100 9201 9400; do ufw allow from "${control}" to any port "${port}" proto tcp; done
ufw --force enable
ufw status numbered
