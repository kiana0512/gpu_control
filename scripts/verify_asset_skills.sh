#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: verify_asset_skills.sh [--root DIR]

Verify the exact Blender UV and retopology Skill files approved for GPU Control.
The default root is $CODEX_HOME/skills when CODEX_HOME is set, otherwise
/home/lilithgames/.codex/skills.
EOF
}

skill_root="${CODEX_HOME:-/home/lilithgames/.codex}/skills"
while (($#)); do
  case "$1" in
    --root)
      [[ $# -ge 2 ]] || { echo "--root requires a directory" >&2; exit 2; }
      skill_root="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

verify_file() {
  local relative_path="$1"
  local expected="$2"
  local absolute_path="$skill_root/$relative_path"
  [[ -f "$absolute_path" ]] || {
    echo "MISSING  $absolute_path" >&2
    return 1
  }
  local actual
  actual="$(sha256sum "$absolute_path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "MISMATCH $absolute_path" >&2
    echo "  expected=$expected" >&2
    echo "  actual=$actual" >&2
    return 1
  }
  echo "OK       $relative_path  $actual"
}

verify_file "blender-pbr-uv/SKILL.md" \
  "37de0b496030e7b20151c7d5cbcf340ed4cd2ea36c132e50fc57743f5b4d427e"
verify_file "blender-pbr-uv/agents/openai.yaml" \
  "8c2940dcf2a9d0058ff5b8bec03e99185f3758a0f0b3ea60e7ce9345f243d5ac"
verify_file "blender-pbr-uv/references/pbr-uv-standard.md" \
  "06872924e99f2e856c36e3e5e0aefce23c06554e6809125a4fa7ec41970c75cb"
verify_file "blender-pbr-uv/scripts/unwrap_fbx.py" \
  "ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758"
verify_file "blender-pbr-uv/scripts/qa_uv.py" \
  "bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d"
verify_file "blender-retopology-compare-iterate/SKILL.md" \
  "e0bb19bcd35ec20a810cdc9f72905e2823052df429dde98110ec8b352ac3d7e4"
verify_file "blender-retopology-compare-iterate/agents/openai.yaml" \
  "b6cc3e9094c75b8acf778c282def96c620a36347515176d794fb42c1ecbfd81a"
verify_file "blender-retopology-compare-iterate/scripts/audit_pair.py" \
  "a6575902cfacd7b8106f9c887069d717a880d870fc48a6295431cdcf717a9dc4"

echo "Asset Skills verified: $skill_root"
