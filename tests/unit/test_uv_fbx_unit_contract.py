import hashlib
from pathlib import Path

from gpu_control_blender_worker.main import UV_FBX_UNITS_SCRIPT_SHA256

SCRIPT = Path("packages/asset_processing/blender_uv_fbx_units.py")


def test_uv_fbx_unit_adapter_is_release_pinned() -> None:
    assert hashlib.sha256(SCRIPT.read_bytes()).hexdigest() == UV_FBX_UNITS_SCRIPT_SHA256


def test_uv_fbx_unit_adapter_uses_browser_safe_meter_export_and_readback() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'apply_scale_options="FBX_SCALE_UNITS"' in source
    assert 'scene.unit_settings.length_unit = "METERS"' in source
    assert 'fbx_double_property(output_fbx, "UnitScaleFactor")' in source
    assert 'fbx_double_property(output_fbx, "OriginalUnitScaleFactor")' in source
    assert "bpy.ops.wm.read_factory_settings(use_empty=True)" in source
    assert 'bpy.ops.wm.fbx_import(filepath=str(output_fbx))' in source
    assert '"dimensions_max_abs_delta"' in source
    assert '"expected_structure"' in source
    assert '"readback_structure"' in source


def test_uv_worker_runs_unit_adapter_before_uv_qa() -> None:
    worker = Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text(encoding="utf-8")

    normalization = worker.index('"UV_FBX_UNIT_NORMALIZATION"')
    blend_qa = worker.index('("blend", output_blend, blend_qa_path, 66.0, 78.0)')
    assert normalization < blend_qa
    assert 'unwrap_report["fbx_unit_contract"] = unit_report' in worker
