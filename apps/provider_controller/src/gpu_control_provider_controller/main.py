from __future__ import annotations

import asyncio
import hmac
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from packages.gpu_control_core.autodl import (
    AutoDLProduct,
    AutoDLProvider,
    AutoDLProviderError,
    read_autodl_token,
    validate_ref,
)
from packages.gpu_control_core.logging import configure_logging
from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings, get_settings


class DesiredStateRequest(BaseModel):
    desired_state: Literal["running", "stopped"]
    correlation_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    bootstrap_profile: Literal["comfyui-6006-v1"] | None = None


def _provider_http_error(exc: AutoDLProviderError) -> HTTPException:
    if exc.code == "AUTODL_STATE_CONFLICT" or exc.status_code == 409:
        status = 409
    elif exc.status_code in {401, 403}:
        status = 502
    elif exc.status_code == 429:
        status = 503
    else:
        status = 502
    return HTTPException(
        status,
        detail={
            "code": exc.code or "AUTODL_PROVIDER_ERROR",
            "message": str(exc),
            "provider_request_id": exc.request_id,
            "uncertain": exc.uncertain,
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging("provider-controller", cfg.environment)
        token = read_autodl_token(cfg.autodl_token_file) if cfg.autodl_enabled else None
        app.state.provider = (
            AutoDLProvider(
                token,
                timeout_seconds=cfg.autodl_timeout_seconds,
                read_retries=cfg.autodl_read_retries,
            )
            if token
            else None
        )
        app.state.redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        try:
            await app.state.redis.ping()
        except Exception:
            await app.state.redis.aclose()
            app.state.redis = None
        app.state.operation_locks = {}
        app.state.inventory_lock = asyncio.Lock()
        app.state.inventory_cache = None
        app.state.inventory_cached_at = 0.0
        yield
        if app.state.provider is not None:
            await app.state.provider.aclose()
        if app.state.redis is not None:
            await app.state.redis.aclose()

    app = FastAPI(title="GPU Control Provider Controller", version="0.1.0", lifespan=lifespan)

    async def authenticated(
        request: Request,
        x_gpu_timestamp: Annotated[str | None, Header()] = None,
        x_gpu_nonce: Annotated[str | None, Header()] = None,
        x_gpu_signature: Annotated[str | None, Header()] = None,
    ) -> None:
        timestamp = x_gpu_timestamp or ""
        nonce = x_gpu_nonce or ""
        signature = x_gpu_signature or ""
        if not timestamp.isdigit() or abs(int(time.time()) - int(timestamp)) > 30:
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "时间戳无效"})
        if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce) is None:
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "Nonce 无效"})
        body = await request.body()
        signing_path = request.url.path
        if request.url.query:
            signing_path += "?" + request.url.query
        expected = sign_agent_request(
            request.method,
            signing_path,
            body,
            timestamp,
            nonce,
            cfg.provider_controller_hmac_secret.get_secret_value(),
        )
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "签名无效"})
        redis: Redis | None = request.app.state.redis
        if redis is None:
            raise HTTPException(
                503,
                detail={"code": "REPLAY_STORE_UNAVAILABLE", "message": "重放保护不可用"},
            )
        accepted = await redis.set(f"gpu-control:provider-nonce:{nonce}", "1", ex=60, nx=True)
        if not accepted:
            raise HTTPException(409, detail={"code": "REPLAY_DETECTED", "message": "重复请求"})

    def provider(request: Request) -> AutoDLProvider:
        value: AutoDLProvider | None = request.app.state.provider
        if value is None:
            raise HTTPException(
                503,
                detail={"code": "AUTODL_NOT_CONFIGURED", "message": "AutoDL Token 尚未配置"},
            )
        return value

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        configured = request.app.state.provider is not None
        replay_store = request.app.state.redis is not None
        return JSONResponse(
            {
                "status": "ready" if configured and replay_store else "not_ready",
                "autodl": configured,
                "replay_store": replay_store,
                "mutations_enabled": cfg.autodl_mutations_enabled,
            },
            status_code=200 if configured and replay_store else 503,
        )

    @app.get(
        "/internal/v1/providers/autodl/inventory",
        dependencies=[Depends(authenticated)],
    )
    async def inventory(
        request: Request,
        current: AutoDLProvider = Depends(provider),  # noqa: B008
        force: Annotated[bool, Query()] = False,
    ) -> dict[str, Any]:
        async with request.app.state.inventory_lock:
            cached = request.app.state.inventory_cache
            cached_at = float(request.app.state.inventory_cached_at)
            if not force and cached is not None and time.monotonic() - cached_at < 8:
                return cast(dict[str, Any], cached)
            try:
                result = await current.inventory()
            except AutoDLProviderError as exc:
                raise _provider_http_error(exc) from exc
            request.app.state.inventory_cache = result
            request.app.state.inventory_cached_at = time.monotonic()
            return result

    @app.get(
        "/internal/v1/providers/autodl/instances/{product}/{instance_id}/state",
        dependencies=[Depends(authenticated)],
    )
    async def read_state(
        request: Request,
        product: AutoDLProduct,
        instance_id: str,
        current: AutoDLProvider = Depends(provider),  # noqa: B008
    ) -> dict[str, Any]:
        """Return provider state without ever dispatching a power mutation."""

        try:
            validate_ref(product, instance_id)
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "AUTODL_REF_INVALID", "message": str(exc)}
            ) from exc
        lock_key = f"{product}:{instance_id}"
        lock = request.app.state.operation_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            try:
                current_state, provider_status = await current.state(product, instance_id)
            except AutoDLProviderError as exc:
                raise _provider_http_error(exc) from exc
        return {
            "accepted": False,
            "state": current_state,
            "provider_status": provider_status,
        }

    @app.post(
        "/internal/v1/providers/autodl/instances/{product}/{instance_id}/state",
        dependencies=[Depends(authenticated)],
    )
    async def ensure_state(
        request: Request,
        product: AutoDLProduct,
        instance_id: str,
        body: DesiredStateRequest,
        current: AutoDLProvider = Depends(provider),  # noqa: B008
    ) -> dict[str, Any]:
        if body.desired_state != "running" and body.bootstrap_profile is not None:
            raise HTTPException(
                422,
                detail={
                    "code": "AUTODL_BOOTSTRAP_PROFILE_INVALID",
                    "message": "bootstrap profile is valid only for a running transition",
                },
            )
        try:
            validate_ref(product, instance_id)
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "AUTODL_REF_INVALID", "message": str(exc)}
            ) from exc
        lock_key = f"{product}:{instance_id}"
        lock = request.app.state.operation_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            try:
                current_state, provider_status = await current.state(product, instance_id)
                if current_state == body.desired_state:
                    return {
                        "accepted": False,
                        "state": current_state,
                        "provider_status": provider_status,
                        "correlation_id": body.correlation_id,
                    }
                if not cfg.autodl_mutations_enabled:
                    raise HTTPException(
                        503,
                        detail={
                            "code": "AUTODL_MUTATIONS_DISABLED",
                            "message": "AutoDL 写操作尚未启用",
                        },
                    )
                result = await current.ensure_state(
                    product,
                    instance_id,
                    body.desired_state,
                    observed=(current_state, provider_status),
                    bootstrap_profile=(
                        body.bootstrap_profile if body.desired_state == "running" else None
                    ),
                )
            except (AutoDLProviderError, ValueError) as exc:
                if isinstance(exc, AutoDLProviderError):
                    raise _provider_http_error(exc) from exc
                raise HTTPException(
                    422, detail={"code": "AUTODL_REF_INVALID", "message": str(exc)}
                ) from exc
            request.app.state.inventory_cache = None
            return {**result, "correlation_id": body.correlation_id}

    @app.post(
        "/internal/v1/providers/autodl/instances/{product}/{instance_id}/ssh-credentials",
        dependencies=[Depends(authenticated)],
    )
    async def ssh_credentials(
        product: AutoDLProduct,
        instance_id: str,
        current: AutoDLProvider = Depends(provider),  # noqa: B008
    ) -> JSONResponse:
        try:
            result = await current.ssh_credentials(product, instance_id)
        except (AutoDLProviderError, ValueError) as exc:
            if isinstance(exc, AutoDLProviderError):
                raise _provider_http_error(exc) from exc
            raise HTTPException(
                422, detail={"code": "AUTODL_REF_INVALID", "message": str(exc)}
            ) from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store, max-age=0"})

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gpu_control_provider_controller.main:app", host="0.0.0.0", port=8020)
