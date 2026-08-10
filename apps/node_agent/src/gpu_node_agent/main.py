import asyncio
import hashlib
import importlib.metadata
import json
import math
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


def _runtime_identity() -> tuple[str, str]:
    try:
        package_version = importlib.metadata.version("gpu-control")
    except importlib.metadata.PackageNotFoundError:
        package_version = "development"
    return package_version, os.getenv("GPU_CONTROL_REVISION", "unknown")


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
    codex_binary: Path = Path("/usr/local/bin/codex")

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


def _validated_gpu_model(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+()/:,\-]{0,127}", normalized):
        raise RuntimeError("invalid GPU product name")
    return normalized


async def _gpu_model() -> str:
    process = await asyncio.create_subprocess_exec(
        _nvidia_smi_path(),
        "--query-gpu=name",
        "--format=csv,noheader",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "nvidia-smi failed")
    lines = stdout.decode(errors="replace").splitlines()
    if not lines:
        raise RuntimeError("invalid nvidia-smi product name response")
    return _validated_gpu_model(lines[0])


def _optional_gpu_metric(value: str, *, minimum: float, maximum: float) -> float | None:
    normalized = value.strip()
    if normalized.lower() in {"n/a", "[n/a]", "not supported", "unknown", ""}:
        return None
    try:
        metric = float(normalized)
    except ValueError:
        return None
    if not math.isfinite(metric) or metric < minimum or metric > maximum:
        return None
    return round(metric, 1)


async def _gpu_metrics() -> dict[str, int | float | None]:
    process = await asyncio.create_subprocess_exec(
        _nvidia_smi_path(),
        "--query-gpu=utilization.gpu,memory.free,memory.total,temperature.gpu,power.draw,power.limit",
        "--format=csv,noheader,nounits",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "nvidia-smi failed")
    lines = stdout.decode().splitlines()
    fields = [field.strip() for field in lines[0].split(",")] if lines else []
    if len(fields) != 6:
        raise RuntimeError("invalid nvidia-smi metrics response")
    try:
        utilization, free_vram_mb, total_vram_mb = (int(float(field)) for field in fields[:3])
    except ValueError as exc:
        raise RuntimeError("invalid nvidia-smi metrics value") from exc
    return {
        "gpu_util_percent": max(0, min(utilization, 100)),
        "free_vram_mb": max(0, free_vram_mb),
        "total_vram_mb": max(0, total_vram_mb),
        "gpu_temperature_c": _optional_gpu_metric(fields[3], minimum=0, maximum=150),
        "gpu_power_w": _optional_gpu_metric(fields[4], minimum=0, maximum=2000),
        "gpu_power_limit_w": _optional_gpu_metric(fields[5], minimum=0, maximum=2000),
    }


def _parse_meminfo(text: str) -> dict[str, int]:
    """Parse the small, kernel-owned /proc/meminfo surface into KiB values."""

    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        fields = raw_value.split()
        if not fields:
            continue
        try:
            value = int(fields[0])
        except ValueError:
            continue
        if value >= 0:
            values[key] = value
    return values


def _parse_pressure_avg10(text: str, level: str) -> float | None:
    """Return one Linux PSI avg10 value without exposing raw proc contents."""

    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] != level:
            continue
        for field in fields[1:]:
            key, separator, raw_value = field.partition("=")
            if key != "avg10" or not separator:
                continue
            try:
                value = float(raw_value)
            except ValueError:
                return None
            if math.isfinite(value) and 0 <= value <= 100:
                return round(value, 2)
            return None
    return None


def _read_pressure(proc_root: Path, resource: str, level: str) -> float | None:
    try:
        text = (proc_root / "pressure" / resource).read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_pressure_avg10(text, level)


def _system_metrics(
    proc_root: Path = Path("/proc"), *, cpu_count: int | None = None
) -> dict[str, str | int | float | None]:
    """Read a bounded WSL/Linux runtime snapshot from trusted procfs files."""

    os_release = (proc_root / "sys" / "kernel" / "osrelease").read_text(encoding="utf-8").strip()
    boot_id = (
        (proc_root / "sys" / "kernel" / "random" / "boot_id")
        .read_text(encoding="utf-8")
        .strip()
        .lower()
    )
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot_id):
        raise RuntimeError("invalid kernel boot id")

    uptime_fields = (proc_root / "uptime").read_text(encoding="utf-8").split()
    load_fields = (proc_root / "loadavg").read_text(encoding="utf-8").split()
    if not uptime_fields or len(load_fields) < 3:
        raise RuntimeError("invalid procfs runtime metrics")
    try:
        uptime_seconds = float(uptime_fields[0])
        loads = [float(value) for value in load_fields[:3]]
    except ValueError as exc:
        raise RuntimeError("invalid procfs numeric metrics") from exc
    if (
        not math.isfinite(uptime_seconds)
        or uptime_seconds < 0
        or any(not math.isfinite(value) or value < 0 for value in loads)
    ):
        raise RuntimeError("invalid procfs metric range")

    processors = max(1, cpu_count if cpu_count is not None else (os.cpu_count() or 1))
    meminfo = _parse_meminfo((proc_root / "meminfo").read_text(encoding="utf-8"))
    try:
        memory_total_kib = meminfo["MemTotal"]
        memory_available_kib = meminfo["MemAvailable"]
    except KeyError as exc:
        raise RuntimeError("required memory metrics are unavailable") from exc
    if memory_total_kib <= 0 or memory_available_kib > memory_total_kib:
        raise RuntimeError("invalid memory metric range")
    swap_total_kib = meminfo.get("SwapTotal", 0)
    swap_free_kib = meminfo.get("SwapFree", 0)
    if swap_free_kib > swap_total_kib:
        raise RuntimeError("invalid swap metric range")
    swap_used_kib = swap_total_kib - swap_free_kib

    return {
        "platform": "wsl2" if "microsoft" in os_release.lower() else "linux",
        "boot_id": boot_id,
        "uptime_seconds": round(uptime_seconds, 2),
        "cpu_count": processors,
        "load_1m_per_cpu": round(loads[0] / processors, 4),
        "load_5m_per_cpu": round(loads[1] / processors, 4),
        "load_15m_per_cpu": round(loads[2] / processors, 4),
        "memory_total_mb": memory_total_kib // 1024,
        "memory_available_mb": memory_available_kib // 1024,
        "memory_available_ratio": round(memory_available_kib / memory_total_kib, 6),
        "swap_total_mb": swap_total_kib // 1024,
        "swap_used_mb": swap_used_kib // 1024,
        "swap_used_ratio": (round(swap_used_kib / swap_total_kib, 6) if swap_total_kib else None),
        "cpu_pressure_some_avg10": _read_pressure(proc_root, "cpu", "some"),
        "memory_pressure_some_avg10": _read_pressure(proc_root, "memory", "some"),
        "memory_pressure_full_avg10": _read_pressure(proc_root, "memory", "full"),
        "io_pressure_some_avg10": _read_pressure(proc_root, "io", "some"),
        "io_pressure_full_avg10": _read_pressure(proc_root, "io", "full"),
    }


async def _codex_cli_runtime(binary: Path) -> dict[str, Any]:
    """Inspect the host CLI entry without reading credentials or calling a model."""
    checked_at = time.time()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return {
            "codex_cli_installed": False,
            "codex_cli_version": None,
            "codex_cli_error": "BINARY_UNAVAILABLE",
            "codex_cli_checked_at": checked_at,
        }
    try:
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        return {
            "codex_cli_installed": True,
            "codex_cli_version": None,
            "codex_cli_error": "VERSION_TIMEOUT",
            "codex_cli_checked_at": checked_at,
        }
    version = stdout.decode(errors="replace").strip()
    if process.returncode != 0 or not version:
        return {
            "codex_cli_installed": True,
            "codex_cli_version": None,
            "codex_cli_error": "VERSION_FAILED",
            "codex_cli_checked_at": checked_at,
        }
    return {
        "codex_cli_installed": True,
        "codex_cli_version": version[:64],
        "codex_cli_error": None,
        "codex_cli_checked_at": checked_at,
    }


def _git_head(repository: Path) -> str:
    head = (repository / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        reference = head.removeprefix("ref: ").strip()
        loose = repository / ".git" / reference
        if loose.is_file():
            head = loose.read_text(encoding="utf-8").strip()
        else:
            packed = (repository / ".git" / "packed-refs").read_text(encoding="utf-8")
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
    identity: dict[str, Any],
) -> dict[str, Any]:
    path = "/api/v1/nodes/heartbeat"
    body = json.dumps(identity, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = sign_agent_request("POST", path, body, timestamp, nonce, cfg.node_agent_hmac_secret)
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
    gpu_model: str | None = None
    gpu_model_next_check = 0.0
    mac: str | None = None
    last_ip = ""
    first_success = True
    codex_health: dict[str, Any] = {}
    codex_next_check = 0.0
    node_agent_version, source_revision = _runtime_identity()
    while True:
        try:
            if gpu_uuid is None:
                gpu_uuid = await _gpu_uuid()
            if gpu_model is None and time.monotonic() >= gpu_model_next_check:
                try:
                    gpu_model = await _gpu_model()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Product name is optional telemetry. A missing/unsupported
                    # nvidia-smi query must never suppress the identity heartbeat.
                    gpu_model_next_check = time.monotonic() + 60
                    logger().warning(
                        "node.gpu_model_unavailable",
                        node_id=cfg.node_id,
                        error_type=type(exc).__name__,
                    )
            if mac is None:
                mac = _mac_address(cfg.node_mac_address)
            imageclip_commit, imageclip_pipeline_sha256 = await asyncio.to_thread(
                _imageclip_pipeline_state, cfg.imageclip_root
            )
            current_ip = await asyncio.to_thread(
                _current_ip, cfg.control_host, cfg.node_advertise_ip
            )
            if time.monotonic() >= codex_next_check:
                codex_health = await _codex_cli_runtime(cfg.codex_binary)
                codex_next_check = time.monotonic() + 60
            identity = {
                "node_id": cfg.node_id,
                "ip": current_ip,
                "mac": mac,
                "gpu_uuid": gpu_uuid,
                "hostname": socket.gethostname(),
                "imageclip_commit": imageclip_commit,
                "imageclip_pipeline_sha256": imageclip_pipeline_sha256,
                "node_agent_version": node_agent_version,
                **codex_health,
            }
            if gpu_model is not None:
                identity["gpu_model"] = gpu_model
            if re.fullmatch(r"[0-9a-f]{40}", source_revision):
                identity["source_revision"] = source_revision
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
    node_agent_version, source_revision = _runtime_identity()

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

    app = FastAPI(
        title="GPU Node Agent",
        version=node_agent_version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

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
        return {
            "status": "live",
            "package_version": node_agent_version,
            "source_revision": source_revision,
        }

    @app.get("/health/ready")
    async def ready() -> dict[str, Any]:
        control_script = Path("/usr/local/sbin/gpu-node-ctl")
        ready_now = control_script.is_file() if cfg.environment.lower() == "production" else True
        if not ready_now:
            raise HTTPException(503, "gpu-node-ctl is not installed")
        return {"status": "ready", "control_script": str(control_script)}

    @app.get("/v1/identity")
    async def identity() -> dict[str, Any]:
        current = dict(app.state.node_identity)
        if not current:
            raise HTTPException(503, "node identity is not initialized")
        return current

    @app.get("/v1/gpu-metrics")
    async def gpu_metrics() -> dict[str, int | float | None]:
        return await _gpu_metrics()

    @app.get("/v1/system-metrics")
    async def system_metrics() -> dict[str, str | int | float | None]:
        # procfs reads are tiny but synchronous; keep them off the ASGI event loop.
        return await asyncio.to_thread(_system_metrics)

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
                        process.pid,
                        getattr(signal, "SIGKILL"),  # noqa: B009
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
