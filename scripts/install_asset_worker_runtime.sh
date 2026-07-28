#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--host HOST --user USER] [--dry-run] [--prepare-only]" >&2
}

target_host=""
target_user=""
dry_run=false
prepare_only=false
while (($#)); do
  case "$1" in
    --host) target_host="${2:?--host requires a value}"; shift 2 ;;
    --user) target_user="${2:?--user requires a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --prepare-only) prepare_only=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
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
if ! $dry_run; then
  ssh "$remote" mkdir -p \
    "$runtime_root/codex" "$runtime_root/asset-skills/blender-pbr-uv" \
    "$runtime_root/asset-skills/blender-retopology-compare-iterate"
fi
rsync "${rsync_flags[@]}" "$codex_path" "$remote:$runtime_root/codex/codex"
for skill in blender-pbr-uv blender-retopology-compare-iterate; do
  rsync "${rsync_flags[@]}" "$skill_root/$skill/" \
    "$remote:$runtime_root/asset-skills/$skill/"
done
if ! $dry_run; then
  ssh "$remote" chmod 0755 "$runtime_root/codex/codex"
  ssh "$remote" env \
    CODEX_BINARY="$runtime_root/codex/codex" \
    CODEX_HOME="/home/$target_user/.codex" \
    "$runtime_root/codex/codex" --version
  ssh "$remote" \
    "$repo_root/scripts/verify_asset_skills.sh" \
    --root "$runtime_root/asset-skills"
  if ! $prepare_only; then
    ssh "$remote" test -r /home/"$target_user"/.codex/auth.json
  fi
fi
echo "Remote Asset Worker runtime synchronized: $remote:$runtime_root"
if $prepare_only; then
  echo "Codex auth check skipped; run device login on the target before enabling retopology jobs"
fi
