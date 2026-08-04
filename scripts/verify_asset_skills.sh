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
  "1b6519d3b725e89ca3beccaf5bc1de8dc5d3a1163b4dc2ce59cb5d0a277a61cf"
verify_file "blender-retopology-compare-iterate/agents/openai.yaml" \
  "1fe705f8bb73c94457a6df5cc409e07b923f00b000d1dca530161405729b0d79"
verify_file "blender-retopology-compare-iterate/references/high-only-game-topology.md" \
  "924744edf10c463a2e6a92525a27da12090955336147e8471c93db5c94fb32c4"
verify_file "blender-retopology-compare-iterate/references/n01-n08-training-lessons.md" \
  "80fee199e60725742d62093b1434076178b45efb07adba3ef13085e8fe660db8"
verify_file "blender-retopology-compare-iterate/references/production-runbook.md" \
  "727971150db58da23dcb5f6e76b370a3c959245f2db6b5e0170559ccc375db7e"
verify_file "blender-retopology-compare-iterate/references/validated-batch-retrospective.md" \
  "799c38ce365816ee57d1ef1e25c2310f71162dd36e4dbc304320fcf93595f5bd"
verify_file "blender-retopology-compare-iterate/scripts/audit_pair.py" \
  "bbc9990a045284be799df2f56f29b4a52f066c923eda0c65f2a88fe2d3128f1b"
verify_file "blender-retopology-compare-iterate/scripts/audit_batch_layout.py" \
  "c400add092827aff84b66915200b406d2501a3db6583d458dff6541fc60d4092"
verify_file "blender-retopology-compare-iterate/scripts/audit_topology_flow.py" \
  "cd1b9f59f3d8ccc65375e453c881a8776d8fbe4b48e47499754f861f5075b789"

echo "Asset Skills verified: $skill_root"
