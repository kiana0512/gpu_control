import hashlib
import subprocess
import sys
from pathlib import Path

from packages.gpu_control_core.assets import RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256

ROOT = Path("resources/retopology-direct-v2")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_approved_v230_package_is_complete() -> None:
    completed = subprocess.run(  # noqa: S603 - repository-owned verifier
        [sys.executable, str(ROOT / "server" / "verify_package.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (ROOT / "server" / "batch_retopology.py").is_file()
    assert (
        file_sha256(ROOT / "blender-retopology-compare-iterate" / "SKILL.md")
        == "03dff7efe9ffac9a365a0b81637bc3065fd4fe7259c67a9d2eb4ebf697e450aa"
    )
    assert (
        RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256
        == "d86f218d2194bd6260a491da66f89b8954a72ef8e5309c0ff1062c639d8f6ec4"
    )


def test_gpu_control_uses_scheduler_fanout_not_upstream_serial_batch() -> None:
    worker = Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text(encoding="utf-8")
    assert "one_click_retopology.py" in worker
    assert "batch_retopology.py" not in worker
    assert '"CODEX_BIN": "/app/packages/asset_processing/codex_job_launcher.py"' in worker


def test_public_create_contract_selects_v230_without_changing_route() -> None:
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text(
        encoding="utf-8"
    )
    assert '@app.post("/api/v1/assets/retopology/process")' in api
    assert '"schema_version": "retopology_input.direct-v2"' in api
    assert '"engine_contract": "retopology-direct-v2"' in api
    assert '"package_version": "2.3.0"' in api
