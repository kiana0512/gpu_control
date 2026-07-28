#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--host HOST --user USER] [--port PORT] [--known-hosts-file PATH] [--dry-run] [--prepare-only]" >&2
}

target_host=""
target_user=""
target_port="22"
known_hosts_file=""
dry_run=false
prepare_only=false
while (($#)); do
  case "$1" in
    --host) target_host="${2:?--host requires a value}"; shift 2 ;;
    --user) target_user="${2:?--user requires a value}"; shift 2 ;;
    --port) target_port="${2:?--port requires a value}"; shift 2 ;;
    --known-hosts-file) known_hosts_file="${2:?--known-hosts-file requires a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --prepare-only) prepare_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[[ "$target_port" =~ ^[0-9]+$ ]] && ((target_port >= 1 && target_port <= 65535)) || {
  echo "Invalid SSH port: $target_port" >&2
  exit 2
}
if [[ -n "$target_host" || -n "$target_user" ]]; then
  [[ -n "$target_host" && -n "$target_user" ]] || { usage; exit 2; }
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
skill_root="${CODEX_SKILL_ROOT_SOURCE:-${CODEX_HOME:-/home/lilithgames/.codex}/skills}"
codex_path="${CODEX_BINARY_SOURCE:-$(command -v codex || true)}"
runtime_root="${ASSET_RUNTIME_ROOT:-/opt/gpu-control/runtime}"

[[ -x "$codex_path" ]] || { echo "Codex CLI binary not found" >&2; exit 1; }
for skill in blender-pbr-uv blender-retopology-compare-iterate; do
  [[ -f "$skill_root/$skill/SKILL.md" ]] || {
    echo "Missing Skill: $skill_root/$skill" >&2
    exit 1
  }
done
"$repo_root/scripts/verify_asset_skills.sh" --root "$skill_root"

rsync_flags=(-a --delete)
$dry_run && rsync_flags+=(--dry-run --itemize-changes)

if [[ -z "$target_host" ]]; then
  $dry_run || mkdir -p "$runtime_root/codex" "$runtime_root/asset-skills"
  rsync "${rsync_flags[@]}" "$codex_path" "$runtime_root/codex/codex"
  for skill in blender-pbr-uv blender-retopology-compare-iterate; do
    rsync "${rsync_flags[@]}" "$skill_root/$skill/" \
      "$runtime_root/asset-skills/$skill/"
  done
  if ! $dry_run; then
    chmod 0755 "$runtime_root/codex/codex"
    "$repo_root/scripts/verify_asset_skills.sh" --root "$runtime_root/asset-skills"
  fi
  echo "Local Asset Worker runtime synchronized: $runtime_root"
  exit 0
fi

remote="$target_user@$target_host"
ssh_options=(-p "$target_port" -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
rsync_ssh=(ssh "${ssh_options[@]}")
if [[ -n "$known_hosts_file" ]]; then
  ssh_options+=(-o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts_file")
  rsync_ssh=(ssh "${ssh_options[@]}")
fi
if ! $dry_run; then
  ssh "${ssh_options[@]}" "$remote" mkdir -p \
    "$runtime_root/codex" "$runtime_root/asset-skills/blender-pbr-uv" \
    "$runtime_root/asset-skills/blender-retopology-compare-iterate"
fi
rsync "${rsync_flags[@]}" -e "${rsync_ssh[*]}" "$codex_path" "$remote:$runtime_root/codex/codex"
for skill in blender-pbr-uv blender-retopology-compare-iterate; do
  rsync "${rsync_flags[@]}" -e "${rsync_ssh[*]}" "$skill_root/$skill/" \
    "$remote:$runtime_root/asset-skills/$skill/"
done
if ! $dry_run; then
  ssh "${ssh_options[@]}" "$remote" chmod 0755 "$runtime_root/codex/codex"
  ssh "${ssh_options[@]}" "$remote" env \
    CODEX_BINARY="$runtime_root/codex/codex" \
    CODEX_HOME="/home/$target_user/.codex" \
    "$runtime_root/codex/codex" --version
  ssh "${ssh_options[@]}" "$remote" \
    "$repo_root/scripts/verify_asset_skills.sh" \
    --root "$runtime_root/asset-skills"
  if ! $prepare_only; then
    ssh "${ssh_options[@]}" "$remote" test -r /home/"$target_user"/.codex/auth.json
  fi
fi
echo "Remote Asset Worker runtime synchronized: $remote:$runtime_root"
if $prepare_only; then
  echo "Codex auth check skipped; run device login on the target before enabling retopology jobs"
fi
