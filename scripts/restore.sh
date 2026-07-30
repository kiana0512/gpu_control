#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

usage() {
  cat <<'EOF'
用法: scripts/restore.sh --from BACKUP_DIR [组件] [选项]

组件（至少一个；未指定时为兼容旧行为，仅恢复 database）:
  --database              覆盖并恢复 PostgreSQL 数据库
  --globals               恢复 PostgreSQL 全局角色（裸机恢复才需要）
  --config                覆盖 GPU Control 仓库配置
  --worktree              覆盖备份时的完整 Git 工作树（仅 full）
  --secrets               恢复 .env、/srv/gpu-control/secrets、/etc/gpu-control（仅 full）
  --data                  恢复任务、资产、镜像、控制运行时和 ComfyUI 运行数据（仅 full）

选项:
  --repo-root DIR         config/worktree 恢复目标（默认由脚本位置推导）
  --host-root DIR         secrets/data 的根目录（默认 /；恢复演练可指向临时目录）
  --allow-crash-consistent
                          明确允许校验/恢复未执行 full 前后双门禁的候选备份
  --dry-run               完整校验并打印计划，不写入任何目标
  --verify-only           只校验完整备份集和归档成员
  -h, --help              显示帮助

每个写操作都有独立确认短语；--dry-run/--verify-only 永远不要求确认。
EOF
}

die() {
  echo "错误: $*" >&2
  exit 1
}

need_value() {
  [[ $# -ge 2 && -n "${2-}" ]] || die "$1 需要一个非空参数"
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
host_root="/"
source_dir=""
dry_run=false
verify_only=false
allow_crash_consistent=false
restore_database=false
restore_globals=false
restore_config=false
restore_worktree=false
restore_secrets=false
restore_data=false
component_selected=false

while (($#)); do
  case "$1" in
    --from)
      need_value "$@"
      source_dir="$2"
      shift 2
      ;;
    --repo-root)
      need_value "$@"
      repo_root="$2"
      shift 2
      ;;
    --host-root)
      need_value "$@"
      host_root="$2"
      shift 2
      ;;
    --database)
      restore_database=true; component_selected=true; shift
      ;;
    --globals)
      restore_globals=true; component_selected=true; shift
      ;;
    --config)
      restore_config=true; component_selected=true; shift
      ;;
    --worktree)
      restore_worktree=true; component_selected=true; shift
      ;;
    --secrets)
      restore_secrets=true; component_selected=true; shift
      ;;
    --data)
      restore_data=true; component_selected=true; shift
      ;;
    --dry-run)
      dry_run=true; shift
      ;;
    --verify-only)
      verify_only=true; shift
      ;;
    --allow-crash-consistent)
      allow_crash_consistent=true; shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "${source_dir}" ]] || { usage >&2; exit 2; }
[[ -d "${source_dir}" ]] || die "备份目录不存在: ${source_dir}"
source_dir_arg="${source_dir%/}"
[[ -n "${source_dir_arg}" ]] || source_dir_arg="/"
[[ ! -L "${source_dir_arg}" ]] || die "备份目录不能是符号链接: ${source_dir}"
source_dir="$(cd -- "${source_dir}" && pwd -P)"

current_uid="$(id -u)"
source_uid="$(stat -c '%u' -- "${source_dir}")"
source_mode="$(stat -c '%a' -- "${source_dir}")"
[[ "${source_uid}" == "${current_uid}" ]] || \
  die "备份目录所有者必须是当前执行用户（root 正式恢复时必须 root-owned）"
[[ "${source_mode}" == "700" ]] || \
  die "备份目录权限必须是 0700，当前为 ${source_mode}: ${source_dir}"

# The backup format intentionally has a flat, regular-file-only top level.
# Rejecting links and special files before reading SHA256SUMS prevents checksum
# entries from escaping the backup directory or reading an arbitrary host file.
while IFS= read -r -d '' entry; do
  [[ -f "${entry}" && ! -L "${entry}" ]] || \
    die "备份顶层只允许普通文件，拒绝: ${entry}"
  entry_uid="$(stat -c '%u' -- "${entry}")"
  entry_mode="$(stat -c '%a' -- "${entry}")"
  [[ "${entry_uid}" == "${source_uid}" ]] || \
    die "备份文件所有者与目录不一致: ${entry}"
  [[ "${entry_mode}" == "600" ]] || \
    die "备份文件权限必须是 0600，当前为 ${entry_mode}: ${entry}"
done < <(find "${source_dir}" -mindepth 1 -maxdepth 1 -print0)

[[ -f "${source_dir}/SHA256SUMS" && ! -L "${source_dir}/SHA256SUMS" ]] || \
  die "缺少普通文件 SHA256SUMS"
[[ -f "${source_dir}/BACKUP_COMPLETE" && ! -L "${source_dir}/BACKUP_COMPLETE" ]] || \
  die "缺少普通文件 BACKUP_COMPLETE；拒绝恢复不完整备份"
[[ -f "${source_dir}/BACKUP_MANIFEST" && ! -L "${source_dir}/BACKUP_MANIFEST" ]] || \
  die "缺少普通文件 BACKUP_MANIFEST"

complete_get() {
  local key="$1"
  awk -F= -v wanted="${key}" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' \
    "${source_dir}/BACKUP_COMPLETE"
}

[[ "$(complete_get STATUS)" == "COMPLETE" ]] || die "BACKUP_COMPLETE 状态无效"
expected_manifest_hash="$(complete_get SHA256SUMS_SHA256)"
[[ "${expected_manifest_hash}" =~ ^[0-9a-f]{64}$ ]] || \
  die "BACKUP_COMPLETE 缺少有效 SHA256SUMS_SHA256"
actual_manifest_hash="$(sha256sum -- "${source_dir}/SHA256SUMS" | awk '{print $1}')"
[[ "${actual_manifest_hash}" == "${expected_manifest_hash}" ]] || \
  die "SHA256SUMS 与 BACKUP_COMPLETE 固定值不一致"

manifest_get() {
  local key="$1"
  awk -F= -v wanted="${key}" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' \
    "${source_dir}/BACKUP_MANIFEST"
}

if [[ "${component_selected}" == false && "${verify_only}" == false ]]; then
  restore_database=true
fi

validate_checksum_manifest() {
  local line digest separator name entry
  local -A listed=()
  local count=0

  while IFS= read -r line || [[ -n "${line}" ]]; do
    ((${#line} >= 67)) || die "SHA256SUMS 包含格式错误的短行"
    digest="${line:0:64}"
    separator="${line:64:2}"
    name="${line:66}"
    [[ "${digest}" =~ ^[0-9a-f]{64}$ && "${separator}" == "  " ]] || \
      die "SHA256SUMS 行格式无效（只接受 sha256sum 文本模式）"
    [[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || \
      die "SHA256SUMS 只能引用安全的相对顶层文件名: ${name}"
    [[ "${name}" != "SHA256SUMS" && "${name}" != "BACKUP_COMPLETE" ]] || \
      die "SHA256SUMS 不得循环引用控制文件: ${name}"
    [[ -z "${listed[${name}]+x}" ]] || die "SHA256SUMS 重复引用文件: ${name}"
    [[ -f "${source_dir}/${name}" && ! -L "${source_dir}/${name}" ]] || \
      die "SHA256SUMS 引用的载荷不是普通文件: ${name}"
    listed["${name}"]=1
    ((count += 1))
  done < "${source_dir}/SHA256SUMS"
  ((count > 0)) || die "SHA256SUMS 为空"

  while IFS= read -r -d '' entry; do
    name="${entry##*/}"
    [[ "${name}" == "SHA256SUMS" || "${name}" == "BACKUP_COMPLETE" ]] && continue
    [[ -n "${listed[${name}]+x}" ]] || die "SHA256SUMS 未覆盖文件: ${name}"
  done < <(find "${source_dir}" -mindepth 1 -maxdepth 1 -type f -print0)

  (
    cd "${source_dir}"
    sha256sum --strict -c SHA256SUMS
  )
}

validate_archive_members() {
  local archive="$1" member listing member_type
  [[ -f "${archive}" && ! -L "${archive}" ]] || die "缺少普通归档文件: ${archive}"
  # A checksum proves byte integrity, not that the tar stream is readable.
  # Run a full listing once outside process substitution so tar's exit status
  # is never hidden by the reader loop.
  tar -tf "${archive}" >/dev/null
  while IFS= read -r listing; do
    member_type="${listing:0:1}"
    case "${member_type}" in
      -|d) ;;
      *) die "归档包含链接或特殊成员（类型 ${member_type}）: ${archive}" ;;
    esac
  done < <(tar -tvf "${archive}")
  while IFS= read -r member; do
    case "${member}" in
      /*|..|../*|*/../*|*/..)
        die "归档包含不安全路径 '${member}': ${archive}"
        ;;
    esac
  done < <(tar -tf "${archive}")
}

echo "校验完整备份集: ${source_dir}"
validate_checksum_manifest

backup_format="$(manifest_get BACKUP_FORMAT)"
backup_mode="$(manifest_get MODE)"
marker_mode="$(complete_get MODE)"
quiesce_check="$(manifest_get QUIESCE_CHECK)"
[[ "${backup_format}" == "2" ]] || die "不支持的 BACKUP_FORMAT: ${backup_format:-<缺失>}"
[[ "${backup_mode}" == "small" || "${backup_mode}" == "full" ]] || \
  die "BACKUP_MANIFEST MODE 无效: ${backup_mode:-<缺失>}"
[[ "${marker_mode}" == "${backup_mode}" ]] || \
  die "BACKUP_COMPLETE 与 BACKUP_MANIFEST 的 MODE 不一致"
if [[ "${backup_mode}" == "full" && "${quiesce_check}" != "ENFORCED_PRE_AND_POST" ]]; then
  [[ "${allow_crash_consistent}" == true ]] || \
    die "full 备份未执行前后双门禁；仅可显式增加 --allow-crash-consistent 后使用"
  echo "警告: 已明确允许 crash-consistent full 候选；该备份不应作为强一致生产基线。" >&2
fi

if [[ "${backup_mode}" != "full" && \
      ( "${restore_worktree}" == true || "${restore_secrets}" == true || "${restore_data}" == true ) ]]; then
  die "worktree/secrets/data 只能从 MODE=full 的备份恢复"
fi

[[ -f "${source_dir}/repository-config.tar.gz" ]] && \
  validate_archive_members "${source_dir}/repository-config.tar.gz"
[[ -f "${source_dir}/repository-worktree.tar" ]] && \
  validate_archive_members "${source_dir}/repository-worktree.tar"
[[ -f "${source_dir}/sensitive-config.tar.gz" ]] && \
  validate_archive_members "${source_dir}/sensitive-config.tar.gz"
[[ -f "${source_dir}/host-data.tar" ]] && \
  validate_archive_members "${source_dir}/host-data.tar"

if [[ "${verify_only}" == true ]]; then
  echo "备份完整性与归档路径校验通过；没有写入任何目标。"
  exit 0
fi

[[ "${repo_root}" == /* ]] || die "--repo-root 必须是绝对路径"
[[ "${host_root}" == /* ]] || die "--host-root 必须是绝对路径"

postgres_container="${POSTGRES_CONTAINER:-$(manifest_get POSTGRES_CONTAINER)}"
postgres_user="${POSTGRES_USER:-$(manifest_get POSTGRES_USER)}"
postgres_db="${POSTGRES_DB:-$(manifest_get POSTGRES_DB)}"
postgres_container="${postgres_container:-gpu-control-postgres-1}"
postgres_user="${postgres_user:-gpu_control}"
postgres_db="${postgres_db:-gpu_control}"

[[ "${restore_database}" == false || -f "${source_dir}/database.dump" ]] || die "缺少 database.dump"
[[ "${restore_globals}" == false || -f "${source_dir}/postgres-globals.sql" ]] || die "缺少 postgres-globals.sql"
[[ "${restore_config}" == false || -f "${source_dir}/repository-config.tar.gz" ]] || die "缺少 repository-config.tar.gz"
[[ "${restore_worktree}" == false || -f "${source_dir}/repository-worktree.tar" ]] || die "该备份没有 full 工作树归档"
[[ "${restore_secrets}" == false || -f "${source_dir}/sensitive-config.tar.gz" ]] || die "该备份没有敏感配置归档"
[[ "${restore_data}" == false || -f "${source_dir}/host-data.tar" ]] || die "该备份没有 host 数据归档"

echo "恢复计划:"
[[ "${restore_database}" == true ]] && echo "  - PostgreSQL ${postgres_db}（容器 ${postgres_container}，用户 ${postgres_user}）"
[[ "${restore_globals}" == true ]] && echo "  - PostgreSQL 全局角色"
[[ "${restore_config}" == true ]] && echo "  - 仓库配置 -> ${repo_root}"
[[ "${restore_worktree}" == true ]] && echo "  - 完整工作树 -> ${repo_root}"
[[ "${restore_secrets}" == true ]] && echo "  - 敏感配置 -> ${host_root}"
[[ "${restore_data}" == true ]] && echo "  - 任务/资产/镜像/运行数据 -> ${host_root}"

if [[ "${dry_run}" == true ]]; then
  echo "dry-run 完成：校验通过，没有写入数据库或文件系统。"
  exit 0
fi

confirm() {
  local expected="$1" prompt="$2" answer
  read -r -p "${prompt} 输入 ${expected}: " answer
  [[ "${answer}" == "${expected}" ]] || die "确认不匹配，未执行该恢复"
}

if [[ "${restore_globals}" == true ]]; then
  command -v docker >/dev/null 2>&1 || die "缺少 docker"
  confirm "RESTORE GLOBALS" "此操作可能创建/修改 PostgreSQL 角色。"
  docker exec -i "${postgres_container}" psql -v ON_ERROR_STOP=1 \
    -U "${postgres_user}" -d postgres < "${source_dir}/postgres-globals.sql"
fi

if [[ "${restore_database}" == true ]]; then
  command -v docker >/dev/null 2>&1 || die "缺少 docker"
  confirm "RESTORE DATABASE" "此操作会断开连接并覆盖数据库 ${postgres_db}。"
  docker exec "${postgres_container}" dropdb -U "${postgres_user}" \
    --force --if-exists "${postgres_db}"
  docker exec "${postgres_container}" createdb -U "${postgres_user}" "${postgres_db}"
  docker exec -i "${postgres_container}" pg_restore -v --exit-on-error \
    -U "${postgres_user}" -d "${postgres_db}" < "${source_dir}/database.dump"
fi

if [[ "${restore_config}" == true ]]; then
  [[ -d "${repo_root}" ]] || die "仓库恢复目标不存在: ${repo_root}"
  confirm "RESTORE CONFIG" "此操作会覆盖 ${repo_root} 下的 configs/workflows/docker/comfyui/deploy/control-plane。"
  tar -xzf "${source_dir}/repository-config.tar.gz" -C "${repo_root}"
fi

if [[ "${restore_worktree}" == true ]]; then
  [[ -d "${repo_root}" ]] || die "工作树恢复目标不存在: ${repo_root}"
  confirm "RESTORE WORKTREE" "此操作会覆盖 ${repo_root} 中备份所含的代码和文档。"
  tar -xf "${source_dir}/repository-worktree.tar" -C "${repo_root}"
fi

if [[ "${restore_secrets}" == true ]]; then
  [[ -d "${host_root}" ]] || install -d -m 0700 -- "${host_root}"
  confirm "RESTORE SECRETS" "此操作会写入明文生产凭据和 TLS 私钥。"
  tar -xzf "${source_dir}/sensitive-config.tar.gz" -C "${host_root}"
fi

if [[ "${restore_data}" == true ]]; then
  [[ -d "${host_root}" ]] || install -d -m 0700 -- "${host_root}"
  confirm "RESTORE DATA" "此操作会覆盖备份所含任务、资产、镜像和运行数据。"
  tar --sparse -xf "${source_dir}/host-data.tar" -C "${host_root}"
fi

echo "恢复步骤完成。启动 API 前运行 alembic upgrade head，再启动服务并执行 smoke test。"
