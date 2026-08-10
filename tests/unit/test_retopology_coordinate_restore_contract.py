from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

try:
    import numpy as np
except ModuleNotFoundError:
    np = None

ROOT = Path("packages/asset_processing")
SCRIPT = ROOT / "blender_retopology_bake_postprocess.py"
EXPECTED_SHA256 = "13af9c8fdf3716cbf1d94a6a22c54d190665a99427c8290e1aca00ef9ed85a83"


def load_postprocess_math(monkeypatch):
    fake_bpy = ModuleType("bpy")
    fake_bpy.types = SimpleNamespace()
    fake_mathutils = ModuleType("mathutils")
    fake_mathutils.Matrix = object
    fake_mathutils.Vector = object
    fake_bvhtree = ModuleType("mathutils.bvhtree")
    fake_bvhtree.BVHTree = object
    monkeypatch.setitem(sys.modules, "bmesh", ModuleType("bmesh"))
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)
    monkeypatch.setitem(sys.modules, "mathutils.bvhtree", fake_bvhtree)
    specification = importlib.util.spec_from_file_location("retopology_postprocess", SCRIPT)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class FakeAlignmentModule:
    def __init__(
        self,
        low_size: np.ndarray,
        *,
        surface_optimum: float = 1.0,
        surface_slope: float = 0.05,
    ) -> None:
        self.low_size = np.asarray(low_size, dtype=np.float64)
        self.surface_optimum = surface_optimum
        self.surface_slope = surface_slope

    def transformed_bounds(self, _objects, matrix):
        scale = float(np.cbrt(np.linalg.det(matrix[:3, :3])))
        return {
            "size": self.low_size * scale,
            "center": np.asarray(matrix[:3, 3], dtype=np.float64),
        }

    def evaluate_candidate(
        self,
        matrix,
        high,
        _low_objects,
        _high_score_points,
        _low_score_points,
        _high_tree,
        _trim_fraction,
    ):
        bounds = self.transformed_bounds([], matrix)
        scale = float(np.cbrt(np.linalg.det(matrix[:3, :3])))
        dimension_error = float(
            np.max(np.abs(bounds["size"] - high["size"])) / np.max(high["size"])
        )
        center_error = float(
            np.linalg.norm(bounds["center"] - high["center"]) / high["diagonal"]
        )
        surface_error = 0.059 + abs(scale - self.surface_optimum) * self.surface_slope
        return {
            "matrix": matrix,
            "score": surface_error + 0.1 * dimension_error,
            "surface_error_ratio": surface_error,
            "center_error_ratio": center_error,
            "dimension_error_ratio": dimension_error,
            "uniform_scale": scale,
            "reflected": False,
        }


def test_uniform_scale_refinement_resolves_dimension_only_failure(monkeypatch) -> None:
    if np is None:
        pytest.skip("Blender-bundled NumPy is not installed in the control-plane test runtime")
    postprocess = load_postprocess_math(monkeypatch)
    alignment = FakeAlignmentModule(np.array([8.632, 8.0, 6.0]))
    high = {
        "size": np.array([10.0, 8.0, 6.0]),
        "center": np.array([42.0, -2.0, 1.5]),
        "diagonal": math.sqrt(200.0),
    }

    selected, evidence = postprocess.refine_uniform_scale_for_dimension_gate(
        np.eye(4, dtype=np.float64),
        high,
        object(),
        alignment,
        np.zeros((16, 3), dtype=np.float64),
        np.zeros((16, 3), dtype=np.float64),
        object(),
        0.82,
    )

    assert selected is not None
    assert evidence["attempted"] is True
    assert evidence["gate_passing_candidate_count"] > 0
    assert selected["dimension_error_ratio"] <= 0.100
    assert selected["surface_error_ratio"] <= 0.070
    assert selected["center_error_ratio"] <= 0.020
    assert selected["reflected"] is False
    assert np.allclose(selected["matrix"][:3, 3], high["center"])
    diagonal = np.diag(selected["matrix"][:3, :3])
    assert np.allclose(diagonal, diagonal[0])


def test_uniform_scale_refinement_rejects_incompatible_dimensions(monkeypatch) -> None:
    if np is None:
        pytest.skip("Blender-bundled NumPy is not installed in the control-plane test runtime")
    postprocess = load_postprocess_math(monkeypatch)
    alignment = FakeAlignmentModule(np.array([5.0, 20.0, 6.0]))
    high = {
        "size": np.array([10.0, 8.0, 6.0]),
        "center": np.zeros(3),
        "diagonal": math.sqrt(200.0),
    }

    selected, evidence = postprocess.refine_uniform_scale_for_dimension_gate(
        np.eye(4, dtype=np.float64),
        high,
        object(),
        alignment,
        np.zeros((16, 3), dtype=np.float64),
        np.zeros((16, 3), dtype=np.float64),
        object(),
        0.82,
    )

    assert selected is None
    assert evidence == {
        "attempted": True,
        "method": "analytic_dimension_interval_plus_bounded_uniform_scale_search",
        "rotation_frozen": True,
        "center_recomputed_for_each_candidate": True,
        "uniform_scale_only": True,
        "axis_scale_used": False,
        "reflection_allowed": False,
        "dimension_error_limit": 0.100,
        "surface_error_limit": 0.070,
        "feasible_interval": [1.8, 0.45],
        "feasible": False,
        "reason": "no_uniform_scale_can_satisfy_dimension_gate",
        "candidate_count": 0,
        "gate_passing_candidate_count": 0,
    }


def test_uniform_scale_refinement_can_improve_surface_without_relaxing_gate(
    monkeypatch,
) -> None:
    if np is None:
        pytest.skip("Blender-bundled NumPy is not installed in the control-plane test runtime")
    postprocess = load_postprocess_math(monkeypatch)
    alignment = FakeAlignmentModule(
        np.array([9.0, 7.5, 5.5]),
        surface_optimum=1.05,
        surface_slope=0.28,
    )
    high = {
        "size": np.array([10.0, 8.0, 6.0]),
        "center": np.array([2.0, 3.0, 4.0]),
        "diagonal": math.sqrt(200.0),
    }
    baseline = alignment.evaluate_candidate(
        np.eye(4), high, [], None, None, None, 0.82
    )
    assert baseline["surface_error_ratio"] > 0.070

    selected, evidence = postprocess.refine_uniform_scale_for_dimension_gate(
        np.eye(4, dtype=np.float64),
        high,
        object(),
        alignment,
        np.zeros((16, 3), dtype=np.float64),
        np.zeros((16, 3), dtype=np.float64),
        object(),
        0.82,
    )

    assert selected is not None
    assert evidence["gate_passing_candidate_count"] > 0
    assert selected["surface_error_ratio"] <= 0.070
    assert selected["dimension_error_ratio"] <= 0.100


def test_post_topology_bake_alignment_is_fail_closed() -> None:
    source = SCRIPT.read_text("utf-8")
    compile(source, str(SCRIPT), "exec")

    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == EXPECTED_SHA256
    assert 'MODE = "transform_only_alignment_then_separate_uv"' in source
    assert '"method": "higher_face_count_is_high"' in source
    assert "module.solve(" in source
    assert "reflected=False" in source
    assert '"uniform_scale_only": True' in source
    assert '"axis_scale_used": False' in source
    assert '"mirror_allowed": False' in source
    assert '"copied_high_object_transform": False' in source
    assert '"applied_exactly_once_to_duplicate_mesh": True' in source
    assert '"original_high_preserved": True' in source
    assert '"original_low_preserved": True' in source
    assert '"topology_rebuild_allowed": False' in source
    assert '"alignment_changes_topology_or_uv": False' in source
    assert '"uv_is_a_separate_pre_alignment_stage": True' in source
    assert "rebuild_from_high" not in source
    assert "apply_volume_union" not in source
    assert "quadriflow_remesh" not in source
    assert "voxel" not in source.lower()
    assert "decimate" not in source.lower()
    assert "allow-axis-scale" not in source
    assert "allow-mirror" not in source
    assert "topology_uv_fingerprint" in source
    assert "fingerprint_after != fingerprint_before" in source
    assert "basic_uv_valid" in source
    assert "VIEW_NAMES =" in source
    assert '"opaque_bright_orange_solid_with_dark_wire"' in source
    assert '"low_transparency": False' in source
    assert '"xray": False' in source
    assert "bpy.ops.export_scene.fbx(" in source
    assert "bpy.ops.wm.fbx_import(filepath=str(path))" in source
    assert "FBX clean-scene readback changed bake pair bounds" in source
    assert 'apply_scale_options="FBX_SCALE_UNITS"' in source
    assert "scene.unit_settings.scale_length = 1.0" in source
    assert '"raw_coordinates_are_meters": True' in source
    assert '"fresh_blender_scene_reimport": True' in source
    assert '"low_faces_less_than_high"' in source
    assert '"low_has_uv"' in source
    assert '"low_structure_match"' in source


def test_direct_v2_delivery_requires_bake_alignment_evidence() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text("utf-8")
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text("utf-8")

    assert f'"{EXPECTED_SHA256}"' in worker
    assert '"schema_version": "retopology_direct_delivery.v6"' in worker
    assert '"bake_alignment": alignment_report' in worker
    assert '"bake_pair_validation": validation_report' in worker
    assert '"visual_qa": visual_qa' in worker
    assert '"uniqueItems"' not in worker
    assert 'manifest.get("schema_version") != "retopology_direct_delivery.v6"' in api
    assert 'staged_by_kind["blend"].filename = f"{stem}_GAME_LOW.blend"' in api
    assert 'staged_by_kind["fbx"].filename = f"{stem}_GAME_LOW.fbx"' in api
    assert "retopology_bake_alignment_evidence_valid" in worker
    assert "retopology_bake_alignment_evidence_valid" in api
    assert "retopology_bake_pair_validation_evidence_valid" in worker
    assert "retopology_bake_pair_validation_evidence_valid" in api
    assert "retopology_bake_visual_qa_evidence_valid" in worker
    assert "retopology_bake_visual_qa_evidence_valid" in api


def test_alignment_is_post_topology_and_has_no_frontend_mutation_surface() -> None:
    targets = (
        Path("apps/asset_api/src/gpu_control_asset_api/main.py"),
        Path("apps/blender_worker/src/gpu_control_blender_worker/main.py"),
        Path("apps/blender_worker/Dockerfile"),
        Path("apps/web/src/assetPresentation.ts"),
        Path("apps/web/src/views/Assets.vue"),
    )
    combined = "\n".join(path.read_text("utf-8") for path in targets)

    assert "alignment_manifest" not in combined
    assert not Path("resources/bake-coordinate-alignment").exists()
    assert "RETOPOLOGY_BAKE_POSTPROCESS" in combined
    assert "automatic_post_generation_review" in combined
