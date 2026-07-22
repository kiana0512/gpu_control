#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "用法: $0 [job JOB_ID] [--stdout] [--output FILE]"; }
job=""; stdout=false; output=""
while (($#)); do case "$1" in job) job="$2"; shift 2;; --stdout) stdout=true; shift;; --output) output="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
collect(){ echo "timestamp=$(date -u +%FT%TZ)"; echo "host=$(hostname)"; uname -a; df -h /srv 2>&1 || true; free -h 2>&1 || true; nvidia-smi 2>&1 || true; docker compose -f deploy/control-plane/compose.yaml ps 2>&1 || docker compose -f deploy/gpu-node/compose.yaml ps 2>&1 || true; [[ -n "${job}" ]] && echo "job_id=${job}"; }
[[ "${stdout}" == true ]] && { collect; exit 0; }
output="${output:-diagnostics/diagnostics-$(date -u +%Y%m%dT%H%M%SZ).txt}"
mkdir -p "$(dirname "${output}")"; collect | sed -E 's/(PASSWORD|SECRET|TOKEN|KEY)=.*/\1=[REDACTED]/Ig' > "${output}"; echo "诊断文件: ${output}"
