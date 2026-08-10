import hashlib
from pathlib import Path

ROOT = Path("packages/asset_processing")
SCRIPT = ROOT / "blender_retopology_bake_postprocess.py"
EXPECTED_SHA256 = "d555d30824f7b822699543efe443de1395c8107428ff1e785770610f0f2f3b01"


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
    assert 'manifest.get("schema_version") != "retopology_direct_delivery.v6"' in api
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
