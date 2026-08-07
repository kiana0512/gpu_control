import hashlib
from pathlib import Path

ROOT = Path("packages/asset_processing")
SCRIPT = ROOT / "blender_retopology_restore_coordinates.py"
EXPECTED_SHA256 = "a1dadcd72318b1475377cc02f3e70876d8cc3ad350ebe86b17d7ed72b10568c5"


def test_coordinate_restore_is_translation_only_and_fbx_readback_gated() -> None:
    source = SCRIPT.read_text("utf-8")
    compile(source, str(SCRIPT), "exec")

    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == EXPECTED_SHA256
    assert "matrix_world.translation = matrix_world.translation + delta" in source
    assert '"low_rotation_scale_preserved": True' in source
    assert 'bpy.ops.export_scene.fbx(' in source
    assert 'bpy.ops.import_scene.fbx(filepath=str(output_fbx))' in source
    assert "FBX readback changed aligned bounds" in source
    assert 'MODE = "translation_only_world_aabb_center"' in source


def test_direct_v2_delivery_requires_coordinate_restore_evidence() -> None:
    worker = Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text("utf-8")
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text("utf-8")

    assert f'"{EXPECTED_SHA256}"' in worker
    assert '"schema_version": "retopology_direct_delivery.v3"' in worker
    assert '"coordinate_restoration": coordinate_report' in worker
    assert 'manifest.get("schema_version") != "retopology_direct_delivery.v3"' in api
    assert '!= "translation_only_world_aabb_center"' in api
    assert 'fbx_readback.get("passed") is not True' in api


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
