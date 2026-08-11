import importlib.util
import json
from copy import deepcopy
from pathlib import Path

from packages.gpu_control_core.assets import _retopology_v3_topology_evidence_valid

GUARD_PATH = Path(
    "resources/retopology-direct-v2/blender-auto-retopo-align/scripts/"
    "guard_shape_authority_plan.py"
)


def load_guard_module():
    specification = importlib.util.spec_from_file_location(
        "retopology_v302_guard", GUARD_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def direct_reduction_plan(
    manifest_path: Path, *, use_normalized_work: bool = False
) -> dict:
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
        plan["source_identity"]["normalized_work_object"] = (
            "SOURCE_HIGH_NORMALIZED_WORK"
        )
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

    errors, warnings = module.guard(
        direct_reduction_plan(manifest, use_normalized_work=True)
    )

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
