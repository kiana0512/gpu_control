#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "用法: $0 [--role control|node]"; }
role="${GPU_CONTROL_ROLE:-control}"
while (($#)); do case "$1" in --role) role="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
failed=0
check(){ if "$@" >/dev/null 2>&1; then echo "OK   $*"; else echo "FAIL $*"; failed=1; fi; }
echo "预检主机 $(hostname)，角色 ${role}"
check test "$(uname -s)" = Linux
check docker version
check docker compose version
check curl --version
check jq --version
check rsync --version
check nvidia-smi
check docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
check test -d /srv
[[ -f .env ]] || { echo "FAIL 缺少 .env"; failed=1; }
((failed==0)) || { echo "预检失败；按 docs/04_PREPARATION_CHECKLIST.md 修复。" >&2; exit 1; }
echo "预检通过。"
