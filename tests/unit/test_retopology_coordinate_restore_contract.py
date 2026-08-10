import hashlib
from pathlib import Path

ROOT = Path("packages/asset_processing")
SCRIPT = ROOT / "blender_retopology_restore_coordinates.py"
EXPECTED_SHA256 = "e4e01e98386484af9c61a3f96d46034820d5a48f7cca1d80d947435f2b12f8b7"


def test_coordinate_restore_is_transform_only_and_fbx_readback_gated() -> None:
    source = SCRIPT.read_text("utf-8")
    compile(source, str(SCRIPT), "exec")

    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == EXPECTED_SHA256
    assert "matrix_world[row][column] = high.matrix_world[row][column]" in source
    assert "matrix_world.translation = matrix_world.translation + delta" in source
    assert "if linear_transform_required:" in source
    assert "if translation_required:" in source
    assert 'coordinate_action = "full_transform_restored"' in source
    assert "if blend_transform_changed:" in source
    assert '"low_rotation_scale_restored": linear_transform_required' in source
    assert "bpy.ops.export_scene.fbx(" in source
    assert "bpy.ops.import_scene.fbx(filepath=str(output_fbx))" in source
    assert "FBX readback changed aligned bounds" in source
    assert 'MODE = "high_world_linear_aabb_center_and_fbx_meter"' in source
    assert 'apply_scale_options="FBX_SCALE_UNITS"' in source
    assert "scene.unit_settings.scale_length = 1.0" in source
    assert 'fbx_double_property(output_fbx, "UnitScaleFactor")' in source
    assert '"raw_coordinates_are_meters": True' in source
    assert "MAXIMUM_DIMENSION_RELATIVE_ERROR = 0.05" in source
    assert "generated low dimensions do not match the source high" in source
    assert "refusing to scale or distort generated topology" in source


def test_direct_v2_delivery_requires_coordinate_restore_evidence() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text("utf-8")
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text("utf-8")

    assert f'"{EXPECTED_SHA256}"' in worker
    assert '"schema_version": "retopology_direct_delivery.v5"' in worker
    assert '"coordinate_restoration": coordinate_report' in worker
    assert 'manifest.get("schema_version") != "retopology_direct_delivery.v5"' in api
    assert '!= "high_world_linear_aabb_center_and_fbx_meter"' in api
    assert 'fbx_readback.get("passed") is not True' in api
    assert "retopology_fbx_meter_evidence_valid" in worker
    assert "retopology_fbx_meter_evidence_valid" in api


def test_removed_pre_bake_alignment_has_no_runtime_or_frontend_surface() -> None:
    targets = (
        Path("apps/asset_api/src/gpu_control_asset_api/main.py"),
        Path("apps/blender_worker/src/gpu_control_blender_worker/main.py"),
        Path("apps/blender_worker/Dockerfile"),
        Path("apps/web/src/assetPresentation.ts"),
        Path("apps/web/src/views/Assets.vue"),
    )
    combined = "\n".join(path.read_text("utf-8") for path in targets)

    assert "BAKE_ALIGNMENT" not in combined
    assert "bake-alignment" not in combined
    assert "alignment_manifest" not in combined
    assert not Path("resources/bake-coordinate-alignment").exists()
