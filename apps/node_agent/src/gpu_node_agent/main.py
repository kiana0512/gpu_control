import asyncio
import os
import re
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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


def create_app(settings: Settings | NodeAgentSettings | None = None) -> FastAPI:
    cfg = settings or NodeAgentSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging("node-agent", cfg.environment)
        app.state.nonces = {}
        app.state.operation_lock = asyncio.Lock()
        yield

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
            "node_operation",
            event="node_agent.operation",
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
