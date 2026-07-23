#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: $0 [--older-than-days N] [--apply]"; }
days=7; apply=false
while (($#)); do case "$1" in --older-than-days) days="$2"; shift 2;; --apply) apply=true; shift;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
[[ "${days}" =~ ^[0-9]+$ ]] || { echo "天数必须是整数" >&2; exit 2; }
root="/srv/comfyui/runtime"
mapfile -d '' files < <(find "${root}/input" "${root}/temp" -type f -mtime "+${days}" -print0 2>/dev/null)
printf '候选文件: %s\n' "${#files[@]}"
if [[ "${apply}" == true ]]; then
  for file in "${files[@]}"; do rm -- "${file}"; done
  echo "只清理 input/temp；output 必须由中央确认下载后另行清理。"
else
  printf '%s\n' "${files[@]}"
  echo "dry-run；添加 --apply 才删除。"
fi
