import hashlib
import subprocess
import sys
from pathlib import Path

from packages.gpu_control_core.assets import (
    RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256,
    RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION,
)

ROOT = Path("resources/retopology-direct-v2")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_approved_v302_package_is_complete() -> None:
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
        == "161382022a9d3abf62f2da098c06829d061a51c812f65426a9b74d9030588fcc"
    )
    assert RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION == "3.0.2"
    assert (
        RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256
        == "258c5b04686f938a6bbbe82f713701f5274b84ef56dda4b577105de4b7a1b542"
    )


def test_gpu_control_uses_scheduler_fanout_not_upstream_serial_batch() -> None:
    worker = Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text(encoding="utf-8")
    assert "one_click_retopology.py" in worker
    assert "batch_retopology.py" not in worker
    assert '"CODEX_BIN": "/app/packages/asset_processing/codex_job_launcher.py"' in worker


def test_direct_v2_task_auth_uses_the_rotated_node_private_credential() -> None:
    worker = Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text(encoding="utf-8")
    assert 'persistent_auth_source = Path(environment["CODEX_HOME"]) / "auth.json"' in worker
    assert '"CODEX_AUTH_SOURCE": str(persistent_auth_source)' in worker


def test_public_create_contract_selects_v302_without_changing_route() -> None:
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text(
        encoding="utf-8"
    )
    assert '@app.post("/api/v1/assets/retopology/process")' in api
    assert '"schema_version": "retopology_input.direct-v2"' in api
    assert '"engine_contract": "retopology-direct-v2"' in api
    assert "RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION" in api
    assert '"schema_version": "retopology_direct_delivery.v7"' in Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text("utf-8")
