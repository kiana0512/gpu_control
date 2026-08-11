from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path("resources/retopology-direct-v2/server/one_click_retopology.py")
SPEC = importlib.util.spec_from_file_location("one_click_retopology", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_second_attempt_changes_generation_method_without_relaxing_gates() -> None:
    first = MODULE.attempt_guidance(1)
    second = MODULE.attempt_guidance(2)

    assert "首次生成" in first
    assert "controlled_direct_reduction" in second
    assert "per_component_hybrid" in second
    assert "禁止原样重复低密度语义代理" in second
    assert "SOURCE_HIGH" in second


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


def test_completes_one_unexecuted_generated_build_script_once(
    tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "build_once.py"
    script.write_text("# generated build\n", encoding="utf-8")
    working_blend = tmp_path / "work" / "source.blend"
    write_blend(working_blend, b"source-high")
    output = tmp_path / "artifacts" / "generated.blend"
    observed: dict[str, object] = {}

    def fake_run_logged(command, cwd, stdout_path, stderr_path, timeout):
        observed["command"] = command
        observed["cwd"] = cwd
        observed["timeout"] = timeout
        write_blend(output, b"server-completed")
        (tmp_path / "generation_report.json").write_text("{}", encoding="utf-8")
        return 0, False

    monkeypatch.setattr(MODULE, "run_logged", fake_run_logged)

    evidence = MODULE.complete_generated_build_script(
        "/opt/blender/blender", tmp_path, working_blend, output, 321
    )

    assert evidence is not None
    assert evidence["script"] == "build_once.py"
    assert evidence["executed_once"] is True
    assert evidence["output_valid"] is True
    assert evidence["generation_report_exists"] is True
    assert observed["cwd"] == tmp_path
    assert observed["timeout"] == 321
    assert str(working_blend) in observed["command"]
    assert evidence["working_blend_sha256"] == MODULE.sha256(working_blend)
    assert observed["command"][-2:] == ["--python", str(script)]
    assert (tmp_path / "build_script_execution.json").is_file()


def test_generated_build_completion_rejects_ambiguous_or_symlinked_scripts(
    tmp_path: Path,
) -> None:
    first = tmp_path / "build_once.py"
    second = tmp_path / "build.py"
    first.write_text("# one\n", encoding="utf-8")
    second.write_text("# two\n", encoding="utf-8")
    assert MODULE.generated_build_script(tmp_path) is None

    second.unlink()
    first.unlink()
    first.symlink_to(tmp_path / "outside.py")
    assert MODULE.generated_build_script(tmp_path) is None


def test_generation_report_allows_blender_to_author_uv_metrics(tmp_path: Path) -> None:
    report = tmp_path / "generation_report.json"
    report.write_text(
        json.dumps(
            {
                "status": "generated_for_user_inspection",
                "assets": [
                    {
                        "high_object": "SOURCE_HIGH",
                        "low_object": "SOURCE_HIGH_LOW",
                        "faces": 10,
                        "triangles": 20,
                        "method_decision": "semantic_reconstruction",
                        "actual_plugin_use": [],
                        "coordinate_space": "source_high_local",
                        "coordinate_authority": "high_object_matrix_world",
                        "presentation_offset_applied": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    parsed = MODULE.validate_generation_report(report, ["SOURCE_HIGH"])
    assert "uv_layers" not in parsed["assets"][0]

    reconciled = MODULE.reconcile_generation_report_mesh_metrics(
        parsed,
        {
            "topology_validation": {
                "generated_blend": {
                    "pairs": [
                        {
                            "low": {
                                "faces": 12,
                                "triangles": 24,
                                "uv_layers": 1,
                            }
                        }
                    ]
                }
            }
        },
        report,
    )
    assert reconciled["assets"][0]["uv_layers"] == 1
    assert reconciled["assets"][0]["faces"] == 12
    assert reconciled["assets"][0]["mesh_metrics_authority"].startswith("blender_")


def test_verified_blend_without_uv_still_fails_closed(tmp_path: Path) -> None:
    report = {
        "assets": [
            {
                "high_object": "SOURCE_HIGH",
                "low_object": "SOURCE_HIGH_LOW",
            }
        ]
    }

    try:
        MODULE.reconcile_generation_report_mesh_metrics(
            report,
            {
                "topology_validation": {
                    "generated_blend": {
                        "pairs": [
                            {
                                "low": {
                                    "faces": 12,
                                    "triangles": 24,
                                    "uv_layers": 0,
                                }
                            }
                        ]
                    }
                }
            },
            tmp_path / "generation_report.json",
        )
    except RuntimeError as error:
        assert "RETOPOLOGY_TOPOLOGY_INVALID" in str(error)
    else:
        raise AssertionError("Blender-verified missing UV must fail")


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
