import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.gpu_control_core.assets import (
    RETOPOLOGY_V6_POLICY_SHA256,
    adapt_retopology_v6_metadata_json,
    retopology_v6_process_request_hash,
)
from packages.gpu_control_core.retopology_v6 import (
    POLICY_SHA256,
    RetopologyV6ResourceError,
    assert_no_forbidden_generator_scripts,
    assert_structured_retopology_plan,
    verify_runtime_resources,
)

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "resources" / "retopology-v6"


def test_v6_contract_has_no_user_face_budget() -> None:
    parsed, warnings = adapt_retopology_v6_metadata_json(
        json.dumps(
            {
                "api_version": "6.0",
                "external_asset_id": "li3d:v6:001",
                "options": {
                    "algorithm": "agent",
                    "budget_mode": "automatic",
                    "topology_style": "mixed_game_ready",
                    "preserve_source": True,
                },
            }
        )
    )

    assert warnings == []
    assert parsed.options.model_dump(mode="json") == {
        "algorithm": "agent",
        "budget_mode": "automatic",
        "topology_style": "mixed_game_ready",
        "preserve_source": True,
        "preserve_sharp_edges": True,
        "preserve_boundaries": True,
        "uv_algorithm": "legacy_pbr",
        "delivery_profile": "next_gen_game_prop",
    }


def test_v6_runtime_resources_match_frozen_manifest() -> None:
    verified = verify_runtime_resources(RESOURCE_ROOT)

    assert verified["config/retopology-policy-v6.json"] == POLICY_SHA256
    assert "skill/blender-retopology-compare-iterate/scripts/audit_topology_flow.py" in verified


def test_v6_accepts_controlled_reduction_and_rejects_remesh(tmp_path: Path) -> None:
    direct_plan = {
        "method": "controlled_direct_reduction",
        "component_decisions": [{"component_id": "body", "method": "controlled_direct_reduction"}],
    }
    assert_structured_retopology_plan(direct_plan)

    (tmp_path / "build_low.py").write_text(
        "modifier = obj.modifiers.new('reduce', 'DECIMATE')\n", "utf-8"
    )
    assert_no_forbidden_generator_scripts(tmp_path)
    (tmp_path / "bad_remesh.py").write_text(
        "modifier = obj.modifiers.new('replace', 'REMESH')\n", "utf-8"
    )
    with pytest.raises(RetopologyV6ResourceError, match="REMESH_FORBIDDEN"):
        assert_no_forbidden_generator_scripts(tmp_path)


def test_v6_accepts_structured_reconstruction_plan_and_script(tmp_path: Path) -> None:
    assert_structured_retopology_plan(
        {
            "method": "semantic_reconstruction",
            "component_decisions": [{"component_id": "body", "method": "semantic_reconstruction"}],
        }
    )
    (tmp_path / "build_low.py").write_text("# deliberate cage and patch reconstruction\n", "utf-8")
    assert_no_forbidden_generator_scripts(tmp_path)


def test_v6_delivery_merge_script_is_pinned_and_preserves_disconnected_islands() -> None:
    worker_source = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        "utf-8"
    )
    merge_path = Path("packages/asset_processing/blender_retopology_merge.py")
    merge_source = merge_path.read_text("utf-8")
    merge_sha256 = hashlib.sha256(merge_path.read_bytes()).hexdigest()

    assert merge_sha256 in worker_source
    assert 'MERGE_MODE = "single_object_disconnected_islands"' in merge_source
    assert "bpy.ops.object.join()" in merge_source
    assert "bpy.ops.mesh.remove_doubles" not in merge_source
    assert "bpy.ops.object.modifier_apply" not in merge_source
    assert 'object_types={"MESH"}' in merge_source


def test_v6_formal_build_has_bounded_iteration_and_realistic_eta() -> None:
    worker_source = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        "utf-8"
    )

    assert "one authoritative build, one render/audit pass" in worker_source
    assert "estimated_stage_seconds=360" in worker_source
    assert '"RETOPOLOGY_V6_MERGE_EXPORT"' in worker_source
    assert "estimated_stage_seconds=180" in worker_source


def test_v5_target_and_bootstrap_selectors_are_ignored_not_translated() -> None:
    parsed, warnings = adapt_retopology_v6_metadata_json(
        json.dumps(
            {
                "external_asset_id": "li3d:v5:compat",
                "options": {
                    "algorithm": "cleanup_existing",
                    "topology_style": "quad_dominant",
                    "target_faces": 50,
                    "high_object": "high",
                    "reference_object": "reference_low",
                    "low_object": "current_low",
                    "generated_low_object": "candidate_v001",
                    "bootstrap_mode": "decimate",
                    "preserve_sharp": False,
                    "preserve_boundary": True,
                },
            }
        )
    )

    canonical = parsed.options.model_dump(mode="json")
    assert canonical["algorithm"] == "agent"
    assert canonical["budget_mode"] == "automatic"
    assert canonical["topology_style"] == "mixed_game_ready"
    assert canonical["preserve_sharp_edges"] is False
    assert "target_faces" not in canonical
    assert "reference_object" not in canonical
    assert "low_object" not in canonical
    assert "DEPRECATED_TARGET_FACES_IGNORED" in warnings
    assert "DEPRECATED_RETOPOLOGY_FIELDS_IGNORED" in warnings
    assert "DEPRECATED_RETOPOLOGY_ALGORITHM_IGNORED" in warnings


def test_unknown_retopology_option_still_fails_closed() -> None:
    with pytest.raises(ValidationError):
        adapt_retopology_v6_metadata_json(
            json.dumps(
                {
                    "api_version": "6.0",
                    "external_asset_id": "li3d:v6:bad",
                    "options": {"arbitrary_shell_command": "touch /tmp/no"},
                }
            )
        )


def test_v6_idempotency_binds_policy_and_canonical_options() -> None:
    parsed, _ = adapt_retopology_v6_metadata_json(
        json.dumps(
            {
                "api_version": "6.0",
                "external_asset_id": "li3d:v6:hash",
                "options": {},
            }
        )
    )
    digest = retopology_v6_process_request_hash(parsed, "a" * 64, {})

    assert len(digest) == 64
    assert RETOPOLOGY_V6_POLICY_SHA256 == (
        "d7e3b0be13a7a9daf5f9452b6429edc5b161a16ce3b43871864680dc7333eef0"
    )
