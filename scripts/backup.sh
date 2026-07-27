#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "用法: $0 [--output DIR] [--dry-run]"; }
output="/srv/gpu-control/backups"; dry=false
while (($#)); do case "$1" in --output) output="$2"; shift 2;; --dry-run) dry=true; shift;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
stamp="$(date -u +%Y%m%dT%H%M%SZ)"; destination="${output}/${stamp}"
if [[ "${dry}" == true ]]; then echo "将备份 PostgreSQL、configs、workflows、锁文件到 ${destination}"; exit 0; fi
mkdir -p "${destination}"
postgres_container="${POSTGRES_CONTAINER:-gpu-control-postgres-1}"
docker exec "${postgres_container}" sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "${destination}/database.dump"
tar -czf "${destination}/repository-config.tar.gz" configs workflows docker/comfyui deploy/control-plane 2>/dev/null
sha256sum "${destination}"/* > "${destination}/SHA256SUMS"
echo "备份完成: ${destination}"
