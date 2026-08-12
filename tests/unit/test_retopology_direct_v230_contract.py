import hashlib
import subprocess
import sys
from pathlib import Path

from packages.gpu_control_core.assets import (
    RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256,
    RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION,
    retopology_direct_v2_completion_identity_valid,
)

ROOT = Path("resources/retopology-direct-v2")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_approved_v313_package_is_complete() -> None:
    completed = subprocess.run(  # noqa: S603 - repository-owned verifier
        [sys.executable, str(ROOT / "server" / "verify_package.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (ROOT / "server" / "batch_retopology.py").is_file()
    assert (
        file_sha256(ROOT / "blender-auto-retopo-align" / "SKILL.md")
        == "30c20960842b91c1ebefff7a35a3bbc0b79191265e9a573cac2aab4b8bbf054b"
    )
    assert RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION == "3.0.13"
    assert (
        RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256
        == "b5f96ee6f0201fe31eaa5018dd341dc924d458f88027f9c5fcd631b5c8dbcfe7"
    )


def test_gpu_control_uses_scheduler_fanout_not_upstream_serial_batch() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    assert "one_click_retopology.py" in worker
    assert "batch_retopology.py" not in worker
    assert '"CODEX_BIN": "/app/packages/asset_processing/codex_job_launcher.py"' in worker


def test_direct_v2_task_auth_uses_the_rotated_node_private_credential() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    assert 'persistent_auth_source = Path(environment["CODEX_HOME"]) / "auth.json"' in worker
    assert '"CODEX_AUTH_SOURCE": str(persistent_auth_source)' in worker
    assert '"CODEX_AUTH_WRITEBACK_DESTINATION": str(persistent_auth_source)' in worker


def test_rolling_completion_accepts_only_matching_approved_package_identity() -> None:
    current = {
        "package_version": RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION,
        "package_sha256": RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256,
    }
    previous = {
        "package_version": "3.0.11",
        "package_sha256": ("8140b5b2359e4ea5542b533fe697992668b45b10a2496d9084c8e52a2e398f2c"),
    }
    assert retopology_direct_v2_completion_identity_valid(current, current) is True
    assert retopology_direct_v2_completion_identity_valid(previous, previous) is True
    assert retopology_direct_v2_completion_identity_valid(current, previous) is False
    unknown = {**current, "package_version": "3.0.1"}
    assert retopology_direct_v2_completion_identity_valid(unknown, unknown) is False


def test_public_create_contract_selects_v313_without_changing_route() -> None:
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/v1/assets/retopology/process")' in api
    assert '"schema_version": "retopology_input.direct-v2"' in api
    assert '"engine_contract": "retopology-direct-v2"' in api
    assert "RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION" in api
    assert '"schema_version": "retopology_direct_delivery.v8"' in Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text("utf-8")


def test_v313_no_broken_face_no_uv_fast_delivery_contract_is_wired() -> None:
    entrypoint = (ROOT / "server" / "one_click_retopology.py").read_text("utf-8")
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        "utf-8"
    )
    assert "shape_validation_command" not in entrypoint
    assert "alignment_views_dir" not in entrypoint
    assert '"topology_gate": "no_broken_faces"' in entrypoint
    assert '"uv_policy": "preserve_optional"' in entrypoint
    assert '"shape_validation": sidecar' not in worker
    assert '"alignment_views": sidecar' not in worker
    prompt = (ROOT / "server/agent_prompt.md").read_text("utf-8")
    assert "不生成 UV" in prompt
    assert "不执行 FBX 重新导入验证" in prompt
    assert "开放边、非流形边、游离点边、重复点面和面朝向仅记录为诊断" in prompt
    assert "ATTEMPT_GUIDANCE" in prompt
    assert '"--attempt-number"' in worker
