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

verify_file "blender-align-bake-models/SKILL.md" \
  "5a32c7759ae998504056e41bc14e556675bbbe6b828b72c9c44a8f2918106aba"
verify_file "blender-align-bake-models/agents/openai.yaml" \
  "a8e61cb47f50eef2fb97b6f29c5d3f3d900e63154e2c2f6257293c1c2e62962a"
verify_file "blender-align-bake-models/scripts/align_bake_models.py" \
  "ea0588e81fa50772080bc19ff096ee29cb5b6dbc67cdb303b9d32cdbf6a99a78"
verify_file "blender-align-bake-models/scripts/create_synthetic_pair.py" \
  "e214969e47c929f80e4f7b84e474d2872bb512a8de0169e777e634521541e8dc"
verify_file "blender-align-bake-models/scripts/render_alignment_views.py" \
  "cf4cf07003b030f6bb6c3c9023f03cfda38da837973f57dfa2f8eb4a71baba0b"
verify_file "blender-align-bake-models/scripts/validate_bake_pair.py" \
  "d8ee19f1fa0e3c93fbf6aa3f846afe1df7162f9569baabbdfaa9fea9f07ed358"
verify_file "blender-pbr-uv/SKILL.md" \
  "255bbeb16b99bb15c37ab085e57dbc26ce3f8c9f58083753423fe0bbe13d20a8"
verify_file "blender-pbr-uv/agents/openai.yaml" \
  "8c2940dcf2a9d0058ff5b8bec03e99185f3758a0f0b3ea60e7ce9345f243d5ac"
verify_file "blender-pbr-uv/references/pbr-uv-standard.md" \
  "06872924e99f2e856c36e3e5e0aefce23c06554e6809125a4fa7ec41970c75cb"
verify_file "blender-pbr-uv/references/mof-wrapper-notes.md" \
  "fc22d8de0ac3a217b4c63e975037691382395e4e9c9d588ee418640913a86aec"
verify_file "blender-pbr-uv/scripts/unwrap_fbx.py" \
  "ebfa3546d61c548a11c0e7561c75f93b6ef93308d8da9f27788bf35643303758"
verify_file "blender-pbr-uv/scripts/qa_uv.py" \
  "bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d"
verify_file "blender-pbr-uv/scripts/mof_unwrap.py" \
  "a45a10ebcae868ba82c1c14bddb6d82beb907961114dad6a4462e40ace7e409d"
verify_file "blender-pbr-uv/scripts/preflight_mof.py" \
  "d4639ebd34128b02496599eef55c21ed1eab295c6117fc234c819003e491db40"
verify_file "blender-retopology-compare-iterate/SKILL.md" \
  "71683855eb7cc093c3da676b196c094210bb851ed3b2d365e1cf377015b73cb1"
verify_file "blender-retopology-compare-iterate/agents/openai.yaml" \
  "1fe705f8bb73c94457a6df5cc409e07b923f00b000d1dca530161405729b0d79"
verify_file "blender-retopology-compare-iterate/references/high-only-game-topology.md" \
  "97ce9486480678c8f08c04c8994f6e90aa135811d8afe7eb33ce332ec828dc7d"
verify_file "blender-retopology-compare-iterate/references/n01-n08-training-lessons.md" \
  "edfdc92fe99e08ab6cc2ca7d63852ead42829ac03532147a3189c476301ac297"
verify_file "blender-retopology-compare-iterate/references/production-runbook.md" \
  "fdc63c8f4639817955e04d1f43a1d932d486a5fcfe28343c61bb544208345716"
verify_file "blender-retopology-compare-iterate/references/validated-batch-retrospective.md" \
  "9af3d1ebbe4ac304d82c65729f1301bf786fe975c69019b2b071065d8ca99558"
verify_file "blender-retopology-compare-iterate/scripts/audit_pair.py" \
  "bbc9990a045284be799df2f56f29b4a52f066c923eda0c65f2a88fe2d3128f1b"
verify_file "blender-retopology-compare-iterate/scripts/audit_batch_layout.py" \
  "c400add092827aff84b66915200b406d2501a3db6583d458dff6541fc60d4092"
verify_file "blender-retopology-compare-iterate/scripts/audit_topology_flow.py" \
  "cd1b9f59f3d8ccc65375e453c881a8776d8fbe4b48e47499754f861f5075b789"

echo "Asset Skills verified: $skill_root"
