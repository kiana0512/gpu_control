import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from gpu_control_blender_worker.main import has_raw_blend_signature

SCRIPT = Path("packages/asset_processing/import_retopology_source.py")


def test_retopology_source_import_writes_direct_v2_compatible_blend() -> None:
    source = SCRIPT.read_text("utf-8")

    compile(source, str(SCRIPT), "exec")
    assert 'BLEND_SIGNATURE = b"BLENDER"' in source
    assert "bpy.ops.wm.read_factory_settings(use_empty=True)" in source
    assert "compress=False" in source
    assert 'elif suffix == ".blend":' in source
    assert "use_scripts=False" in source
    assert "signature != BLEND_SIGNATURE" in source
    assert "source normalization did not create an uncompressed Blend" in source


def test_glb_normalization_empties_scene_and_disables_compression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    source = tmp_path / "source.glb"
    output = tmp_path / "normalized.blend"
    source.write_bytes(b"glTF-test")

    def record(name: str):
        def operation(**kwargs: object) -> None:
            calls.append((name, kwargs))
            if name == "save":
                output.write_bytes(b"BLENDER-v510-test")

        return operation

    fake_bpy = SimpleNamespace(
        ops=SimpleNamespace(
            wm=SimpleNamespace(
                read_factory_settings=record("reset"),
                save_as_mainfile=record("save"),
            ),
            import_scene=SimpleNamespace(gltf=record("gltf"), fbx=record("fbx")),
        ),
        context=SimpleNamespace(scene=SimpleNamespace(objects=[SimpleNamespace(type="MESH")])),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--", "--input", str(source), "--output", str(output)],
    )

    runpy.run_path(str(SCRIPT), run_name="__main__")

    assert calls == [
        ("reset", {"use_empty": True}),
        ("gltf", {"filepath": str(source)}),
        (
            "save",
            {
                "filepath": str(output),
                "check_existing": False,
                "compress": False,
            },
        ),
    ]


def test_raw_blend_signature_check_rejects_compressed_or_truncated_files(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw.blend"
    compressed = tmp_path / "compressed.blend"
    truncated = tmp_path / "truncated.blend"
    raw.write_bytes(b"BLENDER-v510-test")
    compressed.write_bytes(bytes.fromhex("28b52ffd") + b"compressed")
    truncated.write_bytes(b"BLEND")

    assert has_raw_blend_signature(raw) is True
    assert has_raw_blend_signature(compressed) is False
    assert has_raw_blend_signature(truncated) is False
