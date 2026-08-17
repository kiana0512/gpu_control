"""Dedicated WSL control agent for the licensed Windows MOF Blender runtime.

The process runs on the 4070 Ti WSL host, but every Blender subprocess is the
native Windows Blender executable.  It advertises only ``mof_low_seam`` and
uses the Asset API's existing signed heartbeat, lease, progress and artifact
contracts.  The server independently restricts this Worker to MOF UV jobs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("gpu_control_mof_worker")

WORKER_ID = os.environ.get("MOF_WORKER_ID", "asset-worker-4070ti-mof-01")
NODE_ID = os.environ.get("MOF_NODE_ID", "worker-4070ti-animation-host-01")
WORKER_SKILL_VERSION = "mof-windows-1.0.9-2026.08.17-v2"
BLENDER_VERSION = "5.2.0"
CONTROL_BASE_URL = os.environ.get("MOF_CONTROL_BASE_URL", "https://10.3.34.11").rstrip("/")
if not CONTROL_BASE_URL.startswith("https://"):
    raise RuntimeError("MOF control-plane URL must use HTTPS")
CONTROL_CA = Path(
    os.environ.get(
        "MOF_CONTROL_CA",
        "/opt/gpu-control/runtime/codex/lan-ca.crt",
    )
)
CONTROL_ENV = Path(os.environ.get("MOF_CONTROL_ENV", "/opt/gpu-control/.env"))
WINDOWS_ROOT = Path(os.environ.get("MOF_WINDOWS_ROOT", "/mnt/c/ProgramData/Li3D/MOFWorker"))
WINDOWS_BLENDER = Path(
    os.environ.get(
        "MOF_WINDOWS_BLENDER",
        "/mnt/c/Program Files/Blender Foundation/Blender 5.2/blender.exe",
    )
)
SCRIPT_ROOT = WINDOWS_ROOT / "scripts"
JOBS_ROOT = WINDOWS_ROOT / "jobs"
POLL_SECONDS = float(os.environ.get("MOF_POLL_SECONDS", "2"))
LEASE_RENEWAL_SECONDS = float(os.environ.get("MOF_LEASE_RENEWAL_SECONDS", "20"))
ONCE = os.environ.get("MOF_AGENT_ONCE", "").lower() in {"1", "true", "yes"}

MOF_UNWRAP_SHA256 = "08b2b2bae8a5bcdfd6da7099df9b06ea29ac661fcb522ee0a621158f9fffe6c7"
MOF_PREFLIGHT_SHA256 = "d4639ebd34128b02496599eef55c21ed1eab295c6117fc234c819003e491db40"
UV_QA_SHA256 = "bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d"
UV_UNIT_SHA256 = "67e98dc5db415a83736ee154856b2c3b54f057e69440d1edbc76e43873afa24e"
QA_ADAPTER_SHA256 = "6163497253a8ee40d52ff602439cffff08c826f1da2ca30726fac5db91e6b562"


class AgentError(RuntimeError):
    pass


class JobCancelled(AgentError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_dotenv_value(path: Path, key: str) -> str:
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    raise AgentError(f"{key} is missing from {path}")


def available_memory_mb() -> int:
    for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def windows_path(path: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed WSL path converter and trusted local path
        ["/usr/bin/wslpath", "-w", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ControlClient:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise AgentError("Asset Worker HMAC secret is too short")
        if not CONTROL_CA.is_file():
            raise AgentError(f"control-plane CA is missing: {CONTROL_CA}")
        self.secret = secret.encode("utf-8")
        self.context = ssl.create_default_context(cafile=str(CONTROL_CA))

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> bytes:
        request = urllib.request.Request(  # noqa: S310 - HTTPS-only base URL is validated above
            CONTROL_BASE_URL + path,
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - request is restricted to HTTPS
                request, context=self.context, timeout=timeout
            ) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[-4000:]
            raise AgentError(f"HTTP {error.code} for {path}: {detail}") from error
        except urllib.error.URLError as error:
            raise AgentError(f"request failed for {path}: {error.reason}") from error

    def signed_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json_bytes(payload)
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_sha = hashlib.sha256(body).hexdigest()
        message = f"POST\n{path}\n{timestamp}\n{nonce}\n{body_sha}".encode()
        signature = hmac.new(self.secret, message, hashlib.sha256).hexdigest()
        response = self._request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Asset-Timestamp": timestamp,
                "X-Asset-Nonce": nonce,
                "X-Asset-Signature": signature,
            },
        )
        return json.loads(response or b"{}")

    def leased_post(
        self,
        path: str,
        lease: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            path,
            body=json_bytes(payload),
            headers={"Content-Type": "application/json", "X-Asset-Lease": lease},
        )
        return json.loads(response or b"{}")

    def progress(
        self,
        job_id: str,
        lease: str,
        progress: float,
        stage: str,
        message: str,
        eta: int | None,
    ) -> None:
        result = self.leased_post(
            f"/internal/v1/assets/jobs/{job_id}/progress",
            lease,
            {
                "progress": min(max(progress, 0.0), 99.0),
                "stage": stage,
                "message": message,
                "estimated_remaining_seconds": eta,
            },
        )
        if result.get("cancel_requested"):
            raise JobCancelled("asset job was cancelled")

    def download(self, path: str, lease: str, destination: Path, expected_sha: str) -> None:
        request = urllib.request.Request(  # noqa: S310 - HTTPS-only base URL is validated above
            CONTROL_BASE_URL + path,
            headers={"X-Asset-Lease": lease},
            method="GET",
        )
        digest = hashlib.sha256()
        try:
            with (
                urllib.request.urlopen(  # noqa: S310 - request is restricted to HTTPS
                    request,
                    context=self.context,
                    timeout=3600,
                ) as response,
                destination.open("xb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
        except (urllib.error.URLError, OSError) as error:
            raise AgentError(f"input download failed: {error}") from error
        if not hmac.compare_digest(digest.hexdigest(), expected_sha):
            raise AgentError("downloaded asset SHA-256 mismatch")

    def upload(
        self,
        job_id: str,
        lease: str,
        artifacts: dict[str, Path],
    ) -> None:
        path = f"/internal/v1/assets/jobs/{job_id}/uv-v2-complete"
        command = [
            "/usr/bin/curl",
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--cacert",
            str(CONTROL_CA),
            "--connect-timeout",
            "10",
            "--max-time",
            "3600",
            "-X",
            "POST",
            CONTROL_BASE_URL + path,
            "-H",
            f"X-Asset-Lease: {lease}",
        ]
        for field, artifact in artifacts.items():
            content_type = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
            command.extend(["-F", f"{field}=@{artifact};type={content_type}"])
        process = subprocess.Popen(  # noqa: S603 - fixed curl executable and argument vector
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        started = time.monotonic()
        while process.poll() is None:
            try:
                process.wait(timeout=LEASE_RENEWAL_SECONDS)
            except subprocess.TimeoutExpired:
                self.progress(
                    job_id,
                    lease,
                    97.0,
                    "UPLOADING_ARTIFACTS",
                    "正在上传 MOF UV 五件套并保持任务租约",
                    None,
                )
        output = (process.stdout.read() if process.stdout else b"").decode("utf-8", "replace")
        if process.returncode != 0:
            raise AgentError(
                f"MOF completion upload failed after {time.monotonic() - started:.1f}s: "
                f"{output[-4000:]}"
            )


def verify_runtime_files() -> None:
    expected = {
        "mof_unwrap.py": MOF_UNWRAP_SHA256,
        "preflight_mof.py": MOF_PREFLIGHT_SHA256,
        "qa_uv.py": UV_QA_SHA256,
        "blender_uv_fbx_units.py": UV_UNIT_SHA256,
        "blender_uv_qa_adapter.py": QA_ADAPTER_SHA256,
    }
    for filename, digest in expected.items():
        path = SCRIPT_ROOT / filename
        if not path.is_file():
            raise AgentError(f"MOF runtime script is missing: {path}")
        actual = file_sha256(path)
        if not hmac.compare_digest(actual, digest):
            raise AgentError(f"MOF runtime script SHA-256 mismatch: {filename}={actual}")
    if not WINDOWS_BLENDER.is_file():
        raise AgentError(f"Windows Blender is missing: {WINDOWS_BLENDER}")


def run_startup_preflight() -> None:
    version = subprocess.run(  # noqa: S603 - pinned local Windows Blender executable
        [str(WINDOWS_BLENDER), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.splitlines()[0]
    if not version.startswith(f"Blender {BLENDER_VERSION}"):
        raise AgentError(f"unexpected Windows Blender version: {version}")
    completed = subprocess.run(  # noqa: S603 - pinned local Windows Blender executable
        [
            str(WINDOWS_BLENDER),
            "--background",
            "--python-exit-code",
            "1",
            "--python",
            windows_path(SCRIPT_ROOT / "preflight_mof.py"),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0 or '"available": true' not in completed.stdout.lower():
        raise AgentError(
            "UV_MOF_RUNTIME_UNAVAILABLE: Windows Blender MOF preflight failed: "
            + (completed.stdout + completed.stderr)[-4000:]
        )


def run_blender_stage(
    client: ControlClient,
    job_id: str,
    lease: str,
    arguments: list[str],
    *,
    progress_start: float,
    progress_end: float,
    stage: str,
    message: str,
    estimate_seconds: int,
) -> None:
    log_path = JOBS_ROOT / job_id / f"{stage.lower()}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("wb")
    process = subprocess.Popen(  # noqa: S603 - pinned Blender and controlled argument vector
        [str(WINDOWS_BLENDER), *arguments],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    started = time.monotonic()
    try:
        while process.poll() is None:
            try:
                process.wait(timeout=LEASE_RENEWAL_SECONDS)
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                fraction = min(elapsed / max(estimate_seconds, 1), 0.9)
                client.progress(
                    job_id,
                    lease,
                    progress_start + (progress_end - progress_start) * fraction,
                    stage,
                    message,
                    max(estimate_seconds - int(elapsed), 0) or None,
                )
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    finally:
        log_handle.close()
    output = log_path.read_text("utf-8", errors="replace")
    if len(output.encode("utf-8", "replace")) > 16 * 1024 * 1024:
        raise AgentError("Windows Blender output exceeded the 16 MiB safety limit")
    if process.returncode != 0:
        raise AgentError(output[-8000:] or f"Windows Blender exited {process.returncode}")


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text("utf-8"))
    if not isinstance(payload, dict):
        raise AgentError(f"expected a JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def process_job(client: ControlClient, job: dict[str, Any]) -> None:
    job_id = str(job["job_id"])
    lease = str(job["lease_token"])
    if job.get("job_type") != "UV_PROCESS_V2":
        raise AgentError(f"MOF Worker received unsupported job type: {job.get('job_type')}")
    options = job.get("options") or {}
    if options.get("algorithm") != "mof_low_seam":
        raise AgentError("MOF Worker received a non-MOF UV job")
    if options.get("asset_profile") != "complex_non_hardsurface":
        raise AgentError(
            "UV_MOF_ASSET_PROFILE_REQUIRED: MOF is restricted to explicitly "
            "classified complex non-hard-surface assets"
        )

    job_root = JOBS_ROOT / job_id
    if job_root.exists():
        shutil.rmtree(job_root)
    output_root = job_root / "output"
    output_root.mkdir(parents=True, exist_ok=False)
    source_filename = str(job["source_filename"])
    input_path = job_root / source_filename
    stem = input_path.stem
    output_blend = output_root / f"{stem}_PBR_UV.blend"
    output_fbx = output_root / f"{stem}_PBR_UV.fbx"
    output_report = output_root / f"{stem}_PBR_UV_report.json"
    blend_qa = output_root / f"{stem}_PBR_UV_QA.json"
    fbx_qa = output_root / f"{stem}_PBR_UV_FBX_QA.json"
    unit_report_path = output_root / ".fbx-unit-contract.json"

    client.progress(
        job_id,
        lease,
        2,
        "DOWNLOADING_INPUT",
        "MOF Worker 正在下载并校验不可变输入",
        300,
    )
    client.download(
        str(job["input_url"]),
        lease,
        input_path,
        str(job["input_sha256"]),
    )
    resolution = int(options.get("resolution", 2048))
    padding_px = int(options.get("padding_px", 10))
    run_blender_stage(
        client,
        job_id,
        lease,
        [
            "--background",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            windows_path(SCRIPT_ROOT / "mof_unwrap.py"),
            "--",
            "--input",
            windows_path(input_path),
            "--output-blend",
            windows_path(output_blend),
            "--output-fbx",
            windows_path(output_fbx),
            "--report",
            windows_path(output_report),
            "--resolution",
            str(resolution),
            "--padding-px",
            str(padding_px),
        ],
        progress_start=5,
        progress_end=62,
        stage="UV_UNWRAPPING",
        message="Windows Blender 正在调用 MinistryOfFlat 少切线展开并排版",
        estimate_seconds=300,
    )
    run_blender_stage(
        client,
        job_id,
        lease,
        [
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            windows_path(SCRIPT_ROOT / "blender_uv_fbx_units.py"),
            "--",
            "--source-asset",
            windows_path(input_path),
            "--input-blend",
            windows_path(output_blend),
            "--output-fbx",
            windows_path(output_fbx),
            "--output-report",
            windows_path(unit_report_path),
        ],
        progress_start=62,
        progress_end=70,
        stage="UV_FBX_UNIT_PRESERVATION",
        message="正在保持拓扑与 UV 不变并继承输入文件单位",
        estimate_seconds=60,
    )
    unit_report = load_json_object(unit_report_path)
    if unit_report.get("passed") is not True:
        raise AgentError("UV FBX source unit contract did not pass")
    report = load_json_object(output_report)
    report["algorithm"] = "mof_low_seam"
    report["input"] = source_filename
    report["fbx_unit_contract"] = unit_report
    write_json(output_report, report)

    for label, source, qa_path, start, end in (
        ("blend", output_blend, blend_qa, 70.0, 82.0),
        ("fbx_readback", output_fbx, fbx_qa, 82.0, 94.0),
    ):
        run_blender_stage(
            client,
            job_id,
            lease,
            [
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                windows_path(SCRIPT_ROOT / "blender_uv_qa_adapter.py"),
                "--",
                "--input",
                windows_path(source),
                "--output",
                windows_path(qa_path),
            ],
            progress_start=start,
            progress_end=end,
            stage="UV_QA_BLEND" if label == "blend" else "UV_QA_FBX_READBACK",
            message=(
                "正在检查 MOF Blender 工程 UV"
                if label == "blend"
                else "正在回读 FBX 并验证 MOF UV 可交付性"
            ),
            estimate_seconds=60,
        )
        qa_payload = load_json_object(qa_path)
        qa_payload["algorithm"] = "mof_low_seam"
        write_json(qa_path, qa_payload)
        hard_failures = qa_payload.get("hard_failures")
        if qa_payload.get("passed") is not True or not isinstance(hard_failures, list):
            raise AgentError(f"UV_QA_FAILED: invalid {label} QA result")
        if hard_failures:
            raise AgentError(f"UV_QA_FAILED: {label}: {hard_failures[:20]}")

    unit_report_path.unlink()
    client.progress(
        job_id,
        lease,
        95,
        "UPLOADING_ARTIFACTS",
        "正在上传 MOF UV、FBX 回读和双重 QA 制品",
        60,
    )
    client.upload(
        job_id,
        lease,
        {
            "blend": output_blend,
            "fbx": output_fbx,
            "report": output_report,
            "qa": blend_qa,
            "fbx_qa": fbx_qa,
        },
    )
    shutil.rmtree(job_root)


def heartbeat_payload(
    agent_instance_id: str,
    agent_started_at: str,
    current_jobs: int,
) -> dict[str, Any]:
    return {
        "worker_id": WORKER_ID,
        "node_id": NODE_ID,
        "display_name": "4070 Ti Windows MOF UV Worker",
        "hostname": socket.gethostname(),
        "blender_version": BLENDER_VERSION,
        "skill_version": WORKER_SKILL_VERSION,
        "cpu_count": os.cpu_count() or 1,
        "max_concurrency": 1,
        "current_jobs": current_jobs,
        "load_1m": os.getloadavg()[0],
        "available_memory_mb": available_memory_mb(),
        "agent_instance_id": agent_instance_id,
        "agent_started_at": agent_started_at,
    }


def report_failure(client: ControlClient, job: dict[str, Any], error: Exception) -> None:
    diagnostic = (str(error) or type(error).__name__)[-4000:]
    if "UV_MOF_RUNTIME_UNAVAILABLE" in diagnostic:
        code = "UV_MOF_RUNTIME_UNAVAILABLE"
    elif "UV_QA_FAILED" in diagnostic or "hard failures" in diagnostic.lower():
        code = "UV_QA_FAILED"
    else:
        code = "BLENDER_EXECUTION_FAILED"
    try:
        client.leased_post(
            f"/internal/v1/assets/jobs/{job['job_id']}/fail",
            str(job["lease_token"]),
            {
                "code": code,
                "message": diagnostic,
                "retryable": code == "BLENDER_EXECUTION_FAILED",
            },
        )
    except Exception:
        LOG.exception("failed to report MOF job failure", extra={"job_id": job.get("job_id")})


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    verify_runtime_files()
    run_startup_preflight()
    secret = read_dotenv_value(CONTROL_ENV, "ASSET_WORKER_HMAC_SECRET")
    client = ControlClient(secret)
    instance_id = uuid.uuid4().hex
    # The WSL host intentionally runs Ubuntu 22.04's Python 3.10, which has no datetime.UTC.
    started_at = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    LOG.info(
        "MOF Worker runtime verified",
        extra={"worker_id": WORKER_ID, "node_id": NODE_ID},
    )
    current_jobs = 0
    while True:
        try:
            heartbeat = client.signed_post(
                "/internal/v1/assets/workers/heartbeat",
                heartbeat_payload(instance_id, started_at, current_jobs),
            )
            if heartbeat.get("status") != "ONLINE":
                raise AgentError(f"MOF Worker heartbeat is not ONLINE: {heartbeat}")
            claim = client.signed_post(
                "/internal/v1/assets/jobs/claim",
                {
                    "worker_id": WORKER_ID,
                    "node_id": NODE_ID,
                    "agent_instance_id": instance_id,
                    "load_1m": os.getloadavg()[0],
                    "available_memory_mb": available_memory_mb(),
                    "accepts_codex_jobs": False,
                    "uv_algorithms": ["mof_low_seam"],
                },
            )
            job = claim.get("job")
            if job is None:
                if ONCE:
                    return
                time.sleep(POLL_SECONDS)
                continue
            current_jobs = 1
            client.signed_post(
                "/internal/v1/assets/workers/heartbeat",
                heartbeat_payload(instance_id, started_at, current_jobs),
            )
            try:
                process_job(client, job)
                LOG.info("MOF UV job completed", extra={"job_id": job.get("job_id")})
            except Exception as error:
                LOG.exception("MOF UV job failed", extra={"job_id": job.get("job_id")})
                report_failure(client, job, error)
            finally:
                current_jobs = 0
        except (AgentError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
            LOG.exception("MOF Worker control loop failed; retrying")
            if ONCE:
                raise
            time.sleep(min(max(POLL_SECONDS, 2.0), 30.0))


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(130)
