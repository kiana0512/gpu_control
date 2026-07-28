#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
skill_root="${CODEX_SKILL_ROOT:-${CODEX_HOME:-/home/lilithgames/.codex}/skills}"
codex_binary="${CODEX_BINARY:-$(command -v codex || true)}"

if [[ ${1:-} == "--root" ]]; then
  [[ $# -eq 2 ]] || { echo "Usage: $0 [--root DIR]" >&2; exit 2; }
  skill_root="$2"
elif (($#)); then
  echo "Usage: $0 [--root DIR]" >&2
  exit 2
fi

[[ -x "$codex_binary" ]] || {
  echo "Codex CLI is not executable: ${codex_binary:-<not found>}" >&2
  exit 1
}
command -v sha256sum >/dev/null || {
  echo "sha256sum is required" >&2
  exit 1
}

codex_version="$("$codex_binary" --version)"
exec_help="$("$codex_binary" exec --help)"
for required_flag in --json --output-schema --output-last-message --sandbox --ephemeral; do
  grep -Fq -- "$required_flag" <<<"$exec_help" || {
    echo "Codex CLI is missing required exec flag: $required_flag" >&2
    exit 1
  }
done

"$script_dir/verify_asset_skills.sh" --root "$skill_root"

printf 'Codex CLI preflight passed\n'
printf '  version: %s\n' "$codex_version"
printf '  binary:  %s\n' "$codex_binary"
printf '  skills:  %s\n' "$skill_root"
printf '  mode:    planning/orchestration only; deterministic Blender worker executes models\n'
