#!/usr/bin/env bash
set -Eeuo pipefail
usage(){ echo "用法: $0 --from BACKUP_DIR [--dry-run]"; }
source_dir=""; dry=false
while (($#)); do case "$1" in --from) source_dir="$2"; shift 2;; --dry-run) dry=true; shift;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
[[ -n "${source_dir}" && -f "${source_dir}/database.dump" && -f "${source_dir}/SHA256SUMS" ]] || { usage >&2; exit 2; }
(cd "${source_dir}" && sha256sum -c SHA256SUMS)
[[ "${dry}" == true ]] && { echo "校验通过；将清空并恢复数据库 ${POSTGRES_DB}"; exit 0; }
read -r -p "此操作会覆盖数据库。输入 RESTORE: " answer; [[ "${answer}" == RESTORE ]] || exit 1
docker compose -f deploy/control-plane/compose.yaml exec -T postgres dropdb -U "${POSTGRES_USER}" --if-exists "${POSTGRES_DB}"
docker compose -f deploy/control-plane/compose.yaml exec -T postgres createdb -U "${POSTGRES_USER}" "${POSTGRES_DB}"
docker compose -f deploy/control-plane/compose.yaml exec -T postgres pg_restore -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists < "${source_dir}/database.dump"
echo "数据库恢复完成；启动 API 前运行 alembic upgrade head。"
