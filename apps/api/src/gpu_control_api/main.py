import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

import httpx
import jwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.comfy_client import ComfyClient, ComfyError
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import TERMINAL_JOB_STATUSES, JobStatus, NodeMode, Priority
from packages.gpu_control_core.logging import bind_context, configure_logging, logger, reset_context
from packages.gpu_control_core.models import (
    Alert,
    ApiClient,
    ApiKey,
    AuditLog,
    IdempotencyKey,
    Job,
    JobArtifact,
    JobCallback,
    JobEvent,
    Node,
    NodeLease,
    RateLimitPolicy,
    SystemSetting,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import ACTIVE_STATUSES, transition_job
from packages.gpu_control_core.security import (
    create_access_token,
    derive_callback_secret,
    hash_api_secret,
    issue_api_key,
    sign_agent_request,
    validate_callback_url,
    verify_api_key,
    verify_password,
)
from packages.gpu_control_core.settings import Settings, get_settings
from packages.gpu_control_core.storage import (
    LocalJobStorage,
    StorageError,
    inspect_image,
    safe_filename,
)
from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow

REQUESTS = Counter(
    "gpu_control_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
DURATION = Histogram(
    "gpu_control_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)


class Principal(BaseModel):
    id: str
    role: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=1024)


class NodeModeRequest(BaseModel):
    mode: NodeMode
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool


class RetryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool


class ApiKeyCreateRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool


class WorkflowImportRequest(BaseModel):
    workflow_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    version: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    template: dict[str, Any]
    parameter_schema: dict[str, Any]
    bindings: dict[str, str]
    allowed_class_types: list[str] = Field(min_length=1)
    required_models: list[str] = []
    required_custom_nodes: list[str] = []
    min_vram_mb: int = Field(0, ge=0, le=200_000)
    timeout_seconds: int = Field(900, ge=10, le=86_400)
    node_labels: dict[str, str] = {}
    output_nodes: list[str] = Field(min_length=1)


class ClientCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    max_queued: int = Field(20, ge=1, le=10_000)
    max_running: int = Field(1, ge=1, le=10)
    daily_quota: int = Field(1000, ge=1, le=1_000_000)
    weight: int = Field(1, ge=1, le=100)
    callback_hosts: list[str] = []


class SettingUpdateRequest(BaseModel):
    value: int | float | bool | str
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool


class AlertItemRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    fingerprint: str | None = Field(default=None, max_length=128)

    @field_validator("labels", "annotations")
    @classmethod
    def bounded_alert_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64 or any(
            len(key) > 128 or len(item) > 2048 for key, item in value.items()
        ):
            raise ValueError("alert labels/annotations exceed limits")
        return value


class AlertWebhookRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    alerts: list[AlertItemRequest] = Field(max_length=100)


def _request_hash(
    workflow_key: str, version: str, parameters: dict[str, Any], files: list[tuple[str, str]]
) -> str:
    value = {
        "workflow_key": workflow_key,
        "version": version,
        "parameters": parameters,
        "files": sorted(files),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def _request_id(request: Request) -> str:
    value = request.headers.get("x-request-id", "")
    return value if REQUEST_ID_PATTERN.fullmatch(value) else str(uuid.uuid4())


def _validate_parameter_limits(value: dict[str, Any]) -> None:
    keys = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal keys
        if depth > 10:
            raise ValueError("parameters 嵌套层级不能超过 10")
        if isinstance(item, dict):
            keys += len(item)
            if keys > 256:
                raise ValueError("parameters 键数量不能超过 256")
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 1024:
                raise ValueError("parameters 数组元素不能超过 1024")
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


async def _notify(app: FastAPI, channel: str, payload: dict[str, Any]) -> None:
    redis: Redis | None = getattr(app.state, "redis", None)
    if redis is None:
        return
    try:
        await redis.publish(channel, json.dumps(payload))
    except Exception as exc:
        logger().warning(
            "redis_notification_failed",
            event="redis.publish_failed",
            error_code="REDIS_UNAVAILABLE",
            error_type=type(exc).__name__,
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging("api", cfg.environment)
        app.state.settings = cfg
        app.state.db = Database(cfg)
        app.state.storage = LocalJobStorage(cfg.job_root)
        app.state.redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        app.state.tenant_locks = {}
        try:
            await app.state.redis.ping()
        except Exception:
            await app.state.redis.aclose()
            app.state.redis = None
        app.state.alert_delivery_task = asyncio.create_task(alert_delivery_loop(app))
        yield
        app.state.alert_delivery_task.cancel()
        await asyncio.gather(app.state.alert_delivery_task, return_exceptions=True)
        if app.state.redis is not None:
            await app.state.redis.aclose()
        await app.state.db.close()

    app = FastAPI(title="GPU Control API", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = _request_id(request)
        request.state.request_id = request_id
        token = bind_context(request_id=request_id, trace_id=None, event="http.request")
        started = asyncio.get_running_loop().time()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger().exception("unhandled_request_error", error_code="INTERNAL_ERROR")
                response = JSONResponse(
                    {
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "服务器内部错误",
                            "request_id": request_id,
                        }
                    },
                    500,
                )
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            elapsed = asyncio.get_running_loop().time() - started
            REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
            DURATION.labels(request.method, route_path).observe(elapsed)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = "default-src 'self'"
            return response
        finally:
            reset_context(token)

    async def session(request: Request) -> AsyncIterator[AsyncSession]:
        async with request.app.state.db.session() as db_session:
            yield db_session

    async def api_principal(
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> Principal:
        if not x_api_key or not x_api_key.startswith("gpc_"):
            raise HTTPException(
                401, detail={"code": "AUTH_FAILED", "message": "缺少或无效 API Key"}
            )
        parts = x_api_key.split("_", 2)
        if len(parts) != 3:
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "API Key 格式错误"})
        key = await db.scalar(
            select(ApiKey).where(ApiKey.prefix == parts[1], ApiKey.enabled.is_(True))
        )
        if (
            key is None
            or (key.expires_at and key.expires_at <= datetime.now(UTC))
            or not verify_api_key(key.secret_hash, parts[2], cfg.api_key_pepper)
        ):
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "API Key 无效"})
        client = await db.get(ApiClient, key.client_id)
        if client is None or not client.enabled or client.role != "client":
            raise HTTPException(403, detail={"code": "AUTH_FAILED", "message": "客户已停用"})
        policy = await db.scalar(
            select(RateLimitPolicy).where(RateLimitPolicy.client_id == client.id)
        )
        if request.app.state.redis is not None:
            rate = policy.requests_per_second if policy else 5.0
            burst = policy.burst if policy else 10
            script = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
if count > tonumber(ARGV[2]) then return 0 else return 1 end
"""
            try:
                window_ms = max(100, int(1000 / max(rate, 0.1)))
                allowed = await request.app.state.redis.eval(
                    script,
                    1,
                    f"gpu-control:rate:{client.id}:{int(time.time() * 1000) // window_ms}",
                    window_ms * 2,
                    burst,
                )
                if not allowed:
                    raise HTTPException(
                        429, detail={"code": "RATE_LIMITED", "message": "请求频率超过限制"}
                    )
            except HTTPException:
                raise
            except Exception:
                logger().warning(
                    "redis_rate_limit_degraded",
                    event="redis.rate_limit_failed",
                    error_code="REDIS_UNAVAILABLE",
                )
        key.last_used_at = datetime.now(UTC)
        await db.commit()
        return Principal(id=client.id, role="client")

    async def admin_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "需要管理员令牌"})
        try:
            payload = jwt.decode(authorization[7:], cfg.jwt_secret, algorithms=["HS256"])
            principal = Principal(id=str(payload["sub"]), role=str(payload["role"]))
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise HTTPException(
                401, detail={"code": "AUTH_FAILED", "message": "令牌无效或已过期"}
            ) from exc
        if principal.role not in {"admin", "operator", "viewer"}:
            raise HTTPException(403, detail={"code": "AUTH_FAILED", "message": "权限不足"})
        return principal

    def require_operator(principal: Annotated[Principal, Depends(admin_principal)]) -> Principal:
        if principal.role not in {"admin", "operator"}:
            raise HTTPException(403, detail={"code": "AUTH_FAILED", "message": "需要运维权限"})
        return principal

    async def audit(
        db: AsyncSession,
        request: Request,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        result: str = "SUCCESS",
    ) -> None:
        db.add(
            AuditLog(
                actor_id=principal.id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before=before,
                after=after,
                source_ip=request.client.host if request.client else "",
                request_id=str(request.state.request_id),
                result=result,
            )
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, Any]:
        try:
            await request.app.state.db.ping()
        except Exception as exc:
            raise HTTPException(
                503, detail={"code": "DATABASE_ERROR", "message": type(exc).__name__}
            ) from exc
        return {
            "status": "ready",
            "database": "ok",
            "redis": "ok" if request.app.state.redis else "degraded",
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/admin/auth/login")
    async def login(
        body: LoginRequest, db: Annotated[AsyncSession, Depends(session)]
    ) -> dict[str, Any]:
        client = await db.scalar(
            select(ApiClient).where(ApiClient.name == body.username, ApiClient.enabled.is_(True))
        )
        if (
            client is None
            or client.role not in {"admin", "operator", "viewer"}
            or not client.password_hash
            or not verify_password(client.password_hash, body.password)
        ):
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "用户名或密码错误"})
        return {
            "access_token": create_access_token(client.id, client.role, cfg.jwt_secret),
            "token_type": "bearer",
            "expires_in": 900,
            "role": client.role,
        }

    @app.get("/api/v1/workflows")
    async def list_workflows(
        _: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> list[dict[str, Any]]:
        rows = (
            await db.scalars(
                select(WorkflowVersion)
                .where(WorkflowVersion.enabled.is_(True))
                .order_by(WorkflowVersion.workflow_key)
            )
        ).all()
        return [
            {
                "workflow_key": row.workflow_key,
                "version": row.version,
                "parameter_schema": row.parameter_schema,
                "timeout_seconds": row.timeout_seconds,
            }
            for row in rows
        ]

    async def _create_job(
        request: Request,
        workflow_key: str,
        workflow_version: str,
        parameters_raw: str,
        priority: Priority,
        idempotency_key: str | None,
        principal: Principal,
        db: AsyncSession,
        input_image: UploadFile | None,
        mask: UploadFile | None,
        callback_url: str | None,
    ) -> JSONResponse:
        if len(parameters_raw.encode("utf-8")) > 65_536:
            raise HTTPException(
                422, detail={"code": "INPUT_INVALID", "message": "parameters 不能超过 64 KiB"}
            )
        try:
            parameters = json.loads(parameters_raw)
            if not isinstance(parameters, dict):
                raise ValueError
            _validate_parameter_limits(parameters)
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                422,
                detail={
                    "code": "INPUT_INVALID",
                    "message": str(exc) or "parameters 必须是 JSON 对象",
                },
            ) from exc
        if priority == Priority.CRITICAL:
            raise HTTPException(
                403,
                detail={"code": "PRIORITY_FORBIDDEN", "message": "业务 API 不能直接提交 CRITICAL"},
            )
        await request.app.state.db.acquire_tenant_transaction_lock(db, principal.id)
        workflow = await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_key == workflow_key,
                WorkflowVersion.version == workflow_version,
                WorkflowVersion.enabled.is_(True),
            )
        )
        if workflow is None:
            raise HTTPException(
                404, detail={"code": "WORKFLOW_NOT_FOUND", "message": "工作流版本不存在或未启用"}
            )
        queued = await db.scalar(
            select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value)
        )
        tenant_queued = await db.scalar(
            select(func.count(Job.id)).where(
                Job.tenant_id == principal.id, Job.status == JobStatus.QUEUED.value
            )
        )
        client = await db.get(ApiClient, principal.id)
        if callback_url:
            allowed_hosts = {
                str(host).lower() for host in (client.callback_hosts if client else [])
            }
            if cfg.callback_hosts:
                allowed_hosts &= cfg.callback_hosts
            if not validate_callback_url(callback_url, allowed_hosts):
                raise HTTPException(
                    422,
                    detail={
                        "code": "CALLBACK_URL_REJECTED",
                        "message": "回调地址必须是已批准的 HTTPS 域名",
                    },
                )
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        daily = await db.scalar(
            select(func.count(Job.id)).where(Job.tenant_id == principal.id, Job.created_at >= today)
        )
        if client and int(daily or 0) >= client.daily_quota:
            raise HTTPException(
                429, detail={"code": "RATE_LIMITED", "message": "今日任务配额已用完"}
            )
        if int(queued or 0) >= cfg.system_max_queued or int(tenant_queued or 0) >= (
            client.max_queued if client else cfg.default_tenant_max_queued
        ):
            raise HTTPException(429, detail={"code": "RATE_LIMITED", "message": "队列已达到限制"})
        job_id = str(uuid.uuid4())
        storage: LocalJobStorage = request.app.state.storage
        job_now = datetime.now(UTC)
        root = storage.create_staging_layout(job_id)
        file_hashes: list[tuple[str, str]] = []
        image_dimensions: dict[str, tuple[int, int]] = {}
        for field_name, upload in (("image", input_image), ("mask", mask)):
            if upload is None:
                continue
            name = safe_filename(upload.filename or f"{field_name}.bin")
            destination = root / "input" / f"{field_name}-{name}"

            async def chunks(source: UploadFile) -> AsyncIterator[bytes]:
                while chunk := await source.read(1024 * 1024):
                    yield chunk

            try:
                size, digest = await storage.stream_to_file(
                    chunks(upload), destination, cfg.max_upload_bytes
                )
            except StorageError as exc:
                storage.remove_tree(root)
                raise HTTPException(
                    422, detail={"code": "INPUT_INVALID", "message": str(exc)}
                ) from exc
            try:
                width, height, image_format = inspect_image(destination, cfg.max_image_pixels)
            except StorageError as exc:
                storage.remove_tree(root)
                raise HTTPException(
                    422, detail={"code": "INPUT_INVALID", "message": str(exc)}
                ) from exc
            file_hashes.append((field_name, digest))
            image_dimensions[field_name] = (width, height)
            parameters[f"{field_name}_filename"] = destination.name
            storage.atomic_json(
                root / "input" / f"{field_name}.metadata.json",
                {
                    "filename": destination.name,
                    "size_bytes": size,
                    "sha256": digest,
                    "content_type": upload.content_type,
                    "width": width,
                    "height": height,
                    "format": image_format,
                },
            )
        if (
            "image" in image_dimensions
            and "mask" in image_dimensions
            and image_dimensions["image"] != image_dimensions["mask"]
        ):
            storage.remove_tree(root)
            raise HTTPException(
                422,
                detail={"code": "INPUT_INVALID", "message": "蒙版尺寸必须与输入图片一致"},
            )
        manifest = WorkflowManifest(
            workflow_key=workflow.workflow_key,
            version=workflow.version,
            template_file="database",
            parameter_schema=workflow.parameter_schema,
            bindings={str(k): str(v) for k, v in workflow.bindings.items()},
            allowed_class_types=frozenset(str(v) for v in workflow.allowed_class_types),
            required_models=tuple(str(v) for v in workflow.required_models),
            required_custom_nodes=tuple(str(v) for v in workflow.required_custom_nodes),
            min_vram_mb=workflow.min_vram_mb,
            timeout_seconds=workflow.timeout_seconds,
            node_labels={str(k): str(v) for k, v in workflow.node_labels.items()},
            output_nodes=tuple(str(v) for v in workflow.output_nodes),
            enabled=workflow.enabled,
        )
        try:
            rendered = render_workflow(manifest, workflow.template, parameters)
        except Exception as exc:
            storage.remove_tree(root)
            raise HTTPException(
                422, detail={"code": "WORKFLOW_RENDER_FAILED", "message": str(exc)}
            ) from exc
        request_hash = _request_hash(workflow_key, workflow_version, parameters, file_hashes)
        if idempotency_key:
            existing = await db.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.client_id == principal.id, IdempotencyKey.key == idempotency_key
                )
            )
            expires_at = existing.expires_at if existing else None
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if existing and expires_at and expires_at <= datetime.now(UTC):
                await db.delete(existing)
                await db.flush()
                existing = None
            if existing:
                if existing.request_hash != request_hash:
                    storage.remove_tree(root)
                    raise HTTPException(
                        409,
                        detail={
                            "code": "IDEMPOTENCY_CONFLICT",
                            "message": "相同 Idempotency-Key 的请求内容不同",
                        },
                    )
                storage.remove_tree(root)
                return JSONResponse(
                    {
                        "job_id": existing.job_id,
                        "status": "existing",
                        "status_url": f"/api/v1/jobs/{existing.job_id}",
                        "events_url": f"/api/v1/jobs/{existing.job_id}/events",
                    },
                    200,
                )
        trace_id = uuid.uuid4().hex
        request_id = str(request.state.request_id)
        root = storage.promote_staging(root, job_id, job_now)
        storage.atomic_json(
            root / "request.sanitized.json",
            {
                "workflow_key": workflow_key,
                "workflow_version": workflow_version,
                "parameter_names": sorted(parameters),
                "file_hashes": file_hashes,
            },
        )
        storage.atomic_json(root / "request.private.json", {"parameters": parameters}, private=True)
        storage.atomic_json(root / "workflow" / "template.snapshot.json", workflow.template)
        storage.atomic_json(root / "workflow" / "rendered.api.json", rendered)
        job = Job(
            id=job_id,
            tenant_id=principal.id,
            workflow_key=workflow_key,
            workflow_version=workflow_version,
            status=JobStatus.RECEIVED.value,
            priority=priority.value,
            parameters=parameters,
            request_hash=request_hash,
            request_id=request_id,
            trace_id=trace_id,
            job_dir=str(root),
            max_attempts=cfg.job_max_attempts,
        )
        db.add(job)
        await db.flush()
        await transition_job(db, job, JobStatus.VALIDATING, "api.validated")
        await transition_job(db, job, JobStatus.QUEUED, "api.queued")
        if idempotency_key:
            db.add(
                IdempotencyKey(
                    client_id=principal.id,
                    key=idempotency_key,
                    request_hash=request_hash,
                    job_id=job_id,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
        callback_secret: str | None = None
        if callback_url:
            callback_id = str(uuid.uuid4())
            callback_secret = derive_callback_secret(callback_id, cfg.api_key_pepper)
            db.add(
                JobCallback(
                    id=callback_id,
                    job_id=job_id,
                    url=callback_url,
                    signing_secret_hash=hash_api_secret(callback_secret, cfg.api_key_pepper),
                    next_attempt_at=datetime.now(UTC),
                )
            )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            storage.remove_tree(root)
            if not idempotency_key:
                raise
            existing = await db.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.client_id == principal.id,
                    IdempotencyKey.key == idempotency_key,
                    IdempotencyKey.expires_at > datetime.now(UTC),
                )
            )
            if existing is None or existing.request_hash != request_hash:
                raise HTTPException(
                    409,
                    detail={"code": "IDEMPOTENCY_CONFLICT", "message": "幂等请求发生冲突"},
                ) from exc
            return JSONResponse(
                {
                    "job_id": existing.job_id,
                    "status": "existing",
                    "status_url": f"/api/v1/jobs/{existing.job_id}",
                    "events_url": f"/api/v1/jobs/{existing.job_id}/events",
                },
                200,
            )
        await _notify(request.app, "gpu-control:wakeup", {"event": "job.queued", "job_id": job_id})
        payload = {
            "job_id": job_id,
            "status": JobStatus.QUEUED.value,
            "status_url": f"/api/v1/jobs/{job_id}",
            "events_url": f"/api/v1/jobs/{job_id}/events",
            "queue_position": int(queued or 0) + 1,
            "eta_seconds": None,
        }
        if callback_secret:
            payload["callback_secret"] = callback_secret
            payload["callback_secret_warning"] = "仅显示一次，请立即安全保存"  # noqa: S105
        return JSONResponse(payload, 202)

    @app.post("/api/v1/jobs")
    async def create_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        workflow_key: Annotated[str, Form(min_length=1, max_length=128)],
        workflow_version: Annotated[str, Form(min_length=1, max_length=64)],
        parameters: Annotated[str, Form()] = "{}",
        priority: Annotated[Priority, Form()] = Priority.NORMAL,
        input_image: Annotated[UploadFile | None, File()] = None,
        mask: Annotated[UploadFile | None, File()] = None,
        callback_url: Annotated[str | None, Form(max_length=2048)] = None,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> JSONResponse:
        tenant_lock = request.app.state.tenant_locks.setdefault(principal.id, asyncio.Lock())
        async with tenant_lock:
            return await _create_job(
                request,
                workflow_key,
                workflow_version,
                parameters,
                priority,
                idempotency_key,
                principal,
                db,
                input_image,
                mask,
                callback_url,
            )

    @app.post("/api/v1/jobs/inpaint")
    async def create_inpaint_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        parameters: Annotated[str, Form()] = "{}",
        input_image: Annotated[UploadFile | None, File()] = None,
        mask: Annotated[UploadFile | None, File()] = None,
        callback_url: Annotated[str | None, Form(max_length=2048)] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        tenant_lock = request.app.state.tenant_locks.setdefault(principal.id, asyncio.Lock())
        async with tenant_lock:
            return await _create_job(
                request,
                "inpaint",
                "1",
                parameters,
                Priority.NORMAL,
                idempotency_key,
                principal,
                db,
                input_image,
                mask,
                callback_url,
            )

    async def owned_job(job_id: str, principal: Principal, db: AsyncSession) -> Job:
        job = await db.get(Job, job_id)
        if job is None or job.tenant_id != principal.id:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"})
        return job

    def job_payload(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "status": job.status,
            "workflow_key": job.workflow_key,
            "workflow_version": job.workflow_version,
            "priority": job.priority,
            "node_id": job.node_id,
            "prompt_id": job.prompt_id,
            "progress": job.progress,
            "attempt": job.attempt_count,
            "error": {"code": job.error_code, "message": job.error_message}
            if job.error_code
            else None,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        return job_payload(await owned_job(job_id, principal, db))

    @app.get("/api/v1/jobs/{job_id}/events")
    async def job_events(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> StreamingResponse:
        await owned_job(job_id, principal, db)

        async def stream() -> AsyncIterator[str]:
            sequence = 0
            while True:
                async with app.state.db.session() as event_db:
                    events = (
                        await event_db.scalars(
                            select(JobEvent)
                            .where(JobEvent.job_id == job_id, JobEvent.sequence > sequence)
                            .order_by(JobEvent.sequence)
                        )
                    ).all()
                    terminal = False
                    for item in events:
                        sequence = item.sequence
                        yield f"id: {sequence}\nevent: job\ndata: {json.dumps({'status': item.status, 'event': item.event, 'details': item.details})}\n\n"
                        terminal = JobStatus(item.status) in TERMINAL_JOB_STATUSES
                    if terminal:
                        return
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/jobs/{job_id}/artifacts")
    async def artifacts(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> list[dict[str, Any]]:
        await owned_job(job_id, principal, db)
        rows = (await db.scalars(select(JobArtifact).where(JobArtifact.job_id == job_id))).all()
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "content_type": row.content_type,
                "size_bytes": row.size_bytes,
                "sha256": row.sha256,
            }
            for row in rows
        ]

    @app.get("/api/v1/jobs/{job_id}/artifacts/{artifact_id}")
    async def artifact_file(
        job_id: str,
        artifact_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> FileResponse:
        job = await owned_job(job_id, principal, db)
        artifact = await db.scalar(
            select(JobArtifact).where(JobArtifact.id == artifact_id, JobArtifact.job_id == job_id)
        )
        if artifact is None:
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        path = (Path(job.job_dir) / artifact.relative_path).resolve()
        if Path(job.job_dir).resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        return FileResponse(path, media_type=artifact.content_type, filename=path.name)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        job = await owned_job(job_id, principal, db)
        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
            return job_payload(job)
        if job.status == JobStatus.QUEUED.value:
            await transition_job(db, job, JobStatus.CANCELLED, "api.cancelled")
        else:
            job.cancel_requested = True
            if job.status not in {JobStatus.CANCELLING.value}:
                await transition_job(db, job, JobStatus.CANCELLING, "api.cancel_requested")
        await db.commit()
        await _notify(request.app, "gpu-control:wakeup", {"event": "job.cancel", "job_id": job.id})
        return job_payload(job)

    @app.get("/admin/dashboard")
    async def dashboard(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (await db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))).all()
        counts: dict[str, int] = {str(status): int(count) for status, count in rows}
        today_rows = (
            await db.execute(
                select(Job.status, func.count(Job.id))
                .where(Job.created_at >= today)
                .group_by(Job.status)
            )
        ).all()
        today_counts = {str(status): int(count) for status, count in today_rows}
        for terminal in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value):
            counts[terminal] = today_counts.get(terminal, 0)
        oldest = await db.scalar(
            select(func.min(Job.created_at)).where(Job.status == JobStatus.QUEUED.value)
        )
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        oldest_wait_seconds = max(0, int((now - oldest).total_seconds())) if oldest else 0
        durations = list(
            (
                await db.scalars(
                    select(Job)
                    .where(
                        Job.status == JobStatus.SUCCEEDED.value,
                        Job.started_at.is_not(None),
                        Job.finished_at.is_not(None),
                    )
                    .order_by(Job.finished_at.desc())
                    .limit(50)
                )
            ).all()
        )
        duration_values = [
            max(0, (job.finished_at - job.started_at).total_seconds())
            for job in durations
            if job.finished_at is not None and job.started_at is not None
        ]
        nodes = (await db.scalars(select(Node).order_by(Node.pool, Node.id))).all()
        workers = sum(
            node.pool == "PRIMARY"
            and node.mode == NodeMode.ACTIVE.value
            and node.health == "ONLINE"
            for node in nodes
        )
        average_duration = sum(duration_values) / len(duration_values) if duration_values else 0
        estimated_clear_seconds = (
            int(counts.get(JobStatus.QUEUED.value, 0) * average_duration / workers)
            if workers and average_duration
            else None
        )
        trend_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
        created_times = list(
            (await db.scalars(select(Job.created_at).where(Job.created_at >= trend_start))).all()
        )
        buckets = []
        for offset in range(7):
            start = trend_start + timedelta(hours=offset)
            end = start + timedelta(hours=1)
            count = 0
            for created in created_times:
                stamp = created if created.tzinfo else created.replace(tzinfo=UTC)
                count += start <= stamp < end
            buckets.append({"label": start.strftime("%H:00"), "value": count})
        active_alerts = list(
            (
                await db.scalars(
                    select(Alert)
                    .where(Alert.status == "firing")
                    .order_by(Alert.updated_at.desc())
                    .limit(5)
                )
            ).all()
        )
        return {
            "jobs": counts,
            "oldest_wait_seconds": oldest_wait_seconds,
            "estimated_clear_seconds": estimated_clear_seconds,
            "submission_trend": buckets,
            "active_alerts": [
                {
                    "id": alert.id,
                    "severity": alert.severity,
                    "name": alert.labels.get("alertname", "GPU Control"),
                    "summary": alert.annotations.get("summary", ""),
                }
                for alert in active_alerts
            ],
            "nodes": [
                {
                    "id": n.id,
                    "pool": n.pool,
                    "mode": n.mode,
                    "health": n.health,
                    "current_jobs": n.current_jobs,
                    "gpu_util_percent": n.gpu_util_percent,
                    "free_vram_mb": n.free_vram_mb,
                }
                for n in nodes
            ],
        }

    @app.get("/admin/jobs")
    async def admin_jobs(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = select(Job).order_by(Job.created_at.desc()).limit(min(max(limit, 1), 500))
        if status:
            query = query.where(Job.status == status)
        return [job_payload(row) for row in (await db.scalars(query)).all()]

    @app.post("/admin/jobs/{job_id}/pin")
    async def pin_job(
        job_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        job = await db.get(Job, job_id, with_for_update=True)
        if job is None or job.status != JobStatus.QUEUED.value:
            raise HTTPException(409, detail={"code": "JOB_NOT_PINNABLE"})
        before = {"pinned": job.pinned}
        job.pinned = True
        await audit(
            db,
            request,
            principal,
            "job.pin",
            "job",
            job_id,
            before,
            {"pinned": True, "reason": body.reason},
        )
        await db.commit()
        await _notify(request.app, "gpu-control:wakeup", {"event": "job.pin", "job_id": job_id})
        return job_payload(job)

    @app.get("/admin/nodes")
    async def admin_nodes(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> list[dict[str, Any]]:
        rows = (await db.scalars(select(Node).order_by(Node.pool, Node.id))).all()
        return [
            {column.name: getattr(row, column.name) for column in Node.__table__.columns}
            for row in rows
        ]

    async def prepare_idle_maintenance(
        node_id: str,
        action: str,
        reason: str,
        request: Request,
        principal: Principal,
        db: AsyncSession,
    ) -> Node:
        node = await db.scalar(select(Node).where(Node.id == node_id).with_for_update())
        if node is None:
            raise HTTPException(404, detail={"code": "NODE_NOT_FOUND"})
        before = {
            "mode": node.mode,
            "current_jobs": node.current_jobs,
            "manual_reserved": node.manual_reserved,
        }
        node.mode = NodeMode.DRAINING.value
        node.manual_reserved = False
        active_leases = int(
            await db.scalar(
                select(func.count(NodeLease.id)).where(
                    NodeLease.node_id == node_id, NodeLease.active.is_(True)
                )
            )
            or 0
        )
        await audit(
            db,
            request,
            principal,
            f"node.maintenance.{action}",
            "node",
            node_id,
            before,
            {"mode": node.mode, "active_leases": active_leases, "reason": reason},
        )
        await db.commit()
        if active_leases or node.current_jobs:
            raise HTTPException(
                409,
                detail={
                    "code": "NODE_DRAINING",
                    "message": "节点已进入 DRAINING；等待活动任务结束后再次执行操作",
                },
            )
        return node

    async def call_node_agent(node: Node, action: str) -> dict[str, Any]:
        if not node.agent_url:
            raise HTTPException(404, detail={"code": "NODE_AGENT_UNAVAILABLE"})
        payload = json.dumps({"action": action, "lines": 200}, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        path = "/v1/operations"
        signature = sign_agent_request(
            "POST", path, payload, timestamp, nonce, cfg.node_agent_secret(node.id)
        )
        try:
            async with httpx.AsyncClient(timeout=75) as client:
                response = await client.post(
                    node.agent_url.rstrip("/") + path,
                    content=payload,
                    headers={
                        "content-type": "application/json",
                        "x-gpu-timestamp": timestamp,
                        "x-gpu-nonce": nonce,
                        "x-gpu-signature": signature,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                502,
                detail={"code": "NODE_AGENT_REQUEST_FAILED", "message": str(exc)},
            ) from exc
        raw_result = response.json()
        if not isinstance(raw_result, dict):
            raise HTTPException(502, detail={"code": "NODE_AGENT_INVALID_RESPONSE"})
        return raw_result

    @app.put("/admin/nodes/{node_id}/mode")
    async def change_node_mode(
        node_id: str,
        body: NodeModeRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        node = await db.get(Node, node_id, with_for_update=True)
        if node is None:
            raise HTTPException(404, detail={"code": "NODE_NOT_FOUND"})
        before = {"mode": node.mode, "manual_reserved": node.manual_reserved}
        node.mode = body.mode.value
        node.manual_reserved = body.mode == NodeMode.RESERVED
        await audit(
            db,
            request,
            principal,
            "node.mode.change",
            "node",
            node_id,
            before,
            {"mode": node.mode, "reason": body.reason},
        )
        await db.commit()
        await _notify(request.app, "gpu-control:wakeup", {"event": "node.mode", "node_id": node_id})
        return {"id": node.id, "mode": node.mode}

    @app.post("/admin/nodes/{node_id}/reserve")
    async def reserve_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        return await change_node_mode(
            node_id,
            NodeModeRequest(mode=NodeMode.RESERVED, reason=body.reason, confirm=body.confirm),
            request,
            principal,
            db,
        )

    @app.post("/admin/nodes/{node_id}/release")
    async def release_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        node = await db.get(Node, node_id)
        target = NodeMode.ACTIVE if node and node.pool == "PRIMARY" else NodeMode.OVERFLOW
        return await change_node_mode(
            node_id,
            NodeModeRequest(mode=target, reason=body.reason, confirm=body.confirm),
            request,
            principal,
            db,
        )

    @app.post("/admin/nodes/{node_id}/free")
    async def free_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        node = await prepare_idle_maintenance(node_id, "free", body.reason, request, principal, db)
        try:
            async with ComfyClient(node.base_url) as client:
                result = await client.free()
        except ComfyError as exc:
            raise HTTPException(502, detail={"code": exc.code, "message": str(exc)}) from exc
        await audit(db, request, principal, "node.models.free", "node", node_id, {}, result)
        await db.commit()
        return result

    @app.post("/admin/nodes/{node_id}/interrupt")
    async def interrupt_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        node = await db.scalar(select(Node).where(Node.id == node_id).with_for_update())
        if node is None:
            raise HTTPException(404, detail={"code": "NODE_NOT_FOUND"})
        before = {"mode": node.mode, "current_jobs": node.current_jobs}
        node.mode = NodeMode.DRAINING.value
        node.manual_reserved = False
        jobs = list(
            (
                await db.scalars(
                    select(Job)
                    .where(Job.node_id == node_id, Job.status.in_(ACTIVE_STATUSES))
                    .with_for_update()
                )
            ).all()
        )
        for job in jobs:
            job.cancel_requested = True
            if job.status not in {JobStatus.CANCELLING.value, JobStatus.DOWNLOADING.value}:
                await transition_job(db, job, JobStatus.CANCELLING, "admin.node_interrupt")
        await audit(
            db,
            request,
            principal,
            "node.interrupt",
            "node",
            node_id,
            before,
            {"mode": node.mode, "jobs": [job.id for job in jobs], "reason": body.reason},
        )
        await db.commit()
        try:
            async with ComfyClient(node.base_url) as client:
                result = await client.interrupt()
        except ComfyError as exc:
            raise HTTPException(502, detail={"code": exc.code, "message": str(exc)}) from exc
        return result

    @app.post("/admin/nodes/{node_id}/restart")
    async def restart_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        node = await prepare_idle_maintenance(
            node_id, "restart", body.reason, request, principal, db
        )
        result = await call_node_agent(node, "restart")
        await audit(
            db,
            request,
            principal,
            "node.restart",
            "node",
            node_id,
            {},
            {"reason": body.reason, "exit_code": result.get("exit_code")},
        )
        await db.commit()
        return result

    @app.post("/admin/nodes/{node_id}/start")
    async def start_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        node = await db.get(Node, node_id)
        if node is None:
            raise HTTPException(404, detail={"code": "NODE_NOT_FOUND"})
        result = await call_node_agent(node, "start")
        await audit(
            db, request, principal, "node.start", "node", node_id, {}, {"reason": body.reason}
        )
        await db.commit()
        return result

    @app.post("/admin/nodes/{node_id}/stop")
    async def stop_node(
        node_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        node = await prepare_idle_maintenance(node_id, "stop", body.reason, request, principal, db)
        result = await call_node_agent(node, "stop")
        await audit(
            db, request, principal, "node.stop", "node", node_id, {}, {"reason": body.reason}
        )
        await db.commit()
        return result

    @app.post("/admin/jobs/{job_id}/retry")
    async def retry_job(
        job_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        job = await db.get(Job, job_id, with_for_update=True)
        if (
            job is None
            or job.status not in {JobStatus.FAILED.value, JobStatus.TIMED_OUT.value}
            or job.attempt_count >= job.max_attempts
        ):
            raise HTTPException(409, detail={"code": "JOB_NOT_RETRYABLE"})
        before = {"status": job.status, "attempt": job.attempt_count}
        await transition_job(db, job, JobStatus.RETRY_WAIT, "admin.retry", {"reason": body.reason})
        await transition_job(db, job, JobStatus.QUEUED, "admin.requeued")
        job.error_code = None
        job.error_message = None
        await audit(
            db, request, principal, "job.retry", "job", job_id, before, {"status": job.status}
        )
        await db.commit()
        await _notify(request.app, "gpu-control:wakeup", {"event": "job.retry", "job_id": job_id})
        return job_payload(job)

    @app.post("/admin/jobs/{job_id}/cancel")
    async def admin_cancel_job(
        job_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        job = await db.get(Job, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
        before = {"status": job.status, "cancel_requested": job.cancel_requested}
        if JobStatus(job.status) not in TERMINAL_JOB_STATUSES:
            if job.status == JobStatus.QUEUED.value:
                await transition_job(db, job, JobStatus.CANCELLED, "admin.cancelled")
            else:
                job.cancel_requested = True
                if job.status not in {
                    JobStatus.CANCELLING.value,
                    JobStatus.DOWNLOADING.value,
                }:
                    await transition_job(db, job, JobStatus.CANCELLING, "admin.cancel_requested")
        await audit(
            db,
            request,
            principal,
            "job.cancel",
            "job",
            job_id,
            before,
            {"status": job.status, "reason": body.reason},
        )
        await db.commit()
        await _notify(request.app, "gpu-control:wakeup", {"event": "job.cancel", "job_id": job.id})
        return job_payload(job)

    @app.get("/admin/audit-logs")
    async def audit_logs(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (
            await db.scalars(
                select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 500))
            )
        ).all()
        return [
            {column.name: getattr(row, column.name) for column in AuditLog.__table__.columns}
            for row in rows
        ]

    @app.post("/admin/clients/{client_id}/keys")
    async def create_key(
        client_id: str,
        body: ApiKeyCreateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        target = await db.get(ApiClient, client_id)
        if target is None:
            raise HTTPException(404, detail={"code": "CLIENT_NOT_FOUND"})
        if target.role != "client":
            raise HTTPException(
                409,
                detail={"code": "API_KEY_ROLE_REJECTED", "message": "API Key 只能绑定业务客户"},
            )
        plaintext, prefix, secret = issue_api_key()
        key = ApiKey(
            id=str(uuid.uuid4()),
            client_id=client_id,
            prefix=prefix,
            secret_hash=hash_api_secret(secret, cfg.api_key_pepper),
        )
        db.add(key)
        await audit(
            db,
            request,
            principal,
            "api_key.create",
            "api_client",
            client_id,
            {},
            {"prefix": prefix, "reason": body.reason},
        )
        await db.commit()
        return {"api_key": plaintext, "prefix": prefix, "warning": "仅显示一次，请立即安全保存"}

    @app.get("/admin/clients")
    async def list_clients(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> list[dict[str, Any]]:
        rows = (await db.scalars(select(ApiClient).order_by(ApiClient.created_at.desc()))).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "role": row.role,
                "enabled": row.enabled,
                "max_queued": row.max_queued,
                "max_running": row.max_running,
                "daily_quota": row.daily_quota,
                "weight": row.weight,
                "callback_hosts": row.callback_hosts,
            }
            for row in rows
        ]

    @app.post("/admin/clients")
    async def create_client(
        body: ClientCreateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if await db.get(ApiClient, body.id):
            raise HTTPException(409, detail={"code": "CLIENT_EXISTS"})
        for host in body.callback_hosts:
            if any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
                for char in host
            ):
                raise HTTPException(
                    422, detail={"code": "INPUT_INVALID", "message": "回调域名格式错误"}
                )
        client = ApiClient(
            id=body.id,
            name=body.name,
            role="client",
            max_queued=body.max_queued,
            max_running=body.max_running,
            daily_quota=body.daily_quota,
            weight=body.weight,
            callback_hosts=body.callback_hosts,
        )
        db.add(client)
        db.add(RateLimitPolicy(client_id=body.id, requests_per_second=5, burst=10))
        await audit(
            db, request, principal, "client.create", "api_client", body.id, {}, body.model_dump()
        )
        await db.commit()
        return {"id": client.id, "name": client.name}

    @app.get("/admin/workflows")
    async def admin_workflows(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> list[dict[str, Any]]:
        rows = (
            await db.scalars(
                select(WorkflowVersion).order_by(
                    WorkflowVersion.workflow_key, WorkflowVersion.created_at.desc()
                )
            )
        ).all()
        return [
            {
                "id": row.id,
                "workflow_key": row.workflow_key,
                "version": row.version,
                "enabled": row.enabled,
                "min_vram_mb": row.min_vram_mb,
                "timeout_seconds": row.timeout_seconds,
                "required_models": row.required_models,
                "required_custom_nodes": row.required_custom_nodes,
                "template_sha256": row.template_sha256,
            }
            for row in rows
        ]

    async def refresh_workflow_compatibility(
        db: AsyncSession, version: WorkflowVersion
    ) -> list[dict[str, Any]]:
        nodes = list((await db.scalars(select(Node).order_by(Node.id))).all())
        results: list[dict[str, Any]] = []
        for node in nodes:
            reasons: list[str] = []
            if node.total_vram_mb < version.min_vram_mb:
                reasons.append(
                    f"vram {node.total_vram_mb}MB < required {version.min_vram_mb}MB"
                )
            for key, value in version.node_labels.items():
                if str(node.labels.get(key)) != str(value):
                    reasons.append(f"label {key} must equal {value}")
            compatibility = await db.scalar(
                select(WorkflowNodeCompatibility).where(
                    WorkflowNodeCompatibility.workflow_version_id == version.id,
                    WorkflowNodeCompatibility.node_id == node.id,
                )
            )
            if compatibility is None:
                compatibility = WorkflowNodeCompatibility(
                    workflow_version_id=version.id,
                    node_id=node.id,
                    compatible=not reasons,
                    reasons=reasons,
                )
                db.add(compatibility)
            else:
                compatibility.compatible = not reasons
                compatibility.reasons = reasons
                compatibility.checked_at = datetime.now(UTC)
            results.append(
                {"node_id": node.id, "compatible": compatibility.compatible, "reasons": reasons}
            )
        return results

    @app.post("/admin/workflows")
    async def import_workflow(
        body: WorkflowImportRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        from packages.gpu_control_core.workflow import template_digest, validate_api_workflow

        try:
            validate_api_workflow(body.template, frozenset(body.allowed_class_types))
        except Exception as exc:
            raise HTTPException(
                422, detail={"code": "WORKFLOW_RENDER_FAILED", "message": str(exc)}
            ) from exc
        if await db.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_key == body.workflow_key,
                WorkflowVersion.version == body.version,
            )
        ):
            raise HTTPException(409, detail={"code": "WORKFLOW_VERSION_EXISTS"})
        if await db.get(Workflow, body.workflow_key) is None:
            db.add(Workflow(key=body.workflow_key, display_name=body.display_name, description=""))
            await db.flush()
        version = WorkflowVersion(
            workflow_key=body.workflow_key,
            version=body.version,
            template=body.template,
            parameter_schema=body.parameter_schema,
            bindings=body.bindings,
            allowed_class_types=body.allowed_class_types,
            required_models=body.required_models,
            required_custom_nodes=body.required_custom_nodes,
            min_vram_mb=body.min_vram_mb,
            timeout_seconds=body.timeout_seconds,
            node_labels=body.node_labels,
            output_nodes=body.output_nodes,
            enabled=False,
            template_sha256=template_digest(body.template),
        )
        db.add(version)
        await db.flush()
        compatibility = await refresh_workflow_compatibility(db, version)
        await audit(
            db,
            request,
            principal,
            "workflow.import",
            "workflow",
            f"{body.workflow_key}:{body.version}",
            {},
            {"enabled": False},
        )
        await db.commit()
        return {
            "id": version.id,
            "workflow_key": version.workflow_key,
            "version": version.version,
            "enabled": False,
            "compatibility": compatibility,
        }

    @app.put("/admin/workflows/{version_id}/enabled")
    async def enable_workflow(
        version_id: int,
        enabled: bool,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        version = await db.get(WorkflowVersion, version_id)
        if version is None:
            raise HTTPException(404, detail={"code": "WORKFLOW_NOT_FOUND"})
        before = {"enabled": version.enabled}
        compatibility = await refresh_workflow_compatibility(db, version)
        if enabled and not any(item["compatible"] for item in compatibility):
            raise HTTPException(
                409,
                detail={
                    "code": "WORKFLOW_NO_COMPATIBLE_NODE",
                    "message": "没有满足显存和标签条件的节点",
                    "compatibility": compatibility,
                },
            )
        version.enabled = enabled
        await audit(
            db,
            request,
            principal,
            "workflow.enable" if enabled else "workflow.disable",
            "workflow",
            f"{version.workflow_key}:{version.version}",
            before,
            {"enabled": enabled, "reason": body.reason},
        )
        await db.commit()
        return {"id": version.id, "enabled": version.enabled}

    setting_bounds: dict[str, tuple[float, float]] = {
        "overflow_queue_threshold": (1, 100000),
        "overflow_wait_threshold_seconds": (1, 86400),
        "overflow_4090_max_gpu_util_percent": (0, 100),
        "overflow_4090_min_free_vram_mb": (0, 200000),
    }
    setting_extra = {"overflow_4090_auto_enabled", "overflow_4090_allowed_windows"}

    @app.get("/admin/settings")
    async def admin_settings(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        stored = {
            item.key: item.value.get("value")
            for item in (await db.scalars(select(SystemSetting))).all()
        }
        return {
            key: stored.get(key, getattr(cfg, key, None))
            for key in set(setting_bounds) | setting_extra
        }

    @app.put("/admin/settings/{key}")
    async def update_setting(
        key: str,
        body: SettingUpdateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        if key in setting_bounds:
            if isinstance(body.value, bool) or not isinstance(body.value, int | float):
                raise HTTPException(422, detail={"code": "INPUT_INVALID"})
            low, high = setting_bounds[key]
            if not low <= float(body.value) <= high:
                raise HTTPException(
                    422, detail={"code": "INPUT_INVALID", "message": f"允许范围 {low}..{high}"}
                )
        elif key == "overflow_4090_auto_enabled":
            if not isinstance(body.value, bool):
                raise HTTPException(422, detail={"code": "INPUT_INVALID"})
        elif key == "overflow_4090_allowed_windows":
            if not isinstance(body.value, str) or len(body.value) > 256:
                raise HTTPException(422, detail={"code": "INPUT_INVALID"})
            try:
                _ = cfg.model_copy(
                    update={"overflow_4090_allowed_windows": body.value}
                ).overflow_windows
            except ValueError as exc:
                raise HTTPException(
                    422, detail={"code": "INPUT_INVALID", "message": str(exc)}
                ) from exc
        else:
            raise HTTPException(422, detail={"code": "INPUT_INVALID"})
        setting = await db.get(SystemSetting, key)
        before = {"value": setting.value.get("value")} if setting else {}
        if setting is None:
            setting = SystemSetting(key=key, value={"value": body.value}, updated_by=principal.id)
            db.add(setting)
        else:
            setting.value = {"value": body.value}
            setting.version += 1
            setting.updated_by = principal.id
        await audit(
            db,
            request,
            principal,
            "setting.update",
            "setting",
            key,
            before,
            {"value": body.value, "reason": body.reason},
        )
        await db.commit()
        return {"key": key, "value": body.value, "version": setting.version}

    @app.get("/admin/alerts")
    async def list_alerts(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (
            await db.scalars(
                select(Alert).order_by(Alert.updated_at.desc()).limit(min(max(limit, 1), 500))
            )
        ).all()
        return [
            {column.name: getattr(row, column.name) for column in Alert.__table__.columns}
            for row in rows
        ]

    @app.get("/admin/jobs/{job_id}/diagnostics")
    async def diagnostics(
        job_id: str,
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> FileResponse:
        job = await db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
        root = Path(job.job_dir).resolve()
        destination = root / "diagnostics" / f"{job_id}.zip"
        allowed = [
            root / "request.sanitized.json",
            root / "workflow" / "rendered.api.json",
            root / "comfy" / "submit.response.json",
            root / "comfy" / "history.json",
        ]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in allowed:
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        return FileResponse(destination, media_type="application/zip", filename=destination.name)

    @app.get("/admin/log-link")
    async def log_link(
        _: Annotated[Principal, Depends(admin_principal)],
        job_id: str | None = None,
        request_id: str | None = None,
        node_id: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, str]:
        terms = {
            "job_id": job_id,
            "request_id": request_id,
            "node_id": node_id,
            "error_code": error_code,
        }
        expression = " | ".join(f'json | {key}="{value}"' for key, value in terms.items() if value)
        left = json.dumps({"queries": [{"expr": '{job=~".+"} | ' + expression}]})
        return {"url": f"{cfg.grafana_base_url.rstrip('/')}/explore?orgId=1&left={quote(left)}"}

    async def send_feishu(title: str, lines: list[str]) -> dict[str, Any]:
        if not cfg.feishu_webhook_url:
            return {"configured": False, "sent": False}
        timestamp = str(int(time.time()))
        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
            },
        }
        if cfg.feishu_signing_secret:
            message = f"{timestamp}\n{cfg.feishu_signing_secret}".encode()
            payload.update(
                timestamp=timestamp,
                sign=base64.b64encode(
                    hmac.new(message, digestmod=hashlib.sha256).digest()
                ).decode(),
            )
        async with httpx.AsyncClient(timeout=10) as client:
            for attempt in range(3):
                try:
                    response = await client.post(cfg.feishu_webhook_url, json=payload)
                    response.raise_for_status()
                    response_body = response.json()
                    business_code = response_body.get("code", response_body.get("StatusCode", 0))
                    if business_code not in {0, "0", None}:
                        raise httpx.HTTPStatusError(
                            f"Feishu business code {business_code}",
                            request=response.request,
                            response=response,
                        )
                    return {"configured": True, "sent": True, "attempt": attempt + 1}
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
        return {"configured": True, "sent": False}

    async def deliver_one_alert(app_instance: FastAPI) -> bool:
        now = datetime.now(UTC)
        async with app_instance.state.db.session() as delivery_db:
            alert = await delivery_db.scalar(
                select(Alert)
                .where(Alert.next_notification_at.is_not(None), Alert.next_notification_at <= now)
                .order_by(Alert.next_notification_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if alert is None:
                return False
            status = alert.status
            title = ("恢复" if status == "resolved" else "告警") + (
                f"：{alert.labels.get('alertname', 'GPU Control')}"
            )
            lines = [
                f"**级别**：{alert.labels.get('severity', 'warning')}",
                f"**节点**：{alert.labels.get('node_id', alert.labels.get('instance', 'unknown'))}",
                f"**摘要**：{alert.annotations.get('summary', '')}",
                f"**建议动作**：{alert.annotations.get('action', '打开控制台检查指标和日志')}",
            ]
            alert.notification_attempts += 1
            delay = min(300, 10 * (2 ** min(alert.notification_attempts - 1, 5)))
            alert.next_notification_at = now + timedelta(seconds=delay)
            await delivery_db.commit()

        error: str | None = None
        try:
            result = await send_feishu(title, lines)
            sent = bool(result.get("sent"))
            if not result.get("configured"):
                error = "FEISHU_NOT_CONFIGURED"
        except Exception as exc:
            sent = False
            error = type(exc).__name__

        async with app_instance.state.db.session() as delivery_db:
            current = await delivery_db.get(Alert, alert.id, with_for_update=True)
            if current is None:
                return True
            if current.status == status and sent:
                current.last_notified_status = status
                current.next_notification_at = None
                current.notification_error = None
            elif error == "FEISHU_NOT_CONFIGURED":
                current.next_notification_at = None
                current.notification_error = error
            else:
                current.notification_error = error or "FEISHU_DELIVERY_FAILED"
            await delivery_db.commit()
        return True

    async def alert_delivery_loop(app_instance: FastAPI) -> None:
        while True:
            try:
                delivered = await deliver_one_alert(app_instance)
                if not delivered:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger().warning(
                    "alert_delivery_degraded",
                    event="alert.delivery_failed",
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(2)

    @app.post("/internal/alerts/webhook", include_in_schema=False)
    async def alertmanager_webhook(
        body: AlertWebhookRequest,
        db: Annotated[AsyncSession, Depends(session)],
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        if not authorization or not hmac.compare_digest(
            authorization, f"Bearer {cfg.alertmanager_webhook_token}"
        ):
            raise HTTPException(401, detail={"code": "AUTH_FAILED"})
        queued_notifications = 0
        for item in body.alerts:
            labels = item.labels
            annotations = item.annotations
            fingerprint = (
                item.fingerprint
                or hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()
            )
            status = item.status
            existing = await db.get(Alert, fingerprint)
            ends_at = item.ends_at if item.ends_at and item.ends_at.year > 1 else None
            if existing is None:
                existing = Alert(
                    id=fingerprint,
                    fingerprint=fingerprint,
                    status=status,
                    severity=labels.get("severity", "warning")[:24],
                    labels=labels,
                    annotations=annotations,
                    starts_at=item.starts_at,
                    ends_at=ends_at,
                    next_notification_at=datetime.now(UTC),
                )
                db.add(existing)
                queued_notifications += 1
            else:
                status_changed = existing.status != status
                existing.status = status
                existing.labels = labels
                existing.annotations = annotations
                existing.ends_at = ends_at
                if status_changed and existing.last_notified_status != status:
                    existing.notification_attempts = 0
                    existing.notification_error = None
                    existing.next_notification_at = datetime.now(UTC)
                    queued_notifications += 1
        await db.commit()
        return {
            "accepted": len(body.alerts),
            "queued_notifications": queued_notifications,
            "feishu_configured": bool(cfg.feishu_webhook_url),
        }

    @app.post("/admin/alerts/test-feishu")
    async def test_feishu(
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        result = await send_feishu(
            "GPU Control 测试消息", ["飞书告警桥接配置有效。", f"操作人：{principal.id}"]
        )
        await audit(
            db,
            request,
            principal,
            "alert.feishu.test",
            "system",
            "feishu",
            {},
            {"sent": result.get("sent", False)},
        )
        await db.commit()
        return result

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gpu_control_api.main:app", host="0.0.0.0", port=8000)
