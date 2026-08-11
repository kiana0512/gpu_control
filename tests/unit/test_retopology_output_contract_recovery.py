from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path("resources/retopology-direct-v2/server/one_click_retopology.py")
SPEC = importlib.util.spec_from_file_location("one_click_retopology", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_blend(path: Path, payload: bytes = b"test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"BLENDER" + payload)


def write_report(job: Path, output_blend: Path) -> None:
    (job / "generation_report.json").write_text(
        json.dumps(
            {
                "status": "generated_for_user_inspection",
                "output_blend": str(output_blend),
                "assets": [{"high_object": "HIGH", "low_object": "LOW"}],
            }
        ),
        encoding="utf-8",
    )


def test_recovers_one_declared_legacy_artifact_without_modifying_it(
    tmp_path: Path,
) -> None:
    job = tmp_path / "job"
    artifacts = job / "artifacts"
    legacy = artifacts / "result.blend"
    expected = artifacts / "generated.blend"
    write_blend(legacy, b"legacy-payload")
    write_report(job, legacy)

    assert MODULE.recover_declared_output_blend(job, expected) == "artifacts/result.blend"
    assert expected.read_bytes() == legacy.read_bytes()
    recovery = json.loads((job / "output_contract_recovery.json").read_text())
    assert recovery["geometry_modified"] is False
    assert recovery["recovered_from"] == "artifacts/result.blend"


def test_recovers_a_job_relative_declared_output(tmp_path: Path) -> None:
    job = tmp_path / "job"
    legacy = job / "artifacts" / "renamed.blend"
    expected = job / "artifacts" / "generated.blend"
    write_blend(legacy, b"relative-payload")
    write_report(job, Path("artifacts/renamed.blend"))

    assert MODULE.recover_declared_output_blend(job, expected) == "artifacts/renamed.blend"
    assert expected.read_bytes() == legacy.read_bytes()


def test_recovery_rejects_source_blend_and_ambiguous_artifacts(tmp_path: Path) -> None:
    job = tmp_path / "job"
    source = job / "work" / "source.blend"
    expected = job / "artifacts" / "generated.blend"
    write_blend(source)
    write_report(job, source)

    assert MODULE.recover_declared_output_blend(job, expected) is None
    assert not expected.exists()

    first = job / "artifacts" / "result.blend"
    second = job / "artifacts" / "other.blend"
    write_blend(first, b"first")
    write_blend(second, b"second")
    write_report(job, first)
    assert MODULE.recover_declared_output_blend(job, expected) is None
    assert not expected.exists()


def test_failure_diagnostic_classifies_auth_without_returning_secret_text(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent_events.jsonl").write_text(
        json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": "401 Unauthorized token_expired secret-do-not-copy"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "agent_stderr.log").write_text("refresh token was revoked\n")

    diagnostic = MODULE.codex_failure_diagnostic(tmp_path)

    assert diagnostic["error_category"] == "CODEX_AUTH_EXPIRED"
    assert diagnostic["last_event_type"] == "turn.failed"
    assert "secret-do-not-copy" not in json.dumps(diagnostic)


def test_failure_diagnostic_identifies_an_unexecuted_build_script(tmp_path: Path) -> None:
    (tmp_path / "agent_events.jsonl").write_text(
        json.dumps({"type": "turn.completed"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "agent_stderr.log").write_text("", encoding="utf-8")
    (tmp_path / "build_once.py").write_text("# generated but not executed\n", encoding="utf-8")

    diagnostic = MODULE.codex_failure_diagnostic(tmp_path)

    assert diagnostic["error_category"] == "BUILD_SCRIPT_NOT_EXECUTED"


def test_one_click_prepares_every_supported_static_source_as_source_high() -> None:
    assert {".fbx", ".glb", ".gltf", ".obj"}.issubset(MODULE.SUPPORTED_INPUTS)
    source = Path(
        "resources/retopology-direct-v2/blender-auto-retopo-align/scripts/prepare_fbx_source.py"
    ).read_text(encoding="utf-8")
    assert 'SUPPORTED_SOURCE_EXTENSIONS = {".fbx", ".glb", ".gltf", ".obj"}' in source
    assert 'bpy.ops.import_scene.gltf(filepath=input_path)' in source
    assert 'bpy.ops.wm.obj_import(filepath=input_path)' in source
    assert '"schema": "li3d-retopology-static-source-v4"' in source


def test_worker_does_not_flatten_glb_to_an_unqualified_direct_blend() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    assert 'normalization_required = project_suffix == ".blend"' in worker
    assert "project_suffix not in" not in worker
