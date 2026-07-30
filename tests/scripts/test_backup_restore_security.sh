#!/usr/bin/env bash
set -Eeuo pipefail

# Synthetic regression coverage for the backup/restore trust boundary.  This
# test never reads or writes production payloads and never calls real Docker.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
backup_script="${repo_root}/scripts/backup.sh"
restore_script="${repo_root}/scripts/restore.sh"
test_root="$(mktemp -d /tmp/gpu-control-backup-restore-test.XXXXXX)"
pass_count=0

cleanup() {
  case "${test_root}" in
    /tmp/gpu-control-backup-restore-test.*) rm -rf -- "${test_root}" ;;
    *) echo "refusing unsafe cleanup target: ${test_root}" >&2 ;;
  esac
}
trap cleanup EXIT

pass() {
  pass_count=$((pass_count + 1))
  printf 'PASS %02d: %s\n' "${pass_count}" "$1"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

expect_pass() {
  local name="$1"
  shift
  if "$@" >"${test_root}/last.log" 2>&1; then
    pass "${name}"
  else
    cat "${test_root}/last.log" >&2
    fail "${name}"
  fi
}

expect_fail() {
  local name="$1" expected="$2"
  shift 2
  if "$@" >"${test_root}/last.log" 2>&1; then
    cat "${test_root}/last.log" >&2
    fail "${name}: command unexpectedly succeeded"
  fi
  grep -F -- "${expected}" "${test_root}/last.log" >/dev/null || {
    cat "${test_root}/last.log" >&2
    fail "${name}: expected message not found: ${expected}"
  }
  pass "${name}"
}

finalize_fixture() {
  local dir="$1" mode manifest_hash
  rm -f -- "${dir}/SHA256SUMS" "${dir}/BACKUP_COMPLETE"
  chmod 0700 -- "${dir}"
  find "${dir}" -mindepth 1 -maxdepth 1 -type f -exec chmod 0600 {} +
  (
    cd "${dir}"
    mapfile -d '' files < <(
      find . -maxdepth 1 -type f ! -name SHA256SUMS ! -name BACKUP_COMPLETE \
        -printf '%P\0' | sort -z
    )
    sha256sum -- "${files[@]}" > SHA256SUMS
    chmod 0600 SHA256SUMS
  )
  mode="$(awk -F= '$1 == "MODE" {print $2; exit}' "${dir}/BACKUP_MANIFEST")"
  manifest_hash="$(sha256sum -- "${dir}/SHA256SUMS" | awk '{print $1}')"
  printf 'STATUS=COMPLETE\nMODE=%s\nSHA256SUMS_SHA256=%s\n' \
    "${mode}" "${manifest_hash}" > "${dir}/BACKUP_COMPLETE"
  chmod 0600 -- "${dir}/BACKUP_COMPLETE"
}

make_fixture() {
  local dir="$1" mode="${2:-small}" gate="${3:-NOT_ENFORCED}"
  install -d -m 0700 -- "${dir}"
  printf '%s\n' \
    'BACKUP_FORMAT=2' \
    "MODE=${mode}" \
    'POSTGRES_CONTAINER=test-postgres' \
    'POSTGRES_USER=gpu_control' \
    'POSTGRES_DB=gpu_control' \
    "QUIESCE_CHECK=${gate}" > "${dir}/BACKUP_MANIFEST"
  printf 'synthetic database dump\n' > "${dir}/database.dump"
  finalize_fixture "${dir}"
}

make_fixture "${test_root}/valid"
expect_pass "valid format-2 backup verifies" \
  "${restore_script}" --from "${test_root}/valid" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/source-real"
ln -s -- "${test_root}/source-real" "${test_root}/source-link"
expect_fail "backup directory symlink is rejected" "备份目录不能是符号链接" \
  "${restore_script}" --from "${test_root}/source-link" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/top-link"
printf 'outside\n' > "${test_root}/outside.bin"
rm -f -- "${test_root}/top-link/database.dump"
ln -s -- "${test_root}/outside.bin" "${test_root}/top-link/database.dump"
expect_fail "top-level payload symlink is rejected" "备份顶层只允许普通文件" \
  "${restore_script}" --from "${test_root}/top-link" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/unsafe-sha"
outside_hash="$(sha256sum -- "${test_root}/outside.bin" | awk '{print $1}')"
printf '%s  ../outside.bin\n' "${outside_hash}" > "${test_root}/unsafe-sha/SHA256SUMS"
sha_list_hash="$(sha256sum -- "${test_root}/unsafe-sha/SHA256SUMS" | awk '{print $1}')"
printf 'STATUS=COMPLETE\nMODE=small\nSHA256SUMS_SHA256=%s\n' "${sha_list_hash}" \
  > "${test_root}/unsafe-sha/BACKUP_COMPLETE"
chmod 0600 "${test_root}/unsafe-sha/"{SHA256SUMS,BACKUP_COMPLETE}
expect_fail "checksum path traversal is rejected" "安全的相对顶层文件名" \
  "${restore_script}" --from "${test_root}/unsafe-sha" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/unlisted"
printf 'not in checksum manifest\n' > "${test_root}/unlisted/extra.bin"
chmod 0600 "${test_root}/unlisted/extra.bin"
expect_fail "unmanifested top-level payload is rejected" "SHA256SUMS 未覆盖文件" \
  "${restore_script}" --from "${test_root}/unlisted" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/bad-permission"
chmod 0644 "${test_root}/bad-permission/database.dump"
expect_fail "world/group-readable payload is rejected" "备份文件权限必须是 0600" \
  "${restore_script}" --from "${test_root}/bad-permission" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/missing-manifest"
rm -f -- "${test_root}/missing-manifest/BACKUP_MANIFEST"
expect_fail "missing manifest is rejected" "缺少普通文件 BACKUP_MANIFEST" \
  "${restore_script}" --from "${test_root}/missing-manifest" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/bad-format"
sed -i 's/^BACKUP_FORMAT=.*/BACKUP_FORMAT=999/' \
  "${test_root}/bad-format/BACKUP_MANIFEST"
finalize_fixture "${test_root}/bad-format"
expect_fail "unknown backup format is rejected" "不支持的 BACKUP_FORMAT: 999" \
  "${restore_script}" --from "${test_root}/bad-format" --verify-only

cp -a -- "${test_root}/valid" "${test_root}/mode-mismatch"
sed -i 's/^MODE=small$/MODE=full/' "${test_root}/mode-mismatch/BACKUP_COMPLETE"
expect_fail "marker/manifest mode mismatch is rejected" "MODE 不一致" \
  "${restore_script}" --from "${test_root}/mode-mismatch" --verify-only

make_fixture "${test_root}/crash-full" full NOT_ENFORCED
expect_fail "crash-consistent full backup is denied by default" \
  "仅可显式增加 --allow-crash-consistent" \
  "${restore_script}" --from "${test_root}/crash-full" --verify-only
expect_pass "crash-consistent full backup needs explicit opt-in" \
  "${restore_script}" --from "${test_root}/crash-full" \
    --allow-crash-consistent --verify-only

make_fixture "${test_root}/archive-link"
install -d -- "${test_root}/archive-src/configs"
ln -s -- ../../outside "${test_root}/archive-src/configs/escape"
tar -C "${test_root}/archive-src" -czf \
  "${test_root}/archive-link/repository-config.tar.gz" configs
finalize_fixture "${test_root}/archive-link"
expect_fail "archive symlink member is rejected" "归档包含链接或特殊成员" \
  "${restore_script}" --from "${test_root}/archive-link" --verify-only

make_fixture "${test_root}/archive-traversal"
printf 'escape attempt\n' > "${test_root}/archive-src/file"
tar -C "${test_root}/archive-src" --transform='s|^file$|../escape|' -cf \
  "${test_root}/archive-traversal/repository-worktree.tar" file
finalize_fixture "${test_root}/archive-traversal"
expect_fail "archive parent traversal member is rejected" "归档包含不安全路径" \
  "${restore_script}" --from "${test_root}/archive-traversal" --verify-only

expect_fail "backup output overlapping repository is rejected" "备份根目录与仓库工作树重叠" \
  "${backup_script}" --mode small --repo-root "${repo_root}" \
    --output "${repo_root}/synthetic-backup" --dry-run

install -d -- "${test_root}/root-path-fixtures/"{assets,images,runtime,comfy}
expect_fail "full source root slash is rejected" "full 数据路径不能是 /" \
  env JOB_ROOT=/ \
      ASSET_ROOT="${test_root}/root-path-fixtures/assets" \
      IMAGE_ARCHIVE_ROOT="${test_root}/root-path-fixtures/images" \
      CONTROL_RUNTIME_ROOT="${test_root}/root-path-fixtures/runtime" \
      COMFY_DATA_ROOT="${test_root}/root-path-fixtures/comfy" \
      "${backup_script}" --mode full --repo-root "${repo_root}" \
        --output "${test_root}/root-path-output" --dry-run

# Build an isolated Git repository plus Docker/tar fakes so the real backup
# script can exercise marker publication and both full quiescence gates without
# touching production containers or payloads.
fixture_repo="${test_root}/repo"
install -d -- "${fixture_repo}/"{configs,workflows,docker/comfyui,deploy/control-plane}
printf 'fixture\n' > "${fixture_repo}/configs/config.txt"
printf '{}\n' > "${fixture_repo}/workflows/workflow.json"
printf 'synthetic secret\n' > "${fixture_repo}/.env"
git -C "${fixture_repo}" init -q
git -C "${fixture_repo}" config user.name 'Backup Test'
git -C "${fixture_repo}" config user.email 'backup-test@example.invalid'
git -C "${fixture_repo}" add .
git -C "${fixture_repo}" commit -qm fixture

fake_bin="${test_root}/fake-bin"
install -d -- "${fake_bin}"
cat > "${fake_bin}/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1-}" == exec ]]; then
  shift
  stdin=false
  if [[ "${1-}" == -i ]]; then stdin=true; shift; fi
  shift
  if [[ "${1-}" == sh && "${2-}" == -lc ]]; then
    code="${3-}"
    if [[ "${code}" == *'psql -v ON_ERROR_STOP'* ]]; then
      [[ "${stdin}" == true ]] && cat >/dev/null || true
      gate_number=1
      if [[ -n "${FAKE_DOCKER_LOG:-}" && -f "${FAKE_DOCKER_LOG}" ]]; then
        gate_number=$(( $(grep -c '^gate$' "${FAKE_DOCKER_LOG}" || true) + 1 ))
      fi
      [[ -z "${FAKE_DOCKER_LOG:-}" ]] || printf 'gate\n' >> "${FAKE_DOCKER_LOG}"
      active=0
      [[ "${FAKE_GATE_FAIL_ON:-0}" != "${gate_number}" ]] || active=1
      printf 'active_jobs=%s\nactive_batches=0\nactive_asset_jobs=0\n' "${active}"
      [[ "${FAKE_GATE_PARTIAL_ON:-0}" == "${gate_number}" ]] && exit 0
      printf 'busy_nodes=0\naccepting_online_nodes=0\n'
    elif [[ "${code}" == *'printf "%s\t%s"'* ]]; then
      printf 'gpu_control\tgpu_control'
    elif [[ "${code}" == *pg_dumpall* ]]; then
      printf '%s\n' '-- synthetic globals'
    elif [[ "${code}" == *pg_dump* ]]; then
      printf '%s\n' 'synthetic database dump'
    else
      exit 91
    fi
  else
    exit 92
  fi
elif [[ "${1-}" == ps || \
        ( "${1-}" == image && "${2-}" == ls ) || \
        ( "${1-}" == volume && "${2-}" == ls ) ]]; then
  printf 'synthetic Docker inventory\n'
else
  exit 93
fi
FAKE_DOCKER

cat > "${fake_bin}/tar" <<'FAKE_TAR'
#!/usr/bin/env bash
set -Eeuo pipefail
output=''
compressed=false
args=("$@")
for ((index = 0; index < ${#args[@]}; index++)); do
  case "${args[${index}]}" in
    -czf) compressed=true; output="${args[$((index + 1))]}" ;;
    -cf) output="${args[$((index + 1))]}" ;;
  esac
done
[[ -n "${output}" ]] || exec /usr/bin/tar "$@"
if [[ "${output##*/}" == host-data.tar ]]; then
  exec /usr/bin/tar "$@"
fi
if [[ "${compressed}" == true ]]; then
  exec /usr/bin/tar -czf "${output}" --files-from /dev/null
fi
exec /usr/bin/tar -cf "${output}" --files-from /dev/null
FAKE_TAR
chmod 0755 "${fake_bin}/docker" "${fake_bin}/tar"

small_output="${test_root}/small-output"
expect_pass "real small backup script publishes a verifiable fixture" \
  env PATH="${fake_bin}:${PATH}" \
    "${backup_script}" --mode small --repo-root "${fixture_repo}" \
      --output "${small_output}"
small_backup="$(find "${small_output}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
[[ -n "${small_backup}" ]] || fail "small backup directory was not created"
[[ "$(stat -c '%a' "${small_backup}")" == 700 ]] || fail "small directory mode"
while IFS= read -r -d '' file; do
  [[ "$(stat -c '%a' "${file}")" == 600 ]] || fail "small payload mode: ${file}"
done < <(find "${small_backup}" -maxdepth 1 -type f -print0)
expect_pass "newly generated small backup passes strict restore verifier" \
  "${restore_script}" --from "${small_backup}" --verify-only

data_root="${test_root}/data"
install -d -- "${data_root}/"{jobs,assets,images,runtime,comfy}
for dir in jobs assets images runtime comfy; do
  printf 'fixture\n' > "${data_root}/${dir}/payload"
done
# A production job tree contains many hard-linked frame inputs.  The full
# backup must materialize them as regular members because restore rejects link
# members at its trust boundary.
ln "${data_root}/jobs/payload" "${data_root}/jobs/payload-hardlink"

full_output="${test_root}/full-output"
gate_log="${test_root}/full-gates.log"
expect_pass "real full backup executes pre/post gates and publishes marker" \
  env PATH="${fake_bin}:${PATH}" FAKE_DOCKER_LOG="${gate_log}" \
      JOB_ROOT="${data_root}/jobs" ASSET_ROOT="${data_root}/assets" \
      IMAGE_ARCHIVE_ROOT="${data_root}/images" \
      CONTROL_RUNTIME_ROOT="${data_root}/runtime" \
      COMFY_DATA_ROOT="${data_root}/comfy" \
    "${backup_script}" --mode full --repo-root "${fixture_repo}" \
      --output "${full_output}"
full_backup="$(find "${full_output}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
[[ "$(grep -c '^gate$' "${gate_log}")" == 2 ]] || fail "full backup did not run two gates"
[[ -f "${full_backup}/quiesce-gate-pre.txt" ]] || fail "missing pre gate artifact"
[[ -f "${full_backup}/quiesce-gate-post.txt" ]] || fail "missing post gate artifact"
if find "${full_backup}" -maxdepth 1 -name '.BACKUP_COMPLETE.tmp.*' | grep -q .; then
  fail "temporary completion marker leaked"
fi
expect_pass "newly generated dual-gated full backup verifies" \
  "${restore_script}" --from "${full_backup}" --verify-only

partial_output="${test_root}/partial-gate-output"
partial_gate_log="${test_root}/partial-gate.log"
expect_fail "incomplete quiescence result cannot publish a backup" \
  "pre 一致性门禁缺少检查项: busy_nodes" \
  env PATH="${fake_bin}:${PATH}" FAKE_DOCKER_LOG="${partial_gate_log}" \
      FAKE_GATE_PARTIAL_ON=1 \
      JOB_ROOT="${data_root}/jobs" ASSET_ROOT="${data_root}/assets" \
      IMAGE_ARCHIVE_ROOT="${data_root}/images" \
      CONTROL_RUNTIME_ROOT="${data_root}/runtime" \
      COMFY_DATA_ROOT="${data_root}/comfy" \
    "${backup_script}" --mode full --repo-root "${fixture_repo}" \
      --output "${partial_output}"

sensitive_root="${test_root}/sensitive-source"
install -d -- "${sensitive_root}"
expect_fail "backup output overlapping sensitive source is rejected" \
  "备份根目录与敏感数据源重叠" \
  env JOB_ROOT="${data_root}/jobs" ASSET_ROOT="${data_root}/assets" \
      IMAGE_ARCHIVE_ROOT="${data_root}/images" \
      CONTROL_RUNTIME_ROOT="${data_root}/runtime" \
      COMFY_DATA_ROOT="${data_root}/comfy" \
      SECRETS_ROOT="${sensitive_root}" SYSTEM_CONFIG_ROOT="${test_root}/absent-config" \
    "${backup_script}" --mode full --repo-root "${fixture_repo}" \
      --output "${sensitive_root}/backups" --dry-run

failed_output="${test_root}/post-gate-failed-output"
failed_gate_log="${test_root}/post-gate-failed.log"
expect_fail "post-backup quiescence failure prevents completion marker" \
  "post 一致性门禁未通过" \
  env PATH="${fake_bin}:${PATH}" FAKE_DOCKER_LOG="${failed_gate_log}" \
      FAKE_GATE_FAIL_ON=2 \
      JOB_ROOT="${data_root}/jobs" ASSET_ROOT="${data_root}/assets" \
      IMAGE_ARCHIVE_ROOT="${data_root}/images" \
      CONTROL_RUNTIME_ROOT="${data_root}/runtime" \
      COMFY_DATA_ROOT="${data_root}/comfy" \
    "${backup_script}" --mode full --repo-root "${fixture_repo}" \
      --output "${failed_output}"
failed_backup="$(find "${failed_output}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
[[ -n "${failed_backup}" && ! -e "${failed_backup}/BACKUP_COMPLETE" ]] || \
  fail "failed full backup published completion marker"
pass "failed full candidate is retained without BACKUP_COMPLETE"

printf 'All %d backup/restore security tests passed.\n' "${pass_count}"
