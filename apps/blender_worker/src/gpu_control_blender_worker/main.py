import asyncio
import hashlib
import json
import logging
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.asset_processing import blender_uv
from packages.gpu_control_core.security import sign_agent_request

LOG = logging.getLogger("gpu_control_blender_worker")


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    asset_api_url: str = "http://asset-api:8010"
    asset_worker_id: str = "asset-worker-local"
    asset_node_id: str = "worker-local"
    asset_worker_display_name: str = "Local Blender Worker"
    asset_worker_hmac_secret: str = Field(min_length=32)
    asset_worker_max_concurrency: int = 2
    blender_binary: str = "/opt/blender/blender"
    blender_version: str = "5.1.2"
    blender_skill_version: str = "pbr-uv-v1.1"
    asset_poll_seconds: float = 1.0


def available_memory_mb() -> int:
    for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def signed_headers(settings: WorkerSettings, method: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = sign_agent_request(
        method, path, body, timestamp, nonce, settings.asset_worker_hmac_secret
    )
    return {
        "Content-Type": "application/json",
        "X-Asset-Timestamp": timestamp,
        "X-Asset-Nonce": nonce,
        "X-Asset-Signature": signature,
    }


async def signed_post(
    client: httpx.AsyncClient, settings: WorkerSettings, path: str, payload: dict[str, Any]
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return await client.post(
        path, content=body, headers=signed_headers(settings, "POST", path, body)
    )


async def heartbeat(
    client: httpx.AsyncClient, settings: WorkerSettings, running: int
) -> None:
    payload = {
        "worker_id": settings.asset_worker_id,
        "node_id": settings.asset_node_id,
        "display_name": settings.asset_worker_display_name,
        "hostname": socket.gethostname(),
        "blender_version": settings.blender_version,
        "skill_version": settings.blender_skill_version,
        "cpu_count": os.cpu_count() or 1,
        "max_concurrency": settings.asset_worker_max_concurrency,
        "current_jobs": running,
        "load_1m": os.getloadavg()[0],
        "available_memory_mb": available_memory_mb(),
    }
    response = await signed_post(
        client, settings, "/internal/v1/assets/workers/heartbeat", payload
    )
    response.raise_for_status()


async def run_blender(
    settings: WorkerSettings, input_path: Path, output_dir: Path, options: dict[str, Any]
) -> asyncio.subprocess.Process:
    environment = dict(os.environ)
    environment["CUDA_VISIBLE_DEVICES"] = ""
    return await asyncio.create_subprocess_exec(
        settings.blender_binary,
        "--background",
        "--factory-startup",
        "--python",
        str(Path(blender_uv.__file__).resolve()),
        "--",
        "--input",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--options-json",
        json.dumps(options, separators=(",", ":")),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )


async def process_job(
    client: httpx.AsyncClient, settings: WorkerSettings, job: dict[str, Any]
) -> None:
    job_id = str(job["job_id"])
    lease = str(job["lease_token"])
    lease_headers = {"X-Asset-Lease": lease}
    with tempfile.TemporaryDirectory(prefix=f"asset-{job_id}-") as temporary:
        root = Path(temporary)
        input_path = root / str(job["source_filename"])
        async with client.stream("GET", job["input_url"], headers=lease_headers) as response:
            response.raise_for_status()
            digest = hashlib.sha256()
            with input_path.open("xb") as destination:
                async for chunk in response.aiter_bytes():
                    digest.update(chunk)
                    destination.write(chunk)
        if digest.hexdigest() != job["input_sha256"]:
            raise RuntimeError("downloaded asset SHA-256 mismatch")
        output_dir = root / "output"
        process = await run_blender(settings, input_path, output_dir, job["options"])
        progress = 5.0
        while process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except TimeoutError as exc:
                progress = min(90.0, progress + 5.0)
                status = await client.post(
                    f"/internal/v1/assets/jobs/{job_id}/progress",
                    headers=lease_headers,
                    json={"progress": progress},
                )
                status.raise_for_status()
                if status.json().get("cancel_requested"):
                    process.terminate()
                    await process.wait()
                    raise RuntimeError("asset job cancelled") from exc
        output = await process.stdout.read() if process.stdout else b""
        if process.returncode != 0:
            raise RuntimeError(output.decode("utf-8", "replace")[-4000:])
        handles = []
        try:
            files: dict[str, tuple[str, Any, str]] = {}
            for kind, filename in {
                "blend": "model_PBR_UV.blend",
                "fbx": "model_PBR_UV.fbx",
                "report": "model_report.json",
                "qa": "model_QA.json",
            }.items():
                handle = (output_dir / filename).open("rb")
                handles.append(handle)
                content_type = "application/json" if filename.endswith(".json") else "application/octet-stream"
                files[kind] = (filename, handle, content_type)
            completed = await client.post(
                f"/internal/v1/assets/jobs/{job_id}/complete",
                headers=lease_headers,
                files=files,
                timeout=3600,
            )
            completed.raise_for_status()
        finally:
            for handle in handles:
                handle.close()


async def execute_job(
    client: httpx.AsyncClient, settings: WorkerSettings, job: dict[str, Any]
) -> None:
    try:
        await process_job(client, settings, job)
    except Exception as exc:
        try:
            response = await client.post(
                f"/internal/v1/assets/jobs/{job['job_id']}/fail",
                headers={"X-Asset-Lease": str(job["lease_token"])},
                json={
                    "code": "BLENDER_EXECUTION_FAILED",
                    "message": str(exc)[-4000:] or type(exc).__name__,
                    "retryable": True,
                },
            )
            response.raise_for_status()
        except Exception:
            # The lease recovery path in Asset API is the final safety net.
            LOG.exception("failed to report Blender job failure", extra={"job_id": job["job_id"]})


async def worker_loop(settings: WorkerSettings) -> None:
    timeout = httpx.Timeout(30, read=3600)
    async with httpx.AsyncClient(base_url=settings.asset_api_url, timeout=timeout) as client:
        running: set[asyncio.Task[None]] = set()
        while True:
            running = {task for task in running if not task.done()}
            await heartbeat(client, settings, len(running))
            while len(running) < settings.asset_worker_max_concurrency:
                response = await signed_post(
                    client,
                    settings,
                    "/internal/v1/assets/jobs/claim",
                    {
                        "worker_id": settings.asset_worker_id,
                        "load_1m": os.getloadavg()[0],
                        "available_memory_mb": available_memory_mb(),
                    },
                )
                response.raise_for_status()
                job = response.json().get("job")
                if job is None:
                    break
                task = asyncio.create_task(execute_job(client, settings, job))
                running.add(task)
            await asyncio.sleep(settings.asset_poll_seconds)


def run() -> None:
    asyncio.run(worker_loop(WorkerSettings()))


if __name__ == "__main__":
    run()
