#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: $0 --host HOST [--user USER] [--dry-run] [--manifest-only] [--delete]"; }
host=""; user="${USER}"; dry=(); manifest_only=false; delete=false
while (($#)); do case "$1" in --host) host="$2"; shift 2;; --user) user="$2"; shift 2;; --dry-run) dry=(--dry-run); shift;; --manifest-only) manifest_only=true; shift;; --delete) delete=true; shift;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
[[ -n "${host}" ]] || { usage >&2; exit 2; }
source_root="${MODEL_ROOT:-/srv/comfyui/models}/"
options=(-a --partial --append-verify --human-readable --progress)
[[ "${manifest_only}" == true ]] && options+=(--include='models.manifest.yaml' --exclude='*')
if [[ "${delete}" == true ]]; then read -r -p "确认删除远端清单外模型？输入 DELETE: " answer; [[ "${answer}" == DELETE ]] || exit 1; options+=(--delete); fi
rsync "${options[@]}" "${dry[@]}" "${source_root}" "${user}@${host}:/srv/comfyui/models/"
ssh "${user}@${host}" 'cd /opt/gpu-control && scripts/verify_models.sh'
