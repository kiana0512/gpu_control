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
        "delivery_profile": "next_gen_game_prop",
    }


def test_v6_runtime_resources_match_frozen_manifest() -> None:
    verified = verify_runtime_resources(RESOURCE_ROOT)

    assert verified["config/retopology-policy-v6.json"] == POLICY_SHA256
    assert "skill/blender-retopology-compare-iterate/scripts/audit_topology_flow.py" in verified


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
        "e6781d6158a93e571c944f5913a600838fe28fc2edc38a3b1909f649f66f3d3d"
    )
