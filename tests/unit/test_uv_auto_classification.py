from packages.gpu_control_core.uv_auto_classification import (
    UV_AUTO_CLASSIFIER_VERSION,
    UVGeometryEvidence,
    classify_uv_geometry,
)


def evidence(**overrides: object) -> UVGeometryEvidence:
    values: dict[str, object] = {
        "mesh_object_count": 1,
        "face_count": 422,
        "face_component_count": 4,
        "vertex_count": 245,
        "edge_count": 665,
        "manifold_edge_count": 601,
        "boundary_edge_count": 64,
        "nonmanifold_edge_count": 0,
        "modifier_count": 0,
        "shape_key_count": 0,
        "smooth_face_ratio": 1.0,
        "authored_sharp_edge_ratio": 0.02,
        "near_planar_edge_ratio": 0.30,
        "curved_edge_ratio": 0.62,
        "steep_edge_ratio": 0.08,
        "very_steep_edge_ratio": 0.03,
    }
    values.update(overrides)
    return UVGeometryEvidence(**values)  # type: ignore[arg-type]


def test_complex_multi_component_soft_surface_routes_to_mof() -> None:
    result = classify_uv_geometry(evidence())

    assert result.classifier_version == UV_AUTO_CLASSIFIER_VERSION
    assert result.resolved_algorithm == "mof_low_seam"
    assert result.asset_profile == "complex_non_hardsurface"
    assert result.reason_codes == ("complex_multi_component_soft_surface",)


def test_polygon_count_alone_never_routes_hard_surface_to_mof() -> None:
    result = classify_uv_geometry(
        evidence(
            face_count=250_000,
            face_component_count=80,
            smooth_face_ratio=0.1,
            authored_sharp_edge_ratio=0.18,
            near_planar_edge_ratio=0.72,
            curved_edge_ratio=0.08,
            steep_edge_ratio=0.20,
            very_steep_edge_ratio=0.14,
        )
    )

    assert result.resolved_algorithm == "legacy_pbr"
    assert result.asset_profile == "general"
    assert "hard_surface_edge_pattern" in result.reason_codes


def test_complex_multi_mesh_routes_entire_asset_to_mof() -> None:
    result = classify_uv_geometry(
        evidence(
            mesh_object_count=2,
            face_count=7_744,
            face_component_count=456,
            manifold_edge_count=7_991,
            smooth_face_ratio=0.0,
            near_planar_edge_ratio=0.57,
            curved_edge_ratio=0.428,
            very_steep_edge_ratio=0.001,
        )
    )

    assert result.classifier_version == "uv-auto-classifier-v2"
    assert result.resolved_algorithm == "mof_low_seam"
    assert result.asset_profile == "complex_multi_mesh"
    assert result.reason_codes == ("complex_multi_mesh_asset",)


def test_small_multi_mesh_asset_stays_on_legacy() -> None:
    result = classify_uv_geometry(
        evidence(
            mesh_object_count=2,
            face_count=24,
            face_component_count=2,
            manifold_edge_count=12,
        )
    )

    assert result.resolved_algorithm == "legacy_pbr"
    assert "insufficient_multi_mesh_structural_evidence" in result.reason_codes


def test_multi_mesh_with_modifier_fails_closed_to_legacy() -> None:
    result = classify_uv_geometry(
        evidence(mesh_object_count=2, face_component_count=4, modifier_count=1)
    )

    assert result.resolved_algorithm == "legacy_pbr"
    assert "modifiers_require_legacy_preservation" in result.reason_codes


def test_single_connected_soft_mesh_stays_on_legacy() -> None:
    result = classify_uv_geometry(evidence(face_component_count=1))

    assert result.resolved_algorithm == "legacy_pbr"
    assert "requires_multiple_face_components" in result.reason_codes


def test_modifiers_and_shape_keys_fail_closed_to_legacy() -> None:
    result = classify_uv_geometry(evidence(modifier_count=1, shape_key_count=2))

    assert result.resolved_algorithm == "legacy_pbr"
    assert "modifiers_require_legacy_preservation" in result.reason_codes
    assert "shape_keys_require_legacy_preservation" in result.reason_codes


def test_invalid_ratio_is_rejected() -> None:
    try:
        classify_uv_geometry(evidence(curved_edge_ratio=1.1))
    except ValueError as exc:
        assert "ratios" in str(exc)
    else:
        raise AssertionError("invalid classifier evidence was accepted")
