import importlib.util
import json
from copy import deepcopy
from pathlib import Path

from packages.gpu_control_core.assets import (
    _retopology_v3_topology_evidence_valid,
    retopology_auto_align_v3_evidence_failures,
    retopology_auto_align_v3_evidence_valid,
)

GUARD_PATH = Path(
    "resources/retopology-direct-v2/blender-auto-retopo-align/scripts/guard_shape_authority_plan.py"
)


def load_guard_module():
    specification = importlib.util.spec_from_file_location("retopology_v302_guard", GUARD_PATH)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def direct_reduction_plan(manifest_path: Path, *, use_normalized_work: bool = False) -> dict:
    plan = {
        "output_behavior": "save_and_stop",
        "user_inspects_result": True,
        "automatic_post_generation_actions": [],
        "source_identity": {
            "blend_filepath": "/job/source.blend",
            "original_source_filepath": "/job/source.fbx",
            "original_source_format": "fbx",
            "source_manifest_filepath": str(manifest_path),
            "object_name": "SOURCE_HIGH",
            "mesh_data_name": "SOURCE_HIGH_MESH",
            "measurement_space": "high_local",
            "matrix_world": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        },
        "method_decision": "controlled_direct_reduction",
        "shape_authority": {
            "authority": "high_poly_only",
            "global_registration_inputs": ["matrix_world", "coarse_bounds"],
            "local_profile_sections": [
                {
                    "section_id": "body",
                    "coordinate_space": "high_local",
                    "source": "high_measurement",
                    "controlling_views": ["front", "side", "top"],
                }
            ],
            "feature_controls": [],
            "openings": [],
            "component_evidence": [
                {"component_id": "body", "evidence": "integrated irregular body"}
            ],
            "surface_correspondence_method": "bounded_surface_projection",
            "template_constants": [],
            "uses_only_global_bounds": False,
            "fixed_geometry_proportions_from_template": False,
        },
        "component_decisions": [
            {
                "component_id": "body",
                "decision": "continuous",
                "evidence_id": "body",
            }
        ],
        "count_evidence_policy": {
            "fixed_face_count_is_shape_evidence": False,
            "fixed_component_count_is_shape_evidence": False,
            "budget_or_count_can_satisfy_shape_gate": False,
        },
        "direct_reduction_evidence": {
            "structurally_complex": True,
            "integrated_continuous_object": False,
            "fresh_high_duplicate": True,
            "structural_subregions_checked": True,
            "structured_shell_or_assembly_absent": False,
            "joined_source_state_used_as_integration_evidence": False,
            "exceptionally_complex_asset": True,
            "semantic_or_hybrid_would_lose_identity": True,
            "direct_reduction_reason": "complex scanned assembly",
            "uses_normalized_work_source": use_normalized_work,
        },
    }
    if use_normalized_work:
        plan["source_identity"]["normalized_work_object"] = "SOURCE_HIGH_NORMALIZED_WORK"
    return plan


def write_manifest(
    tmp_path: Path,
    *,
    components: int,
    duplicate_ratio: float,
    normalized_work: bool = False,
) -> Path:
    path = tmp_path / "source-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "li3d-retopology-fbx-source-v3",
                "source_topology": {
                    "face_components": components,
                    "duplicate_vertex_ratio": duplicate_ratio,
                },
                **(
                    {
                        "normalized_work_source": {
                            "created": True,
                            "qualified": True,
                            "object_name": "SOURCE_HIGH_NORMALIZED_WORK",
                            "method": "exact_position_weld_on_work_copy",
                            "source_high_unchanged": True,
                            "polygon_count_preserved": True,
                            "world_bounds_preserved": True,
                            "topology": {
                                "finite_coordinates": True,
                                "face_components": 1,
                                "boundary_edges": 0,
                                "multi_face_nonmanifold_edges": 0,
                                "loose_edges": 0,
                                "loose_vertices": 0,
                                "duplicate_vertices": 0,
                                "zero_area_faces": 0,
                                "inconsistent_orientation_edges": 0,
                            },
                        }
                    }
                    if normalized_work
                    else {}
                ),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_fragmented_triangle_soup_cannot_use_whole_object_decimate(
    tmp_path: Path,
) -> None:
    module = load_guard_module()
    manifest = write_manifest(tmp_path, components=38_697, duplicate_ratio=0.5159)

    errors, warnings = module.guard(direct_reduction_plan(manifest))

    assert warnings == []
    assert any("fragmented source topology" in error for error in errors)
    assert any("triangle-soup source topology" in error for error in errors)


def test_clean_integrated_source_remains_eligible_for_direct_reduction(
    tmp_path: Path,
) -> None:
    module = load_guard_module()
    manifest = write_manifest(tmp_path, components=1, duplicate_ratio=0.0)

    errors, warnings = module.guard(direct_reduction_plan(manifest))

    assert warnings == []
    assert errors == []


def test_exact_weld_work_copy_allows_safe_fragmented_source_reduction(
    tmp_path: Path,
) -> None:
    module = load_guard_module()
    manifest = write_manifest(
        tmp_path,
        components=38_697,
        duplicate_ratio=0.5159,
        normalized_work=True,
    )

    errors, warnings = module.guard(direct_reduction_plan(manifest, use_normalized_work=True))

    assert warnings == []
    assert errors == []


def topology_evidence() -> dict:
    def stage(*, unique_positions: bool) -> dict:
        return {
            "passed": True,
            "require_unique_vertex_positions": unique_positions,
            "pairs": [
                {
                    "high": {"faces": 200},
                    "low": {
                        "faces": 100,
                        "finite_coordinates": True,
                        "uv_layers": 1,
                        "boundary_edges": 0,
                        "multi_face_nonmanifold_edges": 0,
                        "loose_edges": 0,
                        "loose_vertices": 0,
                        "duplicate_vertices": 0,
                        "duplicate_faces": 0,
                        "degenerate_faces": 0,
                        "inconsistent_orientation_edges": 0,
                    },
                    "failures": [],
                }
            ],
            "failures": [],
        }

    return {
        "schema": "li3d-retopology-topology-v1",
        "passed": True,
        "generated_blend": stage(unique_positions=True),
        "blend_readback": stage(unique_positions=True),
        "fbx_readback": stage(unique_positions=False),
    }


def test_asset_api_rejects_any_open_boundary_in_delivery_evidence() -> None:
    evidence = topology_evidence()
    assert _retopology_v3_topology_evidence_valid(evidence) is True

    invalid = deepcopy(evidence)
    invalid["fbx_readback"]["pairs"][0]["low"]["boundary_edges"] = 1

    assert _retopology_v3_topology_evidence_valid(invalid) is False


def alignment_evidence() -> dict:
    structure = {
        "object_count": 1,
        "meshes": [
            {
                "vertices": 80,
                "polygons": 100,
                "loops": 300,
                "polygon_sizes": [3] * 100,
                "uv_layer_count": 1,
                "material_slot_count": 1,
            }
        ],
    }
    return {
        "schema": "li3d-auto-retopo-align-v1",
        "pass": True,
        "transform_only_alignment": True,
        "alignment_mode": "source_matrix_restore",
        "coordinate_authority": "high",
        "icp_used": False,
        "topology_or_uv_edited": False,
        "low_display": "opaque_yellow",
        "topology_uv_unchanged": True,
        "topology_validation": topology_evidence(),
        "pairs": [
            {
                "matrix_error_after": 0.0,
                "center_error_ratio": 0.0,
                "size_error_ratio": 0.05,
                "high_determinant_sign": 1,
                "low_determinant_sign_after": 1,
                "delivered_high_name": "ALIGN_HIGH_000",
                "delivered_low_name": "ALIGN_LOW_000",
            }
        ],
        "fbx_readback": {
            "pass": True,
            "high_center_size_error_ratio": 0.0,
            "low_center_size_error_ratio": 0.0,
            "tolerance": 1e-5,
            "low_structure_match": True,
            "expected_low_structure": structure,
            "actual_low_structure": structure,
        },
    }


def test_alignment_evidence_reports_no_failures_for_valid_contract() -> None:
    evidence = alignment_evidence()

    assert retopology_auto_align_v3_evidence_failures(evidence) == []
    assert retopology_auto_align_v3_evidence_valid(evidence) is True


def test_alignment_evidence_reports_every_actionable_failure() -> None:
    evidence = alignment_evidence()
    evidence["pairs"][0]["center_error_ratio"] = 0.02
    evidence["fbx_readback"]["low_center_size_error_ratio"] = 0.03
    evidence["topology_validation"]["fbx_readback"]["pairs"][0]["low"]["boundary_edges"] = 1

    failures = retopology_auto_align_v3_evidence_failures(evidence)

    assert "PAIR_0_CENTER_ERROR" in failures
    assert "FBX_LOW_BOUNDS_MISMATCH" in failures
    assert "TOPOLOGY_VALIDATION_INVALID" in failures
    assert "TOPOLOGY_FBX_READBACK_PAIR_0_BOUNDARY_EDGES" in failures
    assert retopology_auto_align_v3_evidence_valid(evidence) is False


def test_alignment_evidence_identifies_missing_low_uv_at_each_stage() -> None:
    evidence = alignment_evidence()
    for stage in ("generated_blend", "blend_readback", "fbx_readback"):
        evidence["topology_validation"][stage]["pairs"][0]["low"]["uv_layers"] = 0

    failures = retopology_auto_align_v3_evidence_failures(evidence)

    assert "TOPOLOGY_GENERATED_BLEND_PAIR_0_LOW_UV_MISSING" in failures
    assert "TOPOLOGY_BLEND_READBACK_PAIR_0_LOW_UV_MISSING" in failures
    assert "TOPOLOGY_FBX_READBACK_PAIR_0_LOW_UV_MISSING" in failures


def generated_low_delivery_evidence() -> dict:
    evidence = alignment_evidence()
    topology = evidence["topology_validation"]
    topology.pop("fbx_readback")
    topology["gate"] = "no_broken_faces"
    topology["uv_policy"] = "preserve_optional"
    for stage_name in ("generated_blend", "blend_readback"):
        topology[stage_name]["require_unique_vertex_positions"] = False
        low = topology[stage_name]["pairs"][0]["low"]
        low["uv_layers"] = 0
        # These defects are diagnostic under the generated-low delivery policy.
        low["boundary_edges"] = 3
        low["multi_face_nonmanifold_edges"] = 2
        low["loose_edges"] = 1
        low["loose_vertices"] = 1
        low["duplicate_vertices"] = 2
        low["duplicate_faces"] = 1
        low["inconsistent_orientation_edges"] = 4
    evidence["uv_policy"] = "preserve_optional"
    evidence["fbx_readback"] = {
        "performed": False,
        "status": "skipped_by_user_policy",
    }
    evidence["direction_review"] = {
        "performed": False,
        "status": "skipped_by_user_policy",
    }
    return evidence


def test_generated_low_policy_accepts_uv0_and_advisory_topology_defects() -> None:
    evidence = generated_low_delivery_evidence()

    assert (
        retopology_auto_align_v3_evidence_failures(
            evidence,
            require_fbx_readback=False,
            require_uv=False,
            strict_geometry=False,
        )
        == []
    )
    assert retopology_auto_align_v3_evidence_valid(
        evidence,
        require_fbx_readback=False,
        require_uv=False,
        strict_geometry=False,
    )


def test_generated_low_policy_preserves_optional_uv_and_rejects_degenerate_faces() -> None:
    evidence = generated_low_delivery_evidence()
    evidence["topology_validation"]["generated_blend"]["pairs"][0]["low"][
        "uv_layers"
    ] = 1
    evidence["topology_validation"]["blend_readback"]["pairs"][0]["low"][
        "uv_layers"
    ] = 1
    evidence["topology_validation"]["blend_readback"]["pairs"][0]["low"][
        "degenerate_faces"
    ] = 1

    failures = retopology_auto_align_v3_evidence_failures(
        evidence,
        require_fbx_readback=False,
        require_uv=False,
        strict_geometry=False,
    )

    assert "TOPOLOGY_GENERATED_BLEND_PAIR_0_LOW_UV_COUNT_INVALID" not in failures
    assert "TOPOLOGY_BLEND_READBACK_PAIR_0_DEGENERATE_FACES" in failures
