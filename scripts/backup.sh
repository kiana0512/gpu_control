#!/usr/bin/env bash
set -Eeuo pipefail

# Backups may contain PostgreSQL role hashes, application credentials and TLS
# private keys.  Never let the caller's umask make them group/world readable.
umask 077

usage() {
  cat <<'EOF'
用法: scripts/backup.sh [选项]

选项:
  --mode small|full       small: 数据库、Git/配置和清单（默认）
                          full: 再包含当前工作树、运行数据、镜像归档和密钥
  --output DIR            备份根目录（默认 /srv/gpu-control/backups）
  --repo-root DIR         GPU Control 工作树（默认由脚本位置推导）
  --skip-quiesce-check    full 模式跳过“零任务 + GPU 节点 DRAINING”门禁
                          （仅限明确接受非一致性快照时使用）
  --dry-run               只打印计划，不创建文件或连接数据库
  -h, --help              显示帮助

环境变量:
  POSTGRES_CONTAINER      PostgreSQL 容器（默认 gpu-control-postgres-1）
  JOB_ROOT                任务目录（默认 /srv/gpu-control/jobs）
  ASSET_ROOT              资产任务目录（默认 /srv/gpu-control/assets）
  IMAGE_ARCHIVE_ROOT      离线镜像目录（默认 /srv/gpu-control/images）
  CONTROL_RUNTIME_ROOT    Asset/Codex 运行时（默认 /opt/gpu-control/runtime）
  COMFY_DATA_ROOT         4090 ComfyUI 运行数据（默认 /srv/comfyui/4090）
  SECRETS_ROOT            生产密钥目录（默认 /srv/gpu-control/secrets；不存在则跳过）
  SYSTEM_CONFIG_ROOT      主机配置目录（默认 /etc/gpu-control；不存在则跳过）
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
repo_root_default="$(cd -- "${script_dir}/.." && pwd -P)"

mode="small"
output="/srv/gpu-control/backups"
repo_root="${repo_root_default}"
dry_run=false
skip_quiesce_check=false

while (($#)); do
  case "$1" in
    --mode)
      need_value "$@"
      mode="$2"
      shift 2
      ;;
    --output)
      need_value "$@"
      output="$2"
      shift 2
      ;;
    --repo-root)
      need_value "$@"
      repo_root="$2"
      shift 2
      ;;
    --skip-quiesce-check)
      skip_quiesce_check=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
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

[[ "${mode}" == "small" || "${mode}" == "full" ]] || die "--mode 只能是 small 或 full"
[[ -d "${repo_root}/.git" ]] || die "不是 Git 工作树: ${repo_root}"
repo_root="$(cd -- "${repo_root}" && pwd -P)"
command -v realpath >/dev/null 2>&1 || die "缺少 realpath"

postgres_container="${POSTGRES_CONTAINER:-gpu-control-postgres-1}"
job_root="${JOB_ROOT:-/srv/gpu-control/jobs}"
asset_root="${ASSET_ROOT:-/srv/gpu-control/assets}"
image_archive_root="${IMAGE_ARCHIVE_ROOT:-/srv/gpu-control/images}"
control_runtime_root="${CONTROL_RUNTIME_ROOT:-/opt/gpu-control/runtime}"
comfy_data_root="${COMFY_DATA_ROOT:-/srv/comfyui/4090}"
secrets_root="${SECRETS_ROOT:-/srv/gpu-control/secrets}"
system_config_root="${SYSTEM_CONFIG_ROOT:-/etc/gpu-control}"

full_paths=(
  "${job_root}"
  "${asset_root}"
  "${image_archive_root}"
  "${control_runtime_root}"
  "${comfy_data_root}"
)

paths_overlap() {
  local left="${1%/}" right="${2%/}"
  [[ -n "${left}" ]] || left="/"
  [[ -n "${right}" ]] || right="/"
  [[ "${left}" == "${right}" || "${left}" == "${right}/"* || "${right}" == "${left}/"* ]]
}

# Resolve existing symlink components even when the final output directory does
# not exist yet.  A backup destination may never be inside (or contain) the
# repository or a full-backup source: otherwise tar can recursively archive the
# backup that it is currently writing and exhaust the filesystem.
output="$(realpath -m -- "${output}")"
[[ "${output}" != "/" ]] || die "备份根目录不能是 /"
paths_overlap "${output}" "${repo_root}" && \
  die "备份根目录与仓库工作树重叠: ${output} <-> ${repo_root}"

if [[ "${mode}" == "full" ]]; then
  for index in "${!full_paths[@]}"; do
    path="${full_paths[${index}]}"
    [[ "${path}" == /* ]] || die "full 数据路径必须是绝对路径: ${path}"
    [[ -e "${path}" ]] || die "full 备份所需路径不存在: ${path}"
    path="$(realpath -e -- "${path}")"
    [[ "${path}" != "/" ]] || die "full 数据路径不能是 /"
    full_paths[${index}]="${path}"
    paths_overlap "${output}" "${path}" && \
      die "备份根目录与 full 数据源重叠: ${output} <-> ${path}"
  done

  # Resolve sensitive sources before creating the destination.  This closes
  # the same recursive-self-backup class for secrets/config that is already
  # rejected for host data.  Links are rejected because the restore trust
  # boundary permits only ordinary files/directories in its archives.
  sensitive_paths=("${repo_root}/.env")
  [[ -e "${secrets_root}" ]] && sensitive_paths+=("${secrets_root}")
  [[ -e "${system_config_root}" ]] && sensitive_paths+=("${system_config_root}")
  for index in "${!sensitive_paths[@]}"; do
    path="${sensitive_paths[${index}]}"
    [[ "${path}" == /* ]] || die "密钥路径必须是绝对路径: ${path}"
    [[ -e "${path}" ]] || die "full 备份所需敏感路径不存在: ${path}"
    [[ ! -L "${path}" ]] || die "full 备份敏感路径不能是符号链接: ${path}"
    path="$(realpath -e -- "${path}")"
    [[ "${path}" != "/" ]] || die "full 备份敏感路径不能是 /"
    sensitive_paths[${index}]="${path}"
    paths_overlap "${output}" "${path}" && \
      die "备份根目录与敏感数据源重叠: ${output} <-> ${path}"
  done
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${output}/${stamp}-${mode}"

if [[ "${dry_run}" == true ]]; then
  echo "备份模式: ${mode}"
  echo "目标目录: ${destination}"
  echo "small 内容: PostgreSQL custom dump/roles、Git bundle/LFS 清单、当前部署配置、Docker 清单、SHA-256"
  if [[ "${mode}" == "full" ]]; then
    echo "full 额外内容: 当前 Git 工作树、根 .env、/srv/gpu-control/secrets、/etc/gpu-control，以及:"
    printf '  - %s\n' "${full_paths[@]}"
    if [[ "${skip_quiesce_check}" == false ]]; then
      echo "执行前后双门禁: 所有 GPU 节点必须 DRAINING，GPU/批次/资产任务必须为 0"
    else
      echo "警告: 已请求跳过一致性门禁；生成的 full 备份只能视为 crash-consistent 候选"
    fi
    echo "安全警告: full 备份包含生产凭据和 TLS 私钥，只能以 0700/0600 保存并加密离机传输"
  fi
  exit 0
fi

command -v docker >/dev/null 2>&1 || die "缺少 docker"
command -v git >/dev/null 2>&1 || die "缺少 git"
command -v tar >/dev/null 2>&1 || die "缺少 tar"
command -v sha256sum >/dev/null 2>&1 || die "缺少 sha256sum"

install -d -m 0700 -- "${output}"
[[ ! -e "${destination}" ]] || die "备份目录已存在: ${destination}"
install -d -m 0700 -- "${destination}"

on_error() {
  local rc=$?
  echo "备份失败（目录保留用于诊断，且没有 BACKUP_COMPLETE）: ${destination}" >&2
  exit "${rc}"
}
trap on_error ERR

gate_sql=$(cat <<'SQL'
SELECT 'active_jobs=' || count(*)
  FROM jobs
 WHERE status::text NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT');
SELECT 'active_batches=' || count(*)
  FROM job_batches
 WHERE status::text NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT');
SELECT 'active_asset_jobs=' || count(*)
  FROM asset_jobs
 WHERE status::text NOT IN (
   'SUCCEEDED', 'WAITING_REVIEW', 'REVIEW_REJECTED', 'FAILED', 'CANCELLED'
 );
SELECT 'busy_nodes=' || count(*)
  FROM nodes
 WHERE current_jobs <> 0;
SELECT 'accepting_online_nodes=' || count(*)
  FROM nodes
 WHERE status::text = 'ONLINE' AND mode::text <> 'DRAINING';
SQL
)

run_quiesce_gate() {
  local phase="$1" gate_output gate_name gate_value expected_name
  local -a expected_names=(
    active_jobs
    active_batches
    active_asset_jobs
    busy_nodes
    accepting_online_nodes
  )
  local -A expected=() seen=()
  for expected_name in "${expected_names[@]}"; do
    expected["${expected_name}"]=1
  done
  gate_output="$(printf '%s\n' "${gate_sql}" | docker exec -i "${postgres_container}" \
      sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At')"
  printf '%s\n' "${gate_output}" > "${destination}/quiesce-gate-${phase}.txt"
  while IFS='=' read -r gate_name gate_value; do
    [[ -n "${expected[${gate_name}]+x}" ]] || \
      die "${phase} 一致性门禁包含未知检查项: ${gate_name:-<空>}"
    [[ -z "${seen[${gate_name}]+x}" ]] || \
      die "${phase} 一致性门禁包含重复检查项: ${gate_name}"
    [[ "${gate_value}" =~ ^[0-9]+$ ]] || \
      die "无法解析 ${phase} 一致性门禁: ${gate_name}=${gate_value}"
    seen["${gate_name}"]=1
    (( gate_value == 0 )) || \
      die "${phase} 一致性门禁未通过: ${gate_name}=${gate_value}"
  done <<< "${gate_output}"
  for expected_name in "${expected_names[@]}"; do
    [[ -n "${seen[${expected_name}]+x}" ]] || \
      die "${phase} 一致性门禁缺少检查项: ${expected_name}"
  done
}

if [[ "${mode}" == "full" ]]; then
  if [[ "${skip_quiesce_check}" == false ]]; then
    run_quiesce_gate pre
  else
    printf '%s\n' \
      'QUIESCE_CHECK=SKIPPED' \
      'CONSISTENCY=NOT_GUARANTEED' > "${destination}/quiesce-gate.txt"
  fi
fi

db_identity="$(docker exec "${postgres_container}" sh -lc \
  'printf "%s\t%s" "$POSTGRES_USER" "$POSTGRES_DB"')"
IFS=$'\t' read -r postgres_user postgres_db <<< "${db_identity}"
[[ -n "${postgres_user}" && -n "${postgres_db}" ]] || die "无法读取 PostgreSQL 数据库身份"

docker exec "${postgres_container}" sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "${destination}/database.dump"
docker exec "${postgres_container}" sh -lc \
  'pg_dumpall -U "$POSTGRES_USER" --globals-only' \
  > "${destination}/postgres-globals.sql"

repo_config_paths=(configs workflows docker/comfyui deploy/control-plane)
for path in "${repo_config_paths[@]}"; do
  [[ -e "${repo_root}/${path}" ]] || die "仓库配置路径不存在: ${repo_root}/${path}"
done
tar -C "${repo_root}" -czf "${destination}/repository-config.tar.gz" \
  -- "${repo_config_paths[@]}"

git -C "${repo_root}" bundle create "${destination}/repository.bundle" --all
git -C "${repo_root}" status --porcelain=v1 --untracked-files=all \
  > "${destination}/git-status.txt"
git -C "${repo_root}" diff --binary > "${destination}/git-worktree.patch"
git -C "${repo_root}" diff --cached --binary > "${destination}/git-index.patch"
git -C "${repo_root}" rev-parse HEAD > "${destination}/git-head.txt"
git -C "${repo_root}" remote -v > "${destination}/git-remotes.txt"
if git -C "${repo_root}" check-attr filter -- .gitattributes | grep -q 'filter: lfs' || \
   { [[ -f "${repo_root}/.gitattributes" ]] && grep -q 'filter=lfs' "${repo_root}/.gitattributes"; }; then
  command -v git-lfs >/dev/null 2>&1 || git lfs version >/dev/null 2>&1 || \
    die "仓库使用 Git LFS，但 git-lfs 不可用"
  git -C "${repo_root}" lfs ls-files --all --long > "${destination}/git-lfs-files.txt"
else
  : > "${destination}/git-lfs-files.txt"
fi

docker ps --no-trunc > "${destination}/docker-containers.txt"
docker image ls --digests --no-trunc > "${destination}/docker-images.txt"
docker volume ls > "${destination}/docker-volumes.txt"

cat > "${destination}/BACKUP_MANIFEST" <<EOF
BACKUP_FORMAT=2
MODE=${mode}
CREATED_UTC=${stamp}
REPOSITORY_ROOT=${repo_root}
GIT_HEAD=$(<"${destination}/git-head.txt")
POSTGRES_CONTAINER=${postgres_container}
POSTGRES_USER=${postgres_user}
POSTGRES_DB=${postgres_db}
QUIESCE_CHECK=$([[ "${mode}" == "full" && "${skip_quiesce_check}" == false ]] && echo ENFORCED_PRE_AND_POST || echo NOT_ENFORCED)
EOF

if [[ "${mode}" == "full" ]]; then
  echo "警告: 正在生成包含生产凭据/TLS 私钥的 full 备份；目标文件保持 0600。" >&2

  # Capture the exact current tracked/untracked (non-ignored) worktree.  This
  # complements repository.bundle when deployment work has not yet committed.
  git -C "${repo_root}" ls-files -z --cached --others --exclude-standard \
    > "${destination}/repository-worktree-files.list"
  tar --null -C "${repo_root}" -cf "${destination}/repository-worktree.tar" \
    -T "${destination}/repository-worktree-files.list"

  sensitive_rel=()
  for path in "${sensitive_paths[@]}"; do sensitive_rel+=("${path#/}"); done
  tar -C / -czf "${destination}/sensitive-config.tar.gz" -- "${sensitive_rel[@]}"

  full_rel=()
  for path in "${full_paths[@]}"; do full_rel+=("${path#/}"); done
  # Host data is intentionally uncompressed: images and job artifacts are
  # commonly compressed already, and an uncompressed tar is faster to verify
  # and recover during an outage.
  tar --sparse -C / -cf "${destination}/host-data.tar" -- "${full_rel[@]}"

  # This second snapshot detects jobs that appeared while the potentially long
  # filesystem archives were being created.  It is a detector, not an API
  # submission lock; operators must still stop external submissions first.
  if [[ "${skip_quiesce_check}" == false ]]; then
    run_quiesce_gate post
  fi
fi

chmod 0700 "${destination}"
find "${destination}" -maxdepth 1 -type f -exec chmod 0600 {} +

(
  cd "${destination}"
  mapfile -d '' files < <(find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\0' | sort -z)
  ((${#files[@]} > 0)) || die "备份目录中没有可校验文件"
  sha256sum -- "${files[@]}" > SHA256SUMS
  chmod 0600 SHA256SUMS
  sha256sum --strict -c SHA256SUMS
)

# Publish the completion marker only after every payload file has been hashed
# and the resulting checksum manifest has passed its own verification.  The
# marker pins that manifest without creating a circular checksum dependency.
sha_manifest_hash="$(sha256sum -- "${destination}/SHA256SUMS" | awk '{print $1}')"
marker_tmp="${destination}/.BACKUP_COMPLETE.tmp.$$"
cat > "${marker_tmp}" <<EOF
STATUS=COMPLETE
CREATED_UTC=${stamp}
MODE=${mode}
SHA256SUMS_SHA256=${sha_manifest_hash}
EOF
chmod 0600 "${marker_tmp}"
mv -f -- "${marker_tmp}" "${destination}/BACKUP_COMPLETE"

trap - ERR
echo "备份完成并通过 SHA-256 校验: ${destination}"
if [[ "${mode}" == "full" ]]; then
  echo "安全提示: 此目录包含明文生产凭据；请加密后离机复制，并限制为 root/备份管理员读取。"
fi
