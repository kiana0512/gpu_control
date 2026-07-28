import asyncio
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import ssl
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from urllib import request as urllib_request

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.gpu_control_core.logging import configure_logging, logger
from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings


class NodeAgentSettings(BaseSettings):
    """Keep control-plane secrets out of GPU worker configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    node_agent_hmac_secret: str = "development-only-change-me"
    node_id: str = ""
    control_host: str = ""
    node_advertise_ip: str = ""
    node_mac_address: str = ""
    node_heartbeat_interval_seconds: int = Field(10, ge=5, le=300)
    node_control_ca_cert: Path = Path("/etc/gpu-control/lan-ca.crt")
    imageclip_root: Path = Path("/opt/imageclip")

    @model_validator(mode="after")
    def reject_weak_production_secret(self) -> "NodeAgentSettings":
        if self.environment.lower() == "production" and (
            len(self.node_agent_hmac_secret) < 32
            or self.node_agent_hmac_secret.startswith("CHANGE_ME")
            or self.node_agent_hmac_secret == "development-only-change-me"
        ):
            raise ValueError("production NODE_AGENT_HMAC_SECRET must be at least 32 characters")
        return self


class Operation(BaseModel):
    action: str
    lines: int = Field(200, ge=1, le=2000)


COMMANDS: dict[str, tuple[str, ...]] = {
    "status": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "status"),
    "start": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "start"),
    "stop": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "stop"),
    "restart": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "restart"),
    "nvidia-smi": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "nvidia-smi"),
    "system": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "system"),
    "diagnostics": ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "diagnostics"),
}


def _default_interface() -> str:
    for line in Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "00000000" and int(fields[3], 16) & 2:
            return fields[0]
    raise RuntimeError("default network interface not found")


def _current_ip(control_host: str, fallback: str = "") -> str:
    # Hybrid Windows/WSL nodes must advertise the stable physical host address,
    # never the ephemeral WSL NAT address.  Bare-metal nodes also benefit from
    # an explicit address because it makes DHCP reservations auditable.
    if fallback:
        return fallback
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((control_host, 443))
            return str(probe.getsockname()[0])
    except OSError:
        raise


def _mac_address(override: str = "") -> str:
    if override:
        normalized = override.strip().lower().replace("-", ":")
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", normalized):
            raise RuntimeError("invalid configured physical MAC address")
        return normalized
    interface = _default_interface()
    return Path(f"/sys/class/net/{interface}/address").read_text(encoding="utf-8").strip().lower()


def _nvidia_smi_path() -> str:
    for candidate in (Path("/usr/bin/nvidia-smi"), Path("/usr/lib/wsl/lib/nvidia-smi")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError("nvidia-smi executable not found")


async def _gpu_uuid() -> str:
    process = await asyncio.create_subprocess_exec(
        _nvidia_smi_path(),
        "--query-gpu=uuid",
        "--format=csv,noheader",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "nvidia-smi failed")
    value = stdout.decode().splitlines()[0].strip()
    if not re.fullmatch(r"GPU-[0-9a-fA-F-]{36}", value):
        raise RuntimeError("invalid GPU UUID")
    return value


async def _gpu_metrics() -> dict[str, int]:
    process = await asyncio.create_subprocess_exec(
        _nvidia_smi_path(),
        "--query-gpu=utilization.gpu,memory.free,memory.total",
        "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "nvidia-smi failed")
    lines = stdout.decode().splitlines()
    fields = [field.strip() for field in lines[0].split(",")] if lines else []
    if len(fields) != 3:
        raise RuntimeError("invalid nvidia-smi metrics response")
    try:
        utilization, free_vram_mb, total_vram_mb = (int(float(field)) for field in fields)
    except ValueError as exc:
        raise RuntimeError("invalid nvidia-smi metrics value") from exc
    return {
        "gpu_util_percent": max(0, min(utilization, 100)),
        "free_vram_mb": max(0, free_vram_mb),
        "total_vram_mb": max(0, total_vram_mb),
    }


def _git_head(repository: Path) -> str:
    head = (repository / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        reference = head.removeprefix("ref: ").strip()
        loose = repository / ".git" / reference
        if loose.is_file():
            head = loose.read_text(encoding="utf-8").strip()
        else:
            packed = (repository / ".git" / "packed-refs").read_text(
                encoding="utf-8"
            )
            head = next(
                line.split(" ", 1)[0]
                for line in packed.splitlines()
                if line.endswith(f" {reference}")
            )
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise RuntimeError("invalid ImageClip git HEAD")
    return head


def _imageclip_pipeline_state(repository: Path) -> tuple[str, str]:
    paths = [repository / "ImageClip.json"]
    paths.extend(
        path
        for path in (repository / "Cherry_lizi").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    paths.sort(key=lambda path: path.relative_to(repository).as_posix())
    combined = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(repository).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        combined.update(f"{digest}  {relative}\n".encode())
    return _git_head(repository), combined.hexdigest()


def _post_heartbeat(
    cfg: NodeAgentSettings,
    identity: dict[str, str],
) -> dict[str, Any]:
    path = "/api/v1/nodes/heartbeat"
    body = json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = sign_agent_request(
        "POST", path, body, timestamp, nonce, cfg.node_agent_hmac_secret
    )
    heartbeat_request = urllib_request.Request(  # noqa: S310
        f"https://{cfg.control_host}{path}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-GPU-Timestamp": timestamp,
            "X-GPU-Nonce": nonce,
            "X-GPU-Signature": signature,
        },
    )
    context = ssl.create_default_context(cafile=str(cfg.node_control_ca_cert))
    with urllib_request.urlopen(heartbeat_request, timeout=5, context=context) as response:  # noqa: S310
        result = json.loads(response.read())
        if not isinstance(result, dict):
            raise RuntimeError("invalid heartbeat response")
        return cast(dict[str, Any], result)


async def _heartbeat_loop(app: FastAPI, cfg: NodeAgentSettings) -> None:
    # WSL may expose /usr/lib/wsl/lib/nvidia-smi a fraction later than systemd
    # starts this service. Keep identity discovery inside the retry loop so one
    # transient startup race cannot permanently stop node heartbeats.
    gpu_uuid: str | None = None
    mac: str | None = None
    last_ip = ""
    first_success = True
    while True:
        try:
            if gpu_uuid is None:
                gpu_uuid = await _gpu_uuid()
            if mac is None:
                mac = _mac_address(cfg.node_mac_address)
            imageclip_commit, imageclip_pipeline_sha256 = await asyncio.to_thread(
                _imageclip_pipeline_state, cfg.imageclip_root
            )
            current_ip = await asyncio.to_thread(
                _current_ip, cfg.control_host, cfg.node_advertise_ip
            )
            identity = {
                "node_id": cfg.node_id,
                "ip": current_ip,
                "mac": mac,
                "gpu_uuid": gpu_uuid,
                "hostname": socket.gethostname(),
                "imageclip_commit": imageclip_commit,
                "imageclip_pipeline_sha256": imageclip_pipeline_sha256,
            }
            result = await asyncio.to_thread(_post_heartbeat, cfg, identity)
            app.state.node_identity = identity
            if first_success or current_ip != last_ip:
                logger().info(
                    "node.heartbeat_accepted",
                    node_id=cfg.node_id,
                    source_ip=current_ip,
                    base_url=result.get("base_url"),
                )
            first_success = False
            last_ip = current_ip
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger().warning(
                "node.heartbeat_failed",
                node_id=cfg.node_id,
                error_type=type(exc).__name__,
            )
        await asyncio.sleep(cfg.node_heartbeat_interval_seconds)


def create_app(settings: Settings | NodeAgentSettings | None = None) -> FastAPI:
    cfg = settings or NodeAgentSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging("node-agent", cfg.environment)
        app.state.nonces = {}
        app.state.operation_lock = asyncio.Lock()
        app.state.node_identity = {}
        heartbeat_task: asyncio.Task[None] | None = None
        if isinstance(cfg, NodeAgentSettings) and cfg.node_id and cfg.control_host:
            heartbeat_task = asyncio.create_task(_heartbeat_loop(app, cfg))
        yield
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    app = FastAPI(title="GPU Node Agent", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        if request.url.path in {"/health/live", "/health/ready"}:
            return await call_next(request)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 16_384:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        body = await request.body()
        if len(body) > 16_384:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        timestamp = request.headers.get("x-gpu-timestamp", "")
        nonce = request.headers.get("x-gpu-nonce", "")
        signature = request.headers.get("x-gpu-signature", "")
        try:
            stamp = int(timestamp)
        except ValueError:
            return JSONResponse({"detail": "invalid timestamp"}, status_code=401)
        now = int(time.time())
        if abs(now - stamp) > 30 or not nonce or len(nonce) > 128:
            return JSONResponse({"detail": "expired request"}, status_code=401)
        nonces: dict[str, int] = request.app.state.nonces
        for key, seen in list(nonces.items()):
            if now - seen > 60:
                del nonces[key]
        if nonce in nonces:
            return JSONResponse({"detail": "replayed request"}, status_code=409)
        expected = sign_agent_request(
            request.method, request.url.path, body, timestamp, nonce, cfg.node_agent_hmac_secret
        )
        import hmac

        if not hmac.compare_digest(signature, expected):
            return JSONResponse({"detail": "invalid signature"}, status_code=401)
        nonces[nonce] = now
        return await call_next(request)

    @app.get("/health/live")
    async def health() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> dict[str, Any]:
        control_script = Path("/usr/local/sbin/gpu-node-ctl")
        ready_now = control_script.is_file() if cfg.environment.lower() == "production" else True
        if not ready_now:
            raise HTTPException(503, "gpu-node-ctl is not installed")
        return {"status": "ready", "control_script": str(control_script)}

    @app.get("/v1/identity")
    async def identity() -> dict[str, str]:
        current = dict(app.state.node_identity)
        if not current:
            raise HTTPException(503, "node identity is not initialized")
        return current

    @app.get("/v1/gpu-metrics")
    async def gpu_metrics() -> dict[str, int]:
        return await _gpu_metrics()

    @app.post("/v1/operations")
    async def operation(body: Operation) -> dict[str, Any]:
        command = COMMANDS.get(body.action)
        if command is None and body.action != "logs":
            raise HTTPException(400, "operation is not allowed")
        if body.action == "logs":
            command = ("sudo", "-n", "/usr/local/sbin/gpu-node-ctl", "logs", str(body.lines))
        assert command is not None
        async with app.state.operation_lock:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
                start_new_session=True,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError as exc:
                if os.name == "posix":
                    getattr(os, "killpg")(  # noqa: B009
                        process.pid, getattr(signal, "SIGKILL")  # noqa: B009
                    )
                else:
                    process.kill()
                await process.wait()
                raise HTTPException(504, "operation timed out") from exc
        redact = re.compile(r"(?i)(password|secret|token|authorization)=\S+")
        result = {
            "action": body.action,
            "exit_code": process.returncode,
            "stdout": redact.sub(r"\1=[REDACTED]", stdout.decode(errors="replace"))[-16_000:],
            "stderr": redact.sub(r"\1=[REDACTED]", stderr.decode(errors="replace"))[-4_000:],
        }
        logger().info(
            "node_agent.operation",
            action=body.action,
            exit_code=process.returncode,
        )
        if process.returncode != 0:
            raise HTTPException(502, result)
        return result

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "gpu_node_agent.main:app",
        host=os.getenv("NODE_AGENT_HOST", "0.0.0.0"),
        port=int(os.getenv("NODE_AGENT_PORT", "9201")),
    )
