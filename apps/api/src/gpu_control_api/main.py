import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
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
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.comfy_client import ComfyClient, ComfyError
from packages.gpu_control_core.admission import (
    TERMINAL_ASSET_WORK_STATUSES,
    active_production_work_exists,
    client_is_load_test,
)
from packages.gpu_control_core.batches import (
    BatchContractError,
    extract_batch_archive,
    parse_batch_manifest,
    transition_batch,
    workflow_identity_from_row,
    workflow_manifest_from_row,
)
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import (
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
    BatchStatus,
    JobStatus,
    NodeMode,
    Priority,
)
from packages.gpu_control_core.logging import bind_context, configure_logging, logger, reset_context
from packages.gpu_control_core.models import (
    Alert,
    ApiClient,
    ApiKey,
    AssetArtifact,
    AssetJob,
    AssetJobEvent,
    AssetWorker,
    AuditLog,
    BatchArtifact,
    BatchCancelOperation,
    BatchEvent,
    BatchIdempotencyKey,
    IdempotencyKey,
    Job,
    JobArtifact,
    JobAttempt,
    JobBatch,
    JobBatchItem,
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
from packages.gpu_control_core.scheduling import (
    SUBSTANCE_DRAIN_OWNER,
    SUBSTANCE_DRAIN_OWNER_LABEL,
    SUBSTANCE_GPU_NODE_ID,
    SUBSTANCE_MAX_PARALLEL,
    SUBSTANCE_RECOVERY_REQUIRED_LABEL,
    SUBSTANCE_WORKER_ID,
    SUBSTANCE_WORKER_ID_PREFIX,
    linux_asset_claim_allowed,
    substance_fence_job_ids,
    substance_pending_reservation,
)
from packages.gpu_control_core.security import (
    create_access_token,
    create_refresh_token,
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def is_postgres_lock_not_available(exc: DBAPIError) -> bool:
    """Recognize PostgreSQL NOWAIT lock conflicts without hiding other DB errors."""

    original = exc.orig
    return (
        getattr(original, "sqlstate", None) == "55P03"
        or getattr(original, "pgcode", None) == "55P03"
    )


REQUESTS = Counter(
    "gpu_control_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
DURATION = Histogram(
    "gpu_control_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)


def runtime_version_metadata() -> dict[str, Any]:
    try:
        installed = package_version("gpu-control")
    except PackageNotFoundError:
        installed = None
    declared_build_version = os.environ.get("GPU_CONTROL_BUILD_VERSION")
    build_revision = os.environ.get("GPU_CONTROL_BUILD_REVISION")
    build_version = declared_build_version or installed
    version_aligned = bool(
        installed and declared_build_version and installed == declared_build_version
    )
    return {
        "component": "api",
        "version": build_version,
        "package_version": installed,
        "build_version": declared_build_version,
        "source_revision": build_revision,
        "version_aligned": version_aligned,
        "provenance_complete": bool(
            version_aligned and build_revision and re.fullmatch(r"[0-9a-f]{40}", build_revision)
        ),
    }


class Principal(BaseModel):
    id: str
    role: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=1024)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=4096)


class NodeModeRequest(BaseModel):
    mode: NodeMode
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool


class NodeHeartbeatRequest(BaseModel):
    node_id: str = Field(pattern=r"^(?:worker|control)-[a-z0-9-]+$", max_length=64)
    ip: str
    mac: str = Field(pattern=r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
    gpu_uuid: str = Field(pattern=r"^GPU-[0-9a-fA-F-]{36}$", max_length=64)
    gpu_model: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._+()/:,\-]{0,127}$",
    )
    hostname: str = Field(min_length=1, max_length=128)
    node_agent_version: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$"
    )
    source_revision: str | None = Field(
        default=None, min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"
    )
    imageclip_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$", max_length=40)
    imageclip_pipeline_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", max_length=64
    )
    codex_cli_installed: bool = False
    codex_cli_version: str | None = Field(default=None, max_length=64)
    codex_cli_error: str | None = Field(default=None, max_length=64)
    codex_cli_checked_at: float | None = Field(default=None, ge=0)

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        address = ipaddress.ip_address(value)
        if address.version != 4 or address.is_loopback or address.is_unspecified:
            raise ValueError("node heartbeat requires a routable IPv4 address")
        return str(address)

    @field_validator("mac")
    @classmethod
    def normalize_mac(cls, value: str) -> str:
        return value.lower()


class RetryRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool


class BatchCancelRequest(BaseModel):
    reason: str = Field(default="client_request", min_length=3, max_length=500)


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
    client_kind: Literal["production", "test"] = "production"
    max_queued: int = Field(20, ge=1, le=10_000)
    max_running: int = Field(1, ge=1, le=10)
    daily_quota: int = Field(1000, ge=1, le=1_000_000)
    weight: int = Field(1, ge=1, le=100)
    allowed_ips: list[str] = Field(default_factory=list)
    callback_hosts: list[str] = []

    @field_validator("allowed_ips")
    @classmethod
    def validate_allowed_ips(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            try:
                normalized.append(str(ipaddress.ip_address(value.strip())))
            except ValueError as exc:
                raise ValueError(f"无效来源 IP: {value}") from exc
        if len(normalized) != len(set(normalized)):
            raise ValueError("来源 IP 不能重复")
        return normalized


class ClientUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    client_kind: Literal["production", "test"] = "production"
    enabled: bool = True
    max_queued: int = Field(20, ge=1, le=10_000)
    max_running: int = Field(1, ge=1, le=10)
    daily_quota: int = Field(1000, ge=1, le=1_000_000)
    weight: int = Field(1, ge=1, le=100)
    allowed_ips: list[str] = Field(default_factory=list)
    callback_hosts: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool

    @field_validator("allowed_ips")
    @classmethod
    def validate_allowed_ips(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            try:
                normalized.append(str(ipaddress.ip_address(value.strip())))
            except ValueError as exc:
                raise ValueError(f"无效来源 IP: {value}") from exc
        if len(normalized) != len(set(normalized)):
            raise ValueError("来源 IP 不能重复")
        return normalized


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


def _merge_service_parameter(parameters_raw: str, name: str, value: str | None) -> str:
    """Merge a convenience multipart field into the canonical parameters JSON."""
    if value is None:
        return parameters_raw
    try:
        parameters = json.loads(parameters_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("parameters 必须是 JSON 对象") from exc
    if not isinstance(parameters, dict):
        raise ValueError("parameters 必须是 JSON 对象")
    if name in parameters and parameters[name] != value:
        raise ValueError(f"{name} 与 parameters.{name} 不能冲突")
    parameters[name] = value
    return json.dumps(parameters, ensure_ascii=False, separators=(",", ":"))


async def _notify(app: FastAPI, channel: str, payload: dict[str, Any]) -> None:
    redis: Redis | None = getattr(app.state, "redis", None)
    if redis is None:
        return
    try:
        await redis.publish(channel, json.dumps(payload))
    except Exception as exc:
        logger().warning(
            "redis.publish_failed",
            error_code="REDIS_UNAVAILABLE",
            error_type=type(exc).__name__,
        )


def substance_gpu_interlock(node: Node, now: datetime) -> dict[str, Any]:
    """Describe durable native-Baker interlocks without mutating ownership."""

    labels = dict(node.labels or {})
    pending_ids, _ = substance_pending_reservation(labels, now)
    fence_ids = substance_fence_job_ids(labels)
    recovery_required = bool(labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL))
    return {
        "active": bool(pending_ids or fence_ids or recovery_required),
        "pending_job_ids": pending_ids,
        "fence_job_ids": fence_ids,
        "recovery_required": recovery_required,
    }


def take_operator_drain_ownership(node: Node) -> bool:
    """Transfer DRAINING ownership while retaining every physical-GPU fence."""

    labels = dict(node.labels or {})
    owned = labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
    labels.pop(SUBSTANCE_DRAIN_OWNER_LABEL, None)
    node.labels = labels
    return owned


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
        app.state.node_heartbeat_nonces = {}
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

    version_info = runtime_version_metadata()
    app = FastAPI(
        title="GPU Control API",
        version=version_info["version"] or "0+unknown",
        lifespan=lifespan,
    )

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
            pending_batch_root = getattr(request.state, "uncommitted_batch_root", None)
            if pending_batch_root is not None:
                pending_batch_id = getattr(request.state, "uncommitted_batch_id", None)
                committed_batch = False
                try:
                    if pending_batch_id:
                        async with request.app.state.db.session() as cleanup_db:
                            committed_batch = (
                                await cleanup_db.get(JobBatch, pending_batch_id)
                            ) is not None
                except Exception:
                    logger().exception(
                        "batch.uncommitted_commit_state_unknown",
                        error_code="BATCH_STORAGE_CLEANUP_DEFERRED",
                        batch_id=pending_batch_id,
                        batch_root=str(pending_batch_root),
                    )
                    # A commit acknowledgement can be lost after PostgreSQL
                    # made the row durable. Preserve the directory whenever
                    # commit state cannot be proven; deleting it could corrupt
                    # an authoritative batch. A later orphan sweep may remove
                    # it once database state is available.
                    committed_batch = True
                if not committed_batch:
                    try:
                        request.app.state.storage.remove_tree(pending_batch_root)
                    except Exception:
                        logger().exception(
                            "batch.uncommitted_directory_cleanup_failed",
                            error_code="BATCH_STORAGE_CLEANUP_FAILED",
                            batch_id=pending_batch_id,
                            batch_root=str(pending_batch_root),
                        )
            reset_context(token)

    async def session(request: Request) -> AsyncIterator[AsyncSession]:
        async with request.app.state.db.session() as db_session:
            yield db_session

    async def api_principal(
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> Principal:
        key: ApiKey | None = None
        client: ApiClient | None = None
        source_ip = str(
            ipaddress.ip_address(request.client.host if request.client else "127.0.0.1")
        )
        if x_api_key:
            if not x_api_key.startswith("gpc_"):
                raise HTTPException(
                    401, detail={"code": "AUTH_FAILED", "message": "API Key 格式错误"}
                )
            parts = x_api_key.split("_", 2)
            if len(parts) != 3:
                raise HTTPException(
                    401, detail={"code": "AUTH_FAILED", "message": "API Key 格式错误"}
                )
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
        else:
            clients = list(
                (await db.scalars(select(ApiClient).where(ApiClient.role == "client"))).all()
            )
            matches = [row for row in clients if source_ip in (row.allowed_ips or [])]
            if len(matches) > 1:
                raise HTTPException(
                    409,
                    detail={
                        "code": "CLIENT_IP_CONFLICT",
                        "message": "来源 IP 被多个客户绑定，请联系管理员",
                    },
                )
            if matches:
                client = matches[0]
            else:
                auto_id = f"ip-{hashlib.sha256(source_ip.encode()).hexdigest()[:12]}"
                client = await db.get(ApiClient, auto_id)
                if client is None:
                    client = ApiClient(
                        id=auto_id,
                        name=f"自动发现 {source_ip}",
                        role="client",
                        client_kind="production",
                        max_queued=cfg.default_tenant_max_queued,
                        max_running=cfg.default_tenant_max_running,
                        daily_quota=1000,
                        weight=1,
                        allowed_ips=[source_ip],
                        last_seen_ip=source_ip,
                        last_seen_at=datetime.now(UTC),
                    )
                    db.add(client)
                    db.add(RateLimitPolicy(client_id=auto_id, requests_per_second=5, burst=10))
                    try:
                        await db.flush()
                    except IntegrityError:
                        await db.rollback()
                        client = await db.get(ApiClient, auto_id)
        if client is None or not client.enabled or client.role != "client":
            raise HTTPException(403, detail={"code": "AUTH_FAILED", "message": "客户已停用"})
        client.last_seen_ip = source_ip
        client.last_seen_at = datetime.now(UTC)
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
                    "redis.rate_limit_failed",
                    error_code="REDIS_UNAVAILABLE",
                )
        if key is not None:
            key.last_used_at = datetime.now(UTC)
        await db.commit()
        return Principal(id=client.id, role="client")

    async def explicit_api_key_principal(
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> Principal:
        if not x_api_key:
            raise HTTPException(
                401,
                detail={
                    "code": "EXPLICIT_API_KEY_REQUIRED",
                    "message": "取消批次必须提供 X-API-Key",
                },
            )
        return await api_principal(request, db, x_api_key)

    async def admin_principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, detail={"code": "AUTH_FAILED", "message": "需要管理员令牌"})
        try:
            payload = jwt.decode(authorization[7:], cfg.jwt_secret, algorithms=["HS256"])
            if payload.get("type", "access") != "access":
                raise ValueError("refresh token cannot authorize admin requests")
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

    async def append_admin_asset_event(
        db: AsyncSession,
        job: AssetJob,
        *,
        event: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        sequence = (
            int(
                await db.scalar(
                    select(func.coalesce(func.max(AssetJobEvent.sequence), 0)).where(
                        AssetJobEvent.job_id == job.id
                    )
                )
                or 0
            )
            + 1
        )
        db.add(
            AssetJobEvent(
                job_id=job.id,
                sequence=sequence,
                status=job.status,
                stage=job.stage,
                progress=job.progress,
                message=job.stage_message,
                estimated_remaining_seconds=job.estimated_remaining_seconds,
                details={"event": event, **(details or {})},
            )
        )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/api/v1/version")
    async def version() -> dict[str, Any]:
        return runtime_version_metadata()

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

    @app.post("/api/v1/nodes/heartbeat")
    async def node_heartbeat(
        body: NodeHeartbeatRequest,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        x_gpu_timestamp: Annotated[str | None, Header()] = None,
        x_gpu_nonce: Annotated[str | None, Header()] = None,
        x_gpu_signature: Annotated[str | None, Header()] = None,
        x_real_ip: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        timestamp = x_gpu_timestamp or ""
        nonce = x_gpu_nonce or ""
        signature = x_gpu_signature or ""
        try:
            stamp = int(timestamp)
        except ValueError as exc:
            raise HTTPException(401, detail={"code": "NODE_TIMESTAMP_INVALID"}) from exc
        now = int(time.time())
        if abs(now - stamp) > 30 or not nonce or len(nonce) > 128:
            raise HTTPException(401, detail={"code": "NODE_TIMESTAMP_EXPIRED"})
        nonces: dict[str, int] = request.app.state.node_heartbeat_nonces
        for key, seen in list(nonces.items()):
            if now - seen > 60:
                del nonces[key]
        replay_key = f"{body.node_id}:{nonce}"
        if replay_key in nonces:
            raise HTTPException(409, detail={"code": "NODE_HEARTBEAT_REPLAY"})
        # Verify the exact bytes sent by the agent. Re-serializing the parsed
        # model would inject newly added defaults and break signatures from
        # older agents during a rolling control-plane upgrade.
        raw_body = await request.body()
        expected = sign_agent_request(
            request.method,
            request.url.path,
            raw_body,
            timestamp,
            nonce,
            cfg.node_agent_secret(body.node_id),
        )
        if not signature:
            raise HTTPException(401, detail={"code": "NODE_SIGNATURE_MISSING"})
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(401, detail={"code": "NODE_SIGNATURE_INVALID"})
        source_raw = x_real_ip or (request.client.host if request.client else "")
        try:
            source_ip = str(ipaddress.ip_address(source_raw))
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "NODE_SOURCE_IP_INVALID"}) from exc
        if source_ip != body.ip:
            raise HTTPException(409, detail={"code": "NODE_SOURCE_IP_MISMATCH"})
        node = await db.get(Node, body.node_id, with_for_update=True)
        if node is None:
            raise HTTPException(404, detail={"code": "NODE_NOT_APPROVED"})
        labels = dict(node.labels or {})
        for key, reported in (("mac", body.mac), ("gpu_uuid", body.gpu_uuid)):
            registered = str(labels.get(key, ""))
            if registered and registered.lower() != reported.lower():
                raise HTTPException(409, detail={"code": "NODE_IDENTITY_MISMATCH", "field": key})
        old_base_url = node.base_url
        pipeline_changed = labels.get("imageclip_commit") != (
            body.imageclip_commit or ""
        ) or labels.get("imageclip_pipeline_sha256") != (body.imageclip_pipeline_sha256 or "")
        labels.update(
            {
                "host": body.ip,
                "hostname": body.hostname,
                "mac": body.mac,
                "gpu_uuid": body.gpu_uuid,
                "agent_last_seen_at": datetime.now(UTC).isoformat(),
                "imageclip_commit": body.imageclip_commit or "",
                "imageclip_pipeline_sha256": body.imageclip_pipeline_sha256 or "",
                "codex_cli_installed": body.codex_cli_installed,
                "codex_cli_version": body.codex_cli_version or "",
                "codex_cli_error": body.codex_cli_error or "",
                "codex_cli_checked_at": body.codex_cli_checked_at,
            }
        )
        if body.node_agent_version is not None:
            labels["node_agent_version"] = body.node_agent_version
        if body.source_revision is not None:
            labels["source_revision"] = body.source_revision
        if body.gpu_model is not None:
            labels["gpu_model"] = body.gpu_model
        node.labels = labels
        node.custom_nodes_version = (
            f"imageclip:{body.imageclip_commit[:12]}:{body.imageclip_pipeline_sha256[:12]}"
            if body.imageclip_commit and body.imageclip_pipeline_sha256
            else None
        )
        node.base_url = f"http://{body.ip}:8188"
        node.agent_url = f"http://{body.ip}:9201"
        if pipeline_changed:
            versions = list(
                (
                    await db.scalars(
                        select(WorkflowVersion).where(
                            WorkflowVersion.workflow_key == "imageclip-rgba",
                            WorkflowVersion.enabled.is_(True),
                        )
                    )
                ).all()
            )
            for version in versions:
                await refresh_workflow_compatibility(db, version)
        nonces[replay_key] = now
        await db.commit()
        await _notify(
            request.app, "gpu-control:wakeup", {"event": "node.heartbeat", "node_id": node.id}
        )
        logger().info(
            "node.heartbeat",
            node_id=node.id,
            source_ip=body.ip,
            address_changed=old_base_url != node.base_url,
        )
        return {"status": "accepted", "node_id": node.id, "base_url": node.base_url}

    @app.get("/internal/prometheus/workers")
    async def prometheus_worker_targets(
        db: Annotated[AsyncSession, Depends(session)],
    ) -> list[dict[str, Any]]:
        nodes = list(
            (
                await db.scalars(
                    select(Node).where(
                        Node.id.like("worker-%"), Node.mode != NodeMode.DISABLED.value
                    )
                )
            ).all()
        )
        groups: list[dict[str, Any]] = []
        for node in nodes:
            host = str((node.labels or {}).get("host", ""))
            try:
                host = str(ipaddress.ip_address(host))
            except ValueError:
                continue
            groups.extend(
                [
                    {
                        "targets": [f"{host}:9100"],
                        "labels": {"exporter": "node", "node_id": node.id},
                    },
                    {
                        "targets": [f"{host}:9400"],
                        "labels": {"exporter": "dcgm", "node_id": node.id},
                    },
                ]
            )
        return groups

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
            "refresh_token": create_refresh_token(client.id, client.role, cfg.jwt_secret),
            "token_type": "bearer",
            "expires_in": 900,
            "refresh_expires_in": 7 * 24 * 60 * 60,
            "role": client.role,
        }

    @app.post("/admin/auth/refresh")
    async def refresh_admin_token(
        body: RefreshTokenRequest,
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        try:
            payload = jwt.decode(body.refresh_token, cfg.jwt_secret, algorithms=["HS256"])
            if payload.get("type") != "refresh":
                raise ValueError("not a refresh token")
            client_id = str(payload["sub"])
            role = str(payload["role"])
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise HTTPException(
                401,
                detail={"code": "REFRESH_TOKEN_INVALID", "message": "登录已过期，请重新登录"},
            ) from exc
        client = await db.get(ApiClient, client_id)
        if (
            client is None
            or not client.enabled
            or client.role != role
            or role
            not in {
                "admin",
                "operator",
                "viewer",
            }
        ):
            raise HTTPException(
                401,
                detail={"code": "REFRESH_TOKEN_INVALID", "message": "登录已失效，请重新登录"},
            )
        return {
            "access_token": create_access_token(client.id, client.role, cfg.jwt_secret),
            "refresh_token": create_refresh_token(client.id, client.role, cfg.jwt_secret),
            "token_type": "bearer",
            "expires_in": 900,
            "refresh_expires_in": 7 * 24 * 60 * 60,
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
        # The per-job ComfyUI upload path is added below and contains a fresh
        # UUID.  It is an execution detail, not caller input, so it must never
        # participate in the idempotency fingerprint.
        request_parameters = dict(parameters)
        if priority == Priority.CRITICAL:
            raise HTTPException(
                403,
                detail={"code": "PRIORITY_FORBIDDEN", "message": "业务 API 不能直接提交 CRITICAL"},
            )
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
            # Scheduler uploads every job into an isolated ComfyUI input
            # subfolder named after the job.  LoadImage must receive that
            # relative path, otherwise ComfyUI looks in the input root and
            # cannot find the uploaded file.
            parameters[f"{field_name}_filename"] = f"{job_id}/{destination.name}"
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
        request_hash = _request_hash(
            workflow_key, workflow_version, request_parameters, file_hashes
        )
        # Upload and image validation intentionally happen before admission so
        # large request bodies cannot serialize every tenant.  Admission itself
        # uses the same global -> tenant transaction-lock order as the Scheduler
        # feeder, then re-counts under those locks before a Job is inserted.
        await request.app.state.db.acquire_global_admission_transaction_lock(db)
        await request.app.state.db.acquire_tenant_transaction_lock(db, principal.id)
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
        is_load_test = await client_is_load_test(db, principal.id)
        if is_load_test and await active_production_work_exists(db):
            storage.remove_tree(root)
            raise HTTPException(
                503,
                detail={
                    "code": "LOAD_TEST_PREEMPTED",
                    "message": "真实生产任务已进入系统，新的压力测试任务已暂停接收",
                    "retryable": True,
                },
                headers={"Retry-After": "5"},
            )
        queued = await db.scalar(
            select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value)
        )
        tenant_queued = await db.scalar(
            select(func.count(Job.id)).where(
                Job.tenant_id == principal.id, Job.status == JobStatus.QUEUED.value
            )
        )
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        daily = await db.scalar(
            select(func.count(Job.id)).where(Job.tenant_id == principal.id, Job.created_at >= today)
        )
        if client and int(daily or 0) >= client.daily_quota:
            storage.remove_tree(root)
            raise HTTPException(
                429, detail={"code": "RATE_LIMITED", "message": "今日任务配额已用完"}
            )
        system_queue_limit = (
            cfg.test_system_max_queued if is_load_test else cfg.system_max_queued
        )
        if int(queued or 0) >= system_queue_limit or int(tenant_queued or 0) >= (
            client.max_queued if client else cfg.default_tenant_max_queued
        ):
            storage.remove_tree(root)
            detail: dict[str, Any] = {
                "code": "RATE_LIMITED",
                "message": "队列已达到限制",
            }
            if is_load_test and int(queued or 0) >= system_queue_limit:
                detail.update(
                    {
                        "reason": "PRODUCTION_QUEUE_RESERVED",
                        "retryable": True,
                    }
                )
            raise HTTPException(429, detail=detail)
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

    async def run_image_service(
        request: Request,
        workflow_key: str,
        principal: Principal,
        db: AsyncSession,
        image: UploadFile,
        parameters: str,
        idempotency_key: str | None,
    ) -> FileResponse:
        workflow = await db.scalar(
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_key == workflow_key,
                WorkflowVersion.enabled.is_(True),
            )
            .order_by(WorkflowVersion.created_at.desc())
        )
        if workflow is None:
            raise HTTPException(
                404,
                detail={"code": "WORKFLOW_NOT_FOUND", "message": "服务工作流未启用"},
            )
        tenant_lock = request.app.state.tenant_locks.setdefault(principal.id, asyncio.Lock())
        async with tenant_lock:
            queued = await _create_job(
                request,
                workflow.workflow_key,
                workflow.version,
                parameters,
                Priority.NORMAL,
                idempotency_key,
                principal,
                db,
                image,
                None,
                None,
            )
        job_id = str(json.loads(bytes(queued.body))["job_id"])
        deadline = asyncio.get_running_loop().time() + workflow.timeout_seconds + 60
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1)
            async with request.app.state.db.session() as poll_db:
                job = await poll_db.get(Job, job_id)
                if job is None:
                    raise HTTPException(
                        500,
                        detail={"code": "JOB_NOT_FOUND", "message": "任务记录意外丢失"},
                    )
                if job.status == JobStatus.SUCCEEDED.value:
                    artifact = await poll_db.scalar(
                        select(JobArtifact)
                        .where(JobArtifact.job_id == job_id, JobArtifact.kind == "output")
                        .order_by(JobArtifact.created_at.desc())
                    )
                    if artifact is None:
                        raise HTTPException(
                            500,
                            detail={"code": "OUTPUT_MISSING", "message": "任务成功但没有图片产物"},
                        )
                    path = Path(job.job_dir) / artifact.relative_path
                    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    return FileResponse(
                        path,
                        media_type=media_type,
                        filename=path.name,
                        headers={
                            "X-Job-ID": job_id,
                            "X-Client-ID": principal.id,
                            "X-Artifact-SHA256": artifact.sha256,
                            "Cache-Control": "no-store",
                        },
                    )
                if job.status in {status.value for status in TERMINAL_JOB_STATUSES}:
                    raise HTTPException(
                        500,
                        detail={
                            "code": job.error_code or "GENERATION_FAILED",
                            "message": job.error_message or "图片生成失败",
                            "job_id": job_id,
                        },
                    )
        raise HTTPException(
            504,
            detail={"code": "SERVICE_TIMEOUT", "message": "图片生成等待超时", "job_id": job_id},
        )

    @app.post("/api/v1/services/imageclip-rgba", response_class=FileResponse)
    async def imageclip_rgba_service(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        image: Annotated[UploadFile, File()],
        parameters: Annotated[str, Form()] = "{}",
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> FileResponse:
        return await run_image_service(
            request,
            "imageclip-rgba",
            principal,
            db,
            image,
            parameters,
            idempotency_key,
        )

    @app.post("/api/v1/services/modelview-inpaint", response_class=FileResponse)
    async def modelview_inpaint_service(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        image: Annotated[UploadFile, File()],
        parameters: Annotated[str, Form()] = "{}",
        prompt: Annotated[str | None, Form(max_length=4096)] = None,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> FileResponse:
        try:
            parameters = _merge_service_parameter(parameters, "prompt", prompt)
        except ValueError as exc:
            raise HTTPException(
                422,
                detail={"code": "INPUT_INVALID", "message": str(exc)},
            ) from exc
        return await run_image_service(
            request,
            "modelview-inpaint",
            principal,
            db,
            image,
            parameters,
            idempotency_key,
        )

    @app.post("/api/v1/services/modelview-roughness", response_class=FileResponse)
    async def modelview_roughness_service(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        image: Annotated[UploadFile, File()],
        parameters: Annotated[str, Form()] = "{}",
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> FileResponse:
        """Render the approved fixed-prompt roughness workflow for one image."""
        return await run_image_service(
            request,
            "modelview-roughness",
            principal,
            db,
            image,
            parameters,
            idempotency_key,
        )

    async def owned_batch(batch_id: str, principal: Principal, db: AsyncSession) -> JobBatch:
        batch = await db.get(JobBatch, batch_id)
        if batch is None or batch.tenant_id != principal.id:
            raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND", "message": "批次不存在"})
        return batch

    async def batch_payload(
        batch: JobBatch, db: AsyncSession, *, admin: bool = False
    ) -> dict[str, Any]:
        items = list(
            (
                await db.scalars(
                    select(JobBatchItem)
                    .where(JobBatchItem.batch_id == batch.id)
                    .order_by(JobBatchItem.ordinal)
                )
            ).all()
        )
        job_ids = [item.job_id for item in items if item.job_id]
        attempts = (
            list(
                (
                    await db.scalars(
                        select(JobAttempt)
                        .where(JobAttempt.job_id.in_(job_ids))
                        .order_by(JobAttempt.job_id, JobAttempt.attempt)
                    )
                ).all()
            )
            if job_ids
            else []
        )
        node_ids = {
            str(node_id)
            for node_id in [
                *(item.node_id for item in items),
                *(attempt.node_id for attempt in attempts),
            ]
            if node_id
        }
        nodes = (
            list((await db.scalars(select(Node).where(Node.id.in_(node_ids)))).all())
            if node_ids
            else []
        )
        nodes_by_id = {node.id: node for node in nodes}
        identity = {
            "workflow_key": batch.workflow_key,
            "workflow_version": batch.workflow_version,
            "pipeline_commit": batch.pipeline_commit,
            "pipeline_sha256": batch.pipeline_sha256,
            "output_node": batch.output_node,
        }
        distribution: dict[str, int] = {}
        for item in items:
            if item.node_id:
                distribution[item.node_id] = distribution.get(item.node_id, 0) + 1
        artifacts: list[dict[str, Any]] = []
        if batch.status == BatchStatus.SUCCEEDED.value:
            artifact_rows = (
                await db.scalars(
                    select(BatchArtifact)
                    .where(BatchArtifact.batch_id == batch.id)
                    .order_by(BatchArtifact.created_at)
                )
            ).all()
            artifacts = [
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "filename": artifact.filename,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    **identity,
                    "download_url": (
                        f"/admin/batches/{batch.id}/artifacts/{artifact.id}"
                        if admin
                        else f"/api/v1/batches/{batch.id}/artifacts/{artifact.id}"
                    ),
                }
                for artifact in artifact_rows
            ]
        cancel_operation = await db.scalar(
            select(BatchCancelOperation).where(BatchCancelOperation.batch_id == batch.id)
        )

        def duration_ms(start: datetime | None, end: datetime | None) -> int | None:
            if start is None or end is None:
                return None
            normalized_start = start if start.tzinfo is not None else start.replace(tzinfo=UTC)
            normalized_end = end if end.tzinfo is not None else end.replace(tzinfo=UTC)
            value = int(
                (normalized_end.astimezone(UTC) - normalized_start.astimezone(UTC)).total_seconds()
                * 1000
            )
            return value if value >= 0 else None

        def utc_timestamp(value: datetime | None) -> str | None:
            if value is None:
                return None
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat()

        def percentile(values: list[int], percent: int) -> int | None:
            if not values:
                return None
            ordered = sorted(values)
            index = max(0, ((len(ordered) * percent + 99) // 100) - 1)
            return ordered[min(index, len(ordered) - 1)]

        def median(values: list[int]) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            middle = len(ordered) // 2
            if len(ordered) % 2:
                return float(ordered[middle])
            return (ordered[middle - 1] + ordered[middle]) / 2

        attempts_by_job: dict[str, list[JobAttempt]] = {}
        for attempt in attempts:
            attempts_by_job.setdefault(attempt.job_id, []).append(attempt)

        reassignments_in: dict[str, int] = {}
        reassignments_out: dict[str, int] = {}
        for job_attempts in attempts_by_job.values():
            for previous, current in zip(job_attempts, job_attempts[1:], strict=False):
                if previous.node_id == current.node_id:
                    continue
                reassignments_out[previous.node_id] = reassignments_out.get(previous.node_id, 0) + 1
                reassignments_in[current.node_id] = reassignments_in.get(current.node_id, 0) + 1

        gpu_attempts = [
            attempt
            for attempt in attempts
            if attempt.gpu_started_at is not None or attempt.gpu_finished_at is not None
        ]
        completed_gpu_durations = [
            value
            for attempt in gpu_attempts
            if (value := duration_ms(attempt.gpu_started_at, attempt.gpu_finished_at)) is not None
        ]
        completed_gpu_assignments = {
            (attempt.job_id, attempt.node_id)
            for attempt in gpu_attempts
            if duration_ms(attempt.gpu_started_at, attempt.gpu_finished_at) is not None
        }
        successful_items_have_gpu_samples = all(
            item.job_id is not None
            and item.node_id is not None
            and (item.job_id, item.node_id) in completed_gpu_assignments
            for item in items
            if item.status == "SUCCEEDED"
        )
        gpu_measurements_complete = (
            bool(gpu_attempts)
            and len(completed_gpu_durations) == len(gpu_attempts)
            and successful_items_have_gpu_samples
        )
        gpu_service_ms_total = sum(completed_gpu_durations) if completed_gpu_durations else None
        node_performance: list[dict[str, Any]] = []
        complete_node_gpu_finishes: dict[str, datetime] = {}
        for node_id in sorted(node_ids):
            node = nodes_by_id.get(node_id)
            labels = dict(node.labels or {}) if node is not None else {}
            node_items = [item for item in items if item.node_id == node_id]
            node_attempts = [attempt for attempt in attempts if attempt.node_id == node_id]
            node_gpu_attempts = [
                attempt
                for attempt in node_attempts
                if attempt.gpu_started_at is not None or attempt.gpu_finished_at is not None
            ]
            node_gpu_durations = [
                value
                for attempt in node_gpu_attempts
                if (value := duration_ms(attempt.gpu_started_at, attempt.gpu_finished_at))
                is not None
            ]
            node_successful_final_assignments = {
                (item.job_id, node_id)
                for item in node_items
                if item.status == "SUCCEEDED" and item.job_id is not None
            }
            node_successful_items_have_gpu_samples = node_successful_final_assignments.issubset(
                completed_gpu_assignments
            )
            node_gpu_measurements_complete = (
                bool(node_gpu_attempts)
                and len(node_gpu_durations) == len(node_gpu_attempts)
                and node_successful_items_have_gpu_samples
            )
            node_gpu_service_ms = sum(node_gpu_durations) if node_gpu_durations else None
            node_started_at = min(
                (
                    attempt.gpu_started_at
                    for attempt in node_gpu_attempts
                    if attempt.gpu_started_at is not None
                ),
                default=None,
            )
            node_finished_at = (
                max(
                    (
                        attempt.gpu_finished_at
                        for attempt in node_gpu_attempts
                        if attempt.gpu_finished_at is not None
                    ),
                    default=None,
                )
                if node_gpu_measurements_complete
                else None
            )
            if node_finished_at is not None:
                complete_node_gpu_finishes[node_id] = node_finished_at
            node_performance.append(
                {
                    "node_id": node_id,
                    "gpu_model": labels.get("gpu_model") or labels.get("gpu_name") or None,
                    "worker_version": labels.get("node_agent_version") or None,
                    "source_revision": labels.get("source_revision") or None,
                    "workflow_version": batch.workflow_version,
                    "frames_assigned": len({attempt.job_id for attempt in node_attempts}),
                    "frames_final_assignment": len(node_items),
                    "frames_succeeded": sum(item.status == "SUCCEEDED" for item in node_items),
                    "frames_failed": sum(item.status == "FAILED" for item in node_items),
                    "attempts_total": len(node_attempts),
                    "upload_attempts": sum(attempt.upload_attempts for attempt in node_attempts),
                    "prompt_attempts": sum(attempt.prompt_attempts for attempt in node_attempts),
                    "gpu_service_ms": node_gpu_service_ms,
                    "gpu_service_measurements_complete": node_gpu_measurements_complete,
                    "frame_ms_p50": percentile(node_gpu_durations, 50),
                    "frame_ms_p95": percentile(node_gpu_durations, 95),
                    "node_started_at": utc_timestamp(node_started_at),
                    "node_finished_at": utc_timestamp(node_finished_at),
                    "reassignments_in": reassignments_in.get(node_id, 0),
                    "reassignments_out": reassignments_out.get(node_id, 0),
                    "max_concurrent_prompts": (node.max_concurrency if node is not None else None),
                    "input_pixels": sum(item.width * item.height for item in node_items),
                }
            )
        input_pixels_total = sum(item.width * item.height for item in items)
        can_compute_throughput = (
            batch.status == BatchStatus.SUCCEEDED.value
            and gpu_measurements_complete
            and bool(gpu_service_ms_total)
        )
        required_straggler_nodes = set(node_ids)
        straggler_finishes_complete = required_straggler_nodes.issubset(complete_node_gpu_finishes)
        parent_gpu_wall_ms = duration_ms(batch.started_at, batch.execution_finished_at)
        node_finish_offsets_ms: list[int] = []
        straggler_time_bounds_valid = straggler_finishes_complete
        if straggler_finishes_complete:
            for node_id in sorted(required_straggler_nodes):
                node_finished_at = complete_node_gpu_finishes[node_id]
                finish_offset_ms = duration_ms(batch.started_at, node_finished_at)
                finish_to_parent_end_ms = duration_ms(node_finished_at, batch.execution_finished_at)
                if finish_offset_ms is None or finish_to_parent_end_ms is None:
                    straggler_time_bounds_valid = False
                    break
                node_finish_offsets_ms.append(finish_offset_ms)
        finish_median_ms = median(node_finish_offsets_ms)
        can_compute_straggler = (
            BatchStatus(batch.status) in TERMINAL_BATCH_STATUSES
            and gpu_measurements_complete
            and len(required_straggler_nodes) >= 2
            and straggler_time_bounds_valid
            and parent_gpu_wall_ms is not None
            and parent_gpu_wall_ms > 0
            and finish_median_ms is not None
        )
        straggler_ratio = (
            round(
                (max(node_finish_offsets_ms) - finish_median_ms) / parent_gpu_wall_ms,
                6,
            )
            if can_compute_straggler
            and finish_median_ms is not None
            and parent_gpu_wall_ms is not None
            else None
        )
        performance = {
            "schema_version": "1.0",
            "input_pixels_total": input_pixels_total,
            "gpu_service_ms_total": gpu_service_ms_total,
            "gpu_service_measurements_complete": gpu_measurements_complete,
            "queue_ms": duration_ms(batch.queued_at, batch.started_at),
            "execution_ms": duration_ms(batch.started_at, batch.execution_finished_at),
            "assembly_ms": duration_ms(batch.assembling_at, batch.artifact_ready_at),
            "artifact_publish_ms": duration_ms(batch.artifact_ready_at, batch.finished_at),
            "frames_per_gpu_minute": (
                round(batch.succeeded_items * 60_000 / gpu_service_ms_total, 6)
                if can_compute_throughput and gpu_service_ms_total is not None
                else None
            ),
            "megapixels_per_gpu_second": (
                round(input_pixels_total / 1_000_000 / (gpu_service_ms_total / 1000), 6)
                if can_compute_throughput and gpu_service_ms_total is not None
                else None
            ),
            "scheduler_restarts": None,
            "reassignments": sum(reassignments_out.values()),
            "straggler_ratio": straggler_ratio,
            "nodes": node_performance,
        }
        cancel_payload = (
            {
                "cancel_operation_id": cancel_operation.id,
                "cancel_status": cancel_operation.status,
                "cancel_requested_at": cancel_operation.requested_at.isoformat(),
                "cancel_accepted_at": cancel_operation.accepted_at.isoformat(),
                "cancel_finished_at": cancel_operation.finished_at.isoformat()
                if cancel_operation.finished_at
                else None,
                "cancel_requested_by": cancel_operation.requested_by,
                "cancel_source": cancel_operation.source,
                "cancel_reason": cancel_operation.reason,
                "cancel_request_id": cancel_operation.request_id,
                "cancel_idempotency_key": cancel_operation.idempotency_key,
                "cancel_counts": {
                    "completed": cancel_operation.completed_items,
                    "cancelled": cancel_operation.cancelled_items,
                    "not_started": cancel_operation.not_started_items,
                },
                "cancel_audit_reference": f"batch_cancel_operation:{cancel_operation.id}",
            }
            if cancel_operation is not None
            else {}
        )
        payload = {
            "batch_id": batch.id,
            "external_batch_id": batch.external_batch_id,
            "status": batch.status,
            **identity,
            "progress": batch.progress,
            "counts": {
                "total": batch.total_items,
                "pending": batch.pending_items,
                "queued": batch.queued_items,
                "running": batch.running_items,
                "succeeded": batch.succeeded_items,
                "failed": batch.failed_items,
                "cancelled": batch.cancelled_items,
            },
            "node_distribution": distribution,
            "created_at": batch.created_at.isoformat(),
            "validated_at": batch.validated_at.isoformat() if batch.validated_at else None,
            "queued_at": batch.queued_at.isoformat() if batch.queued_at else None,
            "started_at": batch.started_at.isoformat() if batch.started_at else None,
            "last_progress_at": batch.last_progress_at.isoformat()
            if batch.last_progress_at
            else None,
            "execution_finished_at": batch.execution_finished_at.isoformat()
            if batch.execution_finished_at
            else None,
            "assembling_at": batch.assembling_at.isoformat() if batch.assembling_at else None,
            "artifact_ready_at": batch.artifact_ready_at.isoformat()
            if batch.artifact_ready_at
            else None,
            "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
            "updated_at": batch.updated_at.isoformat(),
            "error": {"code": batch.error_code, "message": batch.error_message}
            if batch.error_code
            else None,
            "artifact": artifacts[0] if artifacts else None,
            "artifacts": artifacts,
            "performance": performance,
            **cancel_payload,
        }
        return payload

    async def cancel_response_payload(
        batch: JobBatch, db: AsyncSession, *, admin: bool = False
    ) -> dict[str, Any]:
        """Expose the client cancel acknowledgement without changing state storage.

        ``CANCELLING`` remains the durable state-machine value used by the
        scheduler.  The public cancel contract calls the accepted operation
        ``CANCEL_REQUESTED``; keeping that translation at the response boundary
        avoids teaching the scheduler a second in-flight state.
        """

        payload = await batch_payload(batch, db, admin=admin)
        if payload.get("cancel_status") == "REQUESTED":
            payload["status"] = "CANCEL_REQUESTED"
        return payload

    @app.get("/api/v1/scheduler/capacity")
    async def scheduler_capacity(
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        """Return a read-only, tenant-safe scheduling snapshot.

        This endpoint is advisory only: callers must still rely on the batch
        state machine after submission.  No node address, hardware identity,
        or other tenant's identifiers are exposed.
        """
        client = await db.get(ApiClient, principal.id)
        nodes = list((await db.scalars(select(Node))).all())
        eligible = [
            node
            for node in nodes
            if node.health == "ONLINE"
            and node.mode == NodeMode.ACTIVE.value
            and not node.manual_reserved
            and not node.external_busy
            and not node.foreign_queue_detected
        ]
        total_slots = sum(max(0, int(node.max_concurrency)) for node in eligible)
        used_slots = sum(max(0, int(node.current_jobs)) for node in eligible)
        available_slots = max(0, total_slots - used_slots)
        queued_jobs = int(
            await db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value))
            or 0
        )
        running_jobs = int(
            await db.scalar(select(func.count(Job.id)).where(Job.status.in_(list(ACTIVE_STATUSES))))
            or 0
        )
        tenant_queued = int(
            await db.scalar(
                select(func.count(Job.id)).where(
                    Job.tenant_id == principal.id,
                    Job.status == JobStatus.QUEUED.value,
                )
            )
            or 0
        )
        tenant_running = int(
            await db.scalar(
                select(func.count(Job.id)).where(
                    Job.tenant_id == principal.id,
                    Job.status.in_(list(ACTIVE_STATUSES)),
                )
            )
            or 0
        )
        active_workflow = await db.scalar(
            select(WorkflowVersion)
            .where(
                WorkflowVersion.workflow_key == "imageclip-rgba",
                WorkflowVersion.enabled.is_(True),
            )
            .order_by(WorkflowVersion.created_at.desc())
        )
        compatible_node_ids: set[str] = set()
        if active_workflow is not None:
            compatible_node_ids = set(
                (
                    await db.scalars(
                        select(WorkflowNodeCompatibility.node_id).where(
                            WorkflowNodeCompatibility.workflow_version_id == active_workflow.id,
                            WorkflowNodeCompatibility.compatible.is_(True),
                        )
                    )
                ).all()
            )
        compatible_nodes = sum(node.id in compatible_node_ids for node in eligible)
        tenant_queue_room = max(
            0,
            (client.max_queued if client is not None else cfg.default_tenant_max_queued)
            - tenant_queued,
        )
        client_kind = client.client_kind if client is not None else "production"
        system_queue_limit = (
            cfg.test_system_max_queued
            if client_kind == "test"
            else cfg.system_max_queued
        )
        production_preempting = (
            client_kind == "test" and await active_production_work_exists(db)
        )
        accepting_batches = (
            not production_preempting
            and queued_jobs < system_queue_limit
            and tenant_queue_room > 0
            and compatible_nodes > 0
        )
        suggested_max_new_batches = (
            min(tenant_queue_room, max(1, available_slots)) if accepting_batches else 0
        )
        capacity_generated_at = datetime.now(UTC).isoformat()
        return {
            "schema_version": "1.0",
            "advisory": True,
            "accepting": accepting_batches,
            "as_of": capacity_generated_at,
            "accepting_batches": accepting_batches,
            "suggested_max_new_batches": suggested_max_new_batches,
            "queue_depth": queued_jobs,
            "compatible_nodes": compatible_nodes,
            "estimated_queue_ms_p50": None,
            "estimated_queue_ms_p90": None,
            "capacity_generated_at": capacity_generated_at,
            "cluster": {
                "eligible_nodes": len(eligible),
                "total_slots": total_slots,
                "used_slots": used_slots,
                "available_slots": available_slots,
                "queued_jobs": queued_jobs,
                "running_jobs": running_jobs,
                "system_queue_limit": system_queue_limit,
                "production_queue_reserve": cfg.system_max_queued - system_queue_limit,
            },
            "client": {
                "id": principal.id,
                "kind": client_kind,
                "queued_jobs": tenant_queued,
                "running_jobs": tenant_running,
                "max_queued": client.max_queued
                if client is not None
                else cfg.default_tenant_max_queued,
                "max_running": client.max_running
                if client is not None
                else cfg.default_tenant_max_running,
            },
        }

    @app.post("/api/v1/batches/imageclip-rgba")
    async def create_imageclip_batch(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        archive: Annotated[UploadFile, File()],
        manifest: Annotated[str, Form()],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> JSONResponse:
        request_started_at = datetime.now(UTC)
        if len(manifest.encode("utf-8")) > 4 * 1024 * 1024:
            raise HTTPException(
                413,
                detail={
                    "code": "BATCH_TOO_LARGE",
                    "message": "manifest 不能超过 4 MiB",
                },
            )
        try:
            parsed_manifest, canonical_manifest, manifest_digest = parse_batch_manifest(
                manifest, cfg
            )
            _validate_parameter_limits(parsed_manifest.parameters)
        except BatchContractError as exc:
            status_code = 413 if exc.code == "BATCH_TOO_LARGE" else 400
            raise HTTPException(
                status_code,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "ordinal": exc.ordinal,
                    "relative_path": exc.relative_path,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                422,
                detail={"code": "INPUT_INVALID", "message": str(exc)},
            ) from exc
        tenant_lock = request.app.state.tenant_locks.setdefault(principal.id, asyncio.Lock())
        async with tenant_lock:
            request_hash = hashlib.sha256(b"imageclip-rgba\x00" + canonical_manifest).hexdigest()

            async def live_idempotency_key(*, lock: bool) -> BatchIdempotencyKey | None:
                statement = select(BatchIdempotencyKey).where(
                    BatchIdempotencyKey.client_id == principal.id,
                    BatchIdempotencyKey.key == idempotency_key,
                )
                if lock:
                    statement = statement.with_for_update()
                row = await db.scalar(statement)
                if row is None:
                    return None
                expires_at = row.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= datetime.now(UTC):
                    # The optimistic lookup runs before the global admission
                    # lock and must not acquire a row lock by deleting here;
                    # doing so would invert global -> row ordering across API
                    # replicas.  The locked re-check performs the deletion.
                    if not lock:
                        return None
                    await db.delete(row)
                    # The expired row still owns the unique (client_id, key)
                    # constraint until it is flushed.  Delete it only after
                    # global -> tenant -> row has been established so the key
                    # can be reused without a cross-replica deadlock window.
                    await db.flush()
                    return None
                return row

            existing_key = await live_idempotency_key(lock=False)
            if existing_key is not None:
                existing_batch = await db.get(JobBatch, existing_key.batch_id)
                if existing_batch is None:
                    raise HTTPException(
                        500,
                        detail={
                            "code": "BATCH_NOT_FOUND",
                            "message": "幂等记录对应批次不存在",
                        },
                    )
                legacy_request_hash = hashlib.sha256(
                    existing_batch.workflow_version.encode() + b"\x00" + canonical_manifest
                ).hexdigest()
                if existing_key.request_hash not in {request_hash, legacy_request_hash}:
                    raise HTTPException(
                        409,
                        detail={
                            "code": "IDEMPOTENCY_CONFLICT",
                            "message": "相同 Idempotency-Key 的批次内容不同",
                        },
                    )
                payload = await batch_payload(existing_batch, db)
                payload.update(
                    {
                        "accepted_bytes": existing_batch.archive_size_bytes,
                        "status_url": f"/api/v1/batches/{existing_batch.id}",
                        "events_url": f"/api/v1/batches/{existing_batch.id}/events",
                        "manifest_url": f"/api/v1/batches/{existing_batch.id}/manifest",
                    }
                )
                return JSONResponse(payload, 200)
            same_external = await db.scalar(
                select(JobBatch).where(
                    JobBatch.tenant_id == principal.id,
                    JobBatch.external_batch_id == parsed_manifest.external_batch_id,
                )
            )
            if same_external is not None:
                raise HTTPException(
                    409,
                    detail={
                        "code": "EXTERNAL_BATCH_CONFLICT",
                        "message": "external_batch_id 已被其他幂等请求使用",
                    },
                )
            workflow = await db.scalar(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.workflow_key == "imageclip-rgba",
                    WorkflowVersion.enabled.is_(True),
                )
                .order_by(WorkflowVersion.created_at.desc())
            )
            if workflow is None:
                raise HTTPException(
                    404,
                    detail={
                        "code": "WORKFLOW_NOT_FOUND",
                        "message": "ImageClip RGBA 工作流未启用",
                    },
                )
            try:
                workflow_identity = workflow_identity_from_row(workflow)
            except BatchContractError as exc:
                raise HTTPException(
                    409,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            try:
                render_parameters = dict(parsed_manifest.parameters)
                render_parameters["image_filename"] = "batch-validation/input.png"
                render_workflow(
                    workflow_manifest_from_row(workflow), workflow.template, render_parameters
                )
            except Exception as exc:
                raise HTTPException(
                    422,
                    detail={
                        "code": "WORKFLOW_RENDER_FAILED",
                        "message": str(exc),
                    },
                ) from exc
            batch_id = str(uuid.uuid4())
            storage: LocalJobStorage = request.app.state.storage
            staging = storage.create_batch_staging_layout(batch_id)
            root: Path | None = None
            try:

                async def archive_chunks() -> AsyncIterator[bytes]:
                    while chunk := await archive.read(1024 * 1024):
                        yield chunk

                try:
                    archive_size, archive_digest = await storage.stream_to_file(
                        archive_chunks(), staging / "archive.zip", cfg.batch_max_archive_bytes
                    )
                except StorageError as exc:
                    raise BatchContractError("BATCH_TOO_LARGE", str(exc)) from exc
                extracted = await asyncio.to_thread(
                    extract_batch_archive,
                    staging / "archive.zip",
                    staging / "input",
                    parsed_manifest,
                    cfg,
                )
                storage.atomic_json(
                    staging / "manifest.request.json",
                    parsed_manifest.model_dump(mode="json"),
                )
                storage.atomic_json(staging / "workflow.identity.json", workflow_identity)
                batch_now = datetime.now(UTC)
                root = storage.promote_batch_staging(staging, batch_id, batch_now)
                request.state.uncommitted_batch_root = root
                request.state.uncommitted_batch_id = batch_id
            except BatchContractError as exc:
                storage.remove_tree(staging)
                status_code = 413 if exc.code == "BATCH_TOO_LARGE" else 422
                raise HTTPException(
                    status_code,
                    detail={
                        "code": exc.code,
                        "message": str(exc),
                        "ordinal": exc.ordinal,
                        "relative_path": exc.relative_path,
                    },
                ) from exc
            except Exception:
                storage.remove_tree(staging)
                raise
            # Archive validation is intentionally outside the global admission
            # lock.  Re-check identity and uniqueness after taking the shared
            # global -> tenant lock order, then keep that transaction open
            # through insertion/commit.  This lets a production request win
            # atomically without a large test archive blocking all admissions.
            await request.app.state.db.acquire_global_admission_transaction_lock(db)
            await request.app.state.db.acquire_tenant_transaction_lock(db, principal.id)
            locked_existing_key = await live_idempotency_key(lock=True)
            if locked_existing_key is not None:
                existing_batch = await db.get(JobBatch, locked_existing_key.batch_id)
                if existing_batch is None:
                    storage.remove_tree(root)
                    raise HTTPException(
                        500,
                        detail={
                            "code": "BATCH_NOT_FOUND",
                            "message": "幂等记录对应批次不存在",
                        },
                    )
                legacy_request_hash = hashlib.sha256(
                    existing_batch.workflow_version.encode() + b"\x00" + canonical_manifest
                ).hexdigest()
                if locked_existing_key.request_hash not in {
                    request_hash,
                    legacy_request_hash,
                }:
                    storage.remove_tree(root)
                    raise HTTPException(
                        409,
                        detail={
                            "code": "IDEMPOTENCY_CONFLICT",
                            "message": "相同 Idempotency-Key 的批次内容不同",
                        },
                    )
                storage.remove_tree(root)
                payload = await batch_payload(existing_batch, db)
                payload.update(
                    {
                        "accepted_bytes": existing_batch.archive_size_bytes,
                        "status_url": f"/api/v1/batches/{existing_batch.id}",
                        "events_url": f"/api/v1/batches/{existing_batch.id}/events",
                        "manifest_url": f"/api/v1/batches/{existing_batch.id}/manifest",
                    }
                )
                return JSONResponse(payload, 200)
            locked_same_external = await db.scalar(
                select(JobBatch).where(
                    JobBatch.tenant_id == principal.id,
                    JobBatch.external_batch_id == parsed_manifest.external_batch_id,
                )
            )
            if locked_same_external is not None:
                storage.remove_tree(root)
                raise HTTPException(
                    409,
                    detail={
                        "code": "EXTERNAL_BATCH_CONFLICT",
                        "message": "external_batch_id 已被其他幂等请求使用",
                    },
                )
            is_load_test = await client_is_load_test(db, principal.id)
            if is_load_test and await active_production_work_exists(db):
                storage.remove_tree(root)
                raise HTTPException(
                    503,
                    detail={
                        "code": "LOAD_TEST_PREEMPTED",
                        "message": "真实生产任务已进入系统，新的压力测试任务已暂停接收",
                        "retryable": True,
                    },
                    headers={"Retry-After": "5"},
                )
            if is_load_test:
                queued = int(
                    await db.scalar(
                        select(func.count(Job.id)).where(
                            Job.status == JobStatus.QUEUED.value
                        )
                    )
                    or 0
                )
                if queued >= cfg.test_system_max_queued:
                    storage.remove_tree(root)
                    raise HTTPException(
                        429,
                        detail={
                            "code": "RATE_LIMITED",
                            "message": "测试队列已达到生产保留容量边界",
                            "reason": "PRODUCTION_QUEUE_RESERVED",
                            "retryable": True,
                        },
                    )
            trace_id = uuid.uuid4().hex
            batch = JobBatch(
                id=batch_id,
                tenant_id=principal.id,
                external_batch_id=parsed_manifest.external_batch_id,
                workflow_key=workflow.workflow_key,
                workflow_version=workflow.version,
                pipeline_commit=workflow_identity["pipeline_commit"],
                pipeline_sha256=workflow_identity["pipeline_sha256"],
                output_node=workflow_identity["output_node"],
                status=BatchStatus.VALIDATING.value,
                failure_policy=parsed_manifest.failure_policy,
                output_naming=parsed_manifest.output_naming,
                parameters=parsed_manifest.parameters,
                request_hash=request_hash,
                request_id=str(request.state.request_id),
                trace_id=trace_id,
                batch_dir=str(root),
                manifest_sha256=manifest_digest,
                archive_sha256=archive_digest,
                archive_size_bytes=archive_size,
                total_items=len(extracted),
                pending_items=len(extracted),
                created_at=request_started_at,
                validated_at=batch_now,
                updated_at=batch_now,
            )
            db.add(batch)
            await db.flush()
            db.add_all(
                [
                    JobBatchItem(
                        id=str(uuid.uuid4()),
                        batch_id=batch.id,
                        ordinal=frame.ordinal,
                        input_relative_path=frame.input_relative_path,
                        output_relative_path=frame.output_relative_path,
                        input_size_bytes=frame.size_bytes,
                        input_sha256=frame.sha256,
                        width=frame.width,
                        height=frame.height,
                        image_format=frame.image_format,
                    )
                    for frame in extracted
                ]
            )
            db.add(
                BatchIdempotencyKey(
                    client_id=principal.id,
                    key=idempotency_key,
                    request_hash=request_hash,
                    batch_id=batch.id,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
            await transition_batch(db, batch, BatchStatus.QUEUED, "batch.queued")
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                if root is not None:
                    storage.remove_tree(root)
                raise HTTPException(
                    409,
                    detail={
                        "code": "BATCH_CONFLICT",
                        "message": "批次幂等键或外部 ID 已存在",
                    },
                ) from exc
            request.state.uncommitted_batch_root = None
            request.state.uncommitted_batch_id = None
            await _notify(
                request.app,
                "gpu-control:wakeup",
                {"event": "batch.queued", "batch_id": batch.id},
            )
            payload = await batch_payload(batch, db)
            payload.update(
                {
                    "accepted_bytes": batch.archive_size_bytes,
                    "status_url": f"/api/v1/batches/{batch.id}",
                    "events_url": f"/api/v1/batches/{batch.id}/events",
                    "manifest_url": f"/api/v1/batches/{batch.id}/manifest",
                }
            )
            return JSONResponse(payload, 202)

    @app.get("/api/v1/batches/{batch_id}")
    async def get_batch(
        batch_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        return await batch_payload(await owned_batch(batch_id, principal, db), db)

    @app.get("/api/v1/batches/{batch_id}/manifest")
    async def get_batch_manifest(
        batch_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        offset: int = 0,
        limit: int = 200,
        status: str | None = None,
    ) -> dict[str, Any]:
        batch = await owned_batch(batch_id, principal, db)
        query = (
            select(JobBatchItem)
            .where(JobBatchItem.batch_id == batch.id)
            .order_by(JobBatchItem.ordinal)
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        if status:
            query = query.where(JobBatchItem.status == status)
        rows = (await db.scalars(query)).all()
        return {
            "batch_id": batch.id,
            "external_batch_id": batch.external_batch_id,
            "workflow_key": batch.workflow_key,
            "workflow_version": batch.workflow_version,
            "pipeline_commit": batch.pipeline_commit,
            "pipeline_sha256": batch.pipeline_sha256,
            "output_node": batch.output_node,
            "total": batch.total_items,
            "offset": max(offset, 0),
            "items": [
                {
                    "ordinal": item.ordinal,
                    "input_relative_path": item.input_relative_path,
                    "output_relative_path": item.output_relative_path,
                    "input_sha256": item.input_sha256,
                    "output_sha256": item.output_sha256,
                    "status": item.status,
                    "job_id": item.job_id,
                    "node_id": item.node_id,
                    "attempts": item.attempts,
                    "error": {"code": item.error_code, "message": item.error_message}
                    if item.error_code
                    else None,
                }
                for item in rows
            ],
        }

    @app.get("/api/v1/batches/{batch_id}/events")
    async def batch_events(
        batch_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> StreamingResponse:
        await owned_batch(batch_id, principal, db)

        async def stream() -> AsyncIterator[str]:
            sequence = 0
            while True:
                async with app.state.db.session() as event_db:
                    events = (
                        await event_db.scalars(
                            select(BatchEvent)
                            .where(
                                BatchEvent.batch_id == batch_id,
                                BatchEvent.sequence > sequence,
                            )
                            .order_by(BatchEvent.sequence)
                        )
                    ).all()
                    terminal = False
                    for item in events:
                        sequence = item.sequence
                        data = json.dumps(
                            {
                                "status": item.status,
                                "event": item.event,
                                "details": item.details,
                            }
                        )
                        yield f"id: {sequence}\nevent: batch\ndata: {data}\n\n"
                        terminal = BatchStatus(item.status) in TERMINAL_BATCH_STATUSES
                    if terminal:
                        return
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/batches/{batch_id}/artifacts/{artifact_id}")
    async def batch_artifact_file(
        batch_id: str,
        artifact_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> FileResponse:
        batch = await owned_batch(batch_id, principal, db)
        if batch.status != BatchStatus.SUCCEEDED.value:
            raise HTTPException(
                409,
                detail={
                    "code": "ARTIFACT_NOT_READY",
                    "message": "批次完整成功前不提供结果包",
                },
            )
        artifact = await db.scalar(
            select(BatchArtifact).where(
                BatchArtifact.id == artifact_id, BatchArtifact.batch_id == batch.id
            )
        )
        if artifact is None:
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        path = (Path(batch.batch_dir) / artifact.relative_path).resolve()
        if Path(batch.batch_dir).resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=artifact.filename,
            headers={
                "X-Artifact-SHA256": artifact.sha256,
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/v1/batches/{batch_id}/cancel")
    async def cancel_batch(
        batch_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(explicit_api_key_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=192)
        ],
        body: BatchCancelRequest | None = None,
    ) -> dict[str, Any]:
        batch = await db.scalar(
            select(JobBatch)
            .where(JobBatch.id == batch_id, JobBatch.tenant_id == principal.id)
            .with_for_update()
        )
        if batch is None:
            raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND", "message": "批次不存在"})
        expected_key = f"{batch.external_batch_id}:cancel"
        if idempotency_key != expected_key:
            raise HTTPException(
                409,
                detail={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": f"取消幂等键必须为 {expected_key}",
                },
            )
        existing = await db.scalar(
            select(BatchCancelOperation).where(
                BatchCancelOperation.tenant_id == principal.id,
                BatchCancelOperation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.batch_id != batch.id:
                raise HTTPException(
                    409,
                    detail={
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "取消幂等键已用于其他批次",
                    },
                )
            return await cancel_response_payload(batch, db)
        existing_for_batch = await db.scalar(
            select(BatchCancelOperation).where(BatchCancelOperation.batch_id == batch.id)
        )
        if existing_for_batch is not None:
            if existing_for_batch.idempotency_key != idempotency_key:
                raise HTTPException(
                    409,
                    detail={
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": "批次已经存在另一取消操作",
                    },
                )
            return await cancel_response_payload(batch, db)
        if BatchStatus(batch.status) in TERMINAL_BATCH_STATUSES:
            raise HTTPException(
                409,
                detail={
                    "code": "BATCH_NOT_CANCELLABLE",
                    "message": "没有既有取消操作的终态批次不可取消",
                },
            )
        now = datetime.now(UTC)
        reason = body.reason if body is not None else "client_request"
        previous_status = batch.status
        operation = BatchCancelOperation(
            id=str(uuid.uuid4()),
            batch_id=batch.id,
            tenant_id=principal.id,
            idempotency_key=idempotency_key,
            request_id=str(request.state.request_id),
            requested_by=principal.id,
            source="public_api",
            source_ip=request.client.host if request.client else "",
            reason=reason,
            status="REQUESTED",
            requested_at=now,
            accepted_at=now,
            completed_items=batch.succeeded_items + batch.failed_items,
            cancelled_items=batch.cancelled_items,
            not_started_items=batch.pending_items + batch.queued_items,
        )
        db.add(operation)
        batch.cancel_requested = True
        if batch.status != BatchStatus.CANCELLING.value:
            await transition_batch(db, batch, BatchStatus.CANCELLING, "batch.cancel_requested")
        await audit(
            db,
            request,
            principal,
            "batch.cancel",
            "batch",
            batch.id,
            {"status": previous_status, "cancel_requested": False},
            {
                "status": batch.status,
                "cancel_requested": True,
                "cancel_operation_id": operation.id,
                "idempotency_key": operation.idempotency_key,
                "reason": operation.reason,
                "source": operation.source,
            },
        )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            replay = await db.scalar(
                select(BatchCancelOperation).where(
                    BatchCancelOperation.tenant_id == principal.id,
                    BatchCancelOperation.idempotency_key == idempotency_key,
                )
            )
            if replay is None or replay.batch_id != batch_id:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"}) from exc
            replay_batch = await owned_batch(batch_id, principal, db)
            return await cancel_response_payload(replay_batch, db)
        await _notify(
            request.app,
            "gpu-control:wakeup",
            {"event": "batch.cancel", "batch_id": batch.id},
        )
        return await cancel_response_payload(batch, db)

    async def owned_job(job_id: str, principal: Principal, db: AsyncSession) -> Job:
        job = await db.get(Job, job_id)
        if job is None or job.tenant_id != principal.id:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": "任务不存在"})
        return job

    async def reject_batch_child_cancel(
        job: Job,
        request: Request,
        principal: Principal,
        db: AsyncSession,
        *,
        source: str,
    ) -> None:
        if job.batch_id is None:
            return
        await audit(
            db,
            request,
            principal,
            "job.cancel.rejected_batch_child",
            "job",
            job.id,
            {"status": job.status, "batch_id": job.batch_id},
            {
                "result": "REJECTED",
                "reason": "batch-owned jobs can only be cancelled through the parent batch",
                "source": source,
                "parent_cancel_url": f"/api/v1/batches/{job.batch_id}/cancel",
            },
            result="REJECTED",
        )
        await db.commit()
        raise HTTPException(
            409,
            detail={
                "code": "BATCH_CHILD_CANCEL_FORBIDDEN",
                "message": "批次子任务只能通过父批次取消接口取消",
                "batch_id": job.batch_id,
                "cancel_url": f"/api/v1/batches/{job.batch_id}/cancel",
            },
        )

    async def require_parent_artifact_ready(job: Job, db: AsyncSession) -> None:
        if job.batch_id is None:
            return
        parent = await db.get(JobBatch, job.batch_id)
        if parent is None or parent.status != BatchStatus.SUCCEEDED.value:
            raise HTTPException(
                409,
                detail={
                    "code": "ARTIFACT_NOT_READY",
                    "message": "父批次完整成功前不提供子任务产物",
                    "batch_id": job.batch_id,
                },
            )

    def job_payload(job: Job) -> dict[str, Any]:
        return {
            "kind": "job",
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
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
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
        job = await owned_job(job_id, principal, db)
        await require_parent_artifact_ready(job, db)
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
        await require_parent_artifact_ready(job, db)
        artifact = await db.scalar(
            select(JobArtifact).where(JobArtifact.id == artifact_id, JobArtifact.job_id == job_id)
        )
        if artifact is None:
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        path = (Path(job.job_dir) / artifact.relative_path).resolve()
        if Path(job.job_dir).resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=path.name,
            headers={
                "X-Artifact-SHA256": artifact.sha256,
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        job = await owned_job(job_id, principal, db)
        await reject_batch_child_cancel(job, request, principal, db, source="public_api")
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
        client_kind: Literal["production", "test", "all"] = "production",
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        scoped_client_ids = select(ApiClient.id).where(ApiClient.role == "client")
        if client_kind != "all":
            scoped_client_ids = scoped_client_ids.where(ApiClient.client_kind == client_kind)
        rows = (
            await db.execute(
                select(Job.status, func.count(Job.id))
                .where(
                    Job.batch_id.is_(None),
                    Job.tenant_id.in_(scoped_client_ids),
                )
                .group_by(Job.status)
            )
        ).all()
        counts: dict[str, int] = {str(status): int(count) for status, count in rows}
        batch_status_rows = (
            await db.execute(
                select(JobBatch.status, func.count(JobBatch.id))
                .where(JobBatch.tenant_id.in_(scoped_client_ids))
                .group_by(JobBatch.status)
            )
        ).all()
        batch_dashboard_status = {
            BatchStatus.VALIDATING.value: JobStatus.QUEUED.value,
            BatchStatus.QUEUED.value: JobStatus.QUEUED.value,
            BatchStatus.RUNNING.value: JobStatus.RUNNING.value,
            BatchStatus.ASSEMBLING.value: JobStatus.RUNNING.value,
            BatchStatus.CANCELLING.value: JobStatus.CANCELLING.value,
            BatchStatus.SUCCEEDED.value: JobStatus.SUCCEEDED.value,
            BatchStatus.CANCELLED.value: JobStatus.CANCELLED.value,
            BatchStatus.FAILED.value: JobStatus.FAILED.value,
        }
        for batch_status, count in batch_status_rows:
            mapped = batch_dashboard_status[str(batch_status)]
            counts[mapped] = counts.get(mapped, 0) + int(count)
        today_rows = (
            await db.execute(
                select(Job.status, func.count(Job.id))
                .where(
                    Job.batch_id.is_(None),
                    Job.created_at >= today,
                    Job.tenant_id.in_(scoped_client_ids),
                )
                .group_by(Job.status)
            )
        ).all()
        today_counts = {str(status): int(count) for status, count in today_rows}
        today_batch_rows = (
            await db.execute(
                select(JobBatch.status, func.count(JobBatch.id))
                .where(
                    JobBatch.created_at >= today,
                    JobBatch.tenant_id.in_(scoped_client_ids),
                )
                .group_by(JobBatch.status)
            )
        ).all()
        for batch_status, count in today_batch_rows:
            mapped = batch_dashboard_status[str(batch_status)]
            today_counts[mapped] = today_counts.get(mapped, 0) + int(count)
        for terminal in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value):
            counts[terminal] = today_counts.get(terminal, 0)
        oldest = await db.scalar(
            select(func.min(Job.created_at)).where(
                Job.batch_id.is_(None),
                Job.status == JobStatus.QUEUED.value,
                Job.tenant_id.in_(scoped_client_ids),
            )
        )
        oldest_batch = await db.scalar(
            select(func.min(JobBatch.created_at)).where(
                JobBatch.status.in_([BatchStatus.VALIDATING.value, BatchStatus.QUEUED.value]),
                JobBatch.tenant_id.in_(scoped_client_ids),
            )
        )
        if oldest_batch is not None and (
            oldest is None
            or oldest_batch.replace(tzinfo=oldest_batch.tzinfo or UTC)
            < oldest.replace(tzinfo=oldest.tzinfo or UTC)
        ):
            oldest = oldest_batch
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        oldest_wait_seconds = max(0, int((now - oldest).total_seconds())) if oldest else 0
        durations = list(
            (
                await db.scalars(
                    select(Job)
                    .where(
                        Job.status == JobStatus.SUCCEEDED.value,
                        Job.batch_id.is_(None),
                        Job.tenant_id.in_(scoped_client_ids),
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
        batch_durations = list(
            (
                await db.scalars(
                    select(JobBatch)
                    .where(
                        JobBatch.status == BatchStatus.SUCCEEDED.value,
                        JobBatch.tenant_id.in_(scoped_client_ids),
                        JobBatch.started_at.is_not(None),
                        JobBatch.finished_at.is_not(None),
                    )
                    .order_by(JobBatch.finished_at.desc())
                    .limit(50)
                )
            ).all()
        )
        duration_values.extend(
            max(0, (batch.finished_at - batch.started_at).total_seconds())
            for batch in batch_durations
            if batch.finished_at is not None and batch.started_at is not None
        )
        nodes = (await db.scalars(select(Node).order_by(Node.pool, Node.id))).all()
        workers = sum(
            node.mode == NodeMode.ACTIVE.value and node.health == "ONLINE" for node in nodes
        )
        average_duration = sum(duration_values) / len(duration_values) if duration_values else 0
        estimated_clear_seconds = (
            int(counts.get(JobStatus.QUEUED.value, 0) * average_duration / workers)
            if workers and average_duration
            else None
        )
        trend_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
        created_times = list(
            (
                await db.scalars(
                    select(Job.created_at).where(
                        Job.batch_id.is_(None),
                        Job.created_at >= trend_start,
                        Job.tenant_id.in_(scoped_client_ids),
                    )
                )
            ).all()
        )
        created_times.extend(
            (
                await db.scalars(
                    select(JobBatch.created_at).where(
                        JobBatch.created_at >= trend_start,
                        JobBatch.tenant_id.in_(scoped_client_ids),
                    )
                )
            ).all()
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
            "client_kind": client_kind,
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
        client_kind: Literal["production", "test", "all"] = "production",
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        if active_only and status:
            raise HTTPException(
                422,
                detail={
                    "code": "AMBIGUOUS_JOB_SCOPE",
                    "message": "active_only 与 status 不能同时使用",
                },
            )
        bounded_limit = min(max(limit, 1), 500)
        scoped_client_ids = select(ApiClient.id).where(ApiClient.role == "client")
        if client_kind != "all":
            scoped_client_ids = scoped_client_ids.where(ApiClient.client_kind == client_kind)
        job_scope = [Job.batch_id.is_(None)]
        batch_scope: list[Any] = []
        if client_kind != "all":
            job_scope.append(Job.tenant_id.in_(scoped_client_ids))
            batch_scope.append(JobBatch.tenant_id.in_(scoped_client_ids))
        # client_kind=all is a safety/audit scope and must include orphaned or
        # unknown tenants. The response marks their owner as production so the
        # load watchdog stops instead of silently ignoring schedulable work.
        query = select(Job).where(*job_scope).order_by(Job.created_at.desc()).limit(bounded_limit)
        if active_only:
            query = query.where(~Job.status.in_([item.value for item in TERMINAL_JOB_STATUSES]))
        elif status:
            query = query.where(Job.status == status)
        job_rows = list((await db.scalars(query)).all())
        batch_query = (
            select(JobBatch)
            .where(*batch_scope)
            .order_by(JobBatch.created_at.desc())
            .limit(bounded_limit)
        )
        if active_only:
            batch_query = batch_query.where(
                ~JobBatch.status.in_([item.value for item in TERMINAL_BATCH_STATUSES])
            )
        elif status:
            batch_query = batch_query.where(JobBatch.status == status)
        batch_rows = list((await db.scalars(batch_query)).all())
        tenant_ids = {row.tenant_id for row in job_rows} | {row.tenant_id for row in batch_rows}
        clients = (
            list((await db.scalars(select(ApiClient).where(ApiClient.id.in_(tenant_ids)))).all())
            if tenant_ids
            else []
        )
        client_by_id = {row.id: row for row in clients}
        rows: list[dict[str, Any]] = []
        for job in job_rows:
            payload = job_payload(job)
            owner = client_by_id.get(job.tenant_id)
            payload.update(
                {
                    "tenant_id": job.tenant_id,
                    "client_kind": owner.client_kind if owner else "production",
                    "request_id": job.request_id,
                }
            )
            rows.append(payload)
        for batch in batch_rows:
            payload = await batch_payload(batch, db, admin=True)
            owner = client_by_id.get(batch.tenant_id)
            payload.update(
                {
                    "kind": "batch",
                    "job_id": batch.id,
                    "tenant_id": batch.tenant_id,
                    "client_kind": owner.client_kind if owner else "production",
                    "priority": Priority.BATCH.value,
                    "node_id": None,
                    "prompt_id": None,
                    "attempt": int(
                        await db.scalar(
                            select(func.coalesce(func.sum(JobBatchItem.attempts), 0)).where(
                                JobBatchItem.batch_id == batch.id
                            )
                        )
                        or 0
                    ),
                }
            )
            rows.append(payload)
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[:bounded_limit]

    @app.get("/admin/asset-processing")
    async def admin_asset_processing(
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        limit: int = 100,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """Expose the Blender queue without mixing it into GPU jobs."""
        bounded_limit = min(max(limit, 1), 500)
        now = datetime.now(UTC)
        heartbeat_cutoff = now - timedelta(seconds=cfg.asset_worker_heartbeat_timeout_seconds)

        def utc_value(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

        def elapsed_seconds(job: AssetJob) -> int:
            started = utc_value(job.started_at)
            if started is None:
                return 0
            terminal = job.status in {
                "SUCCEEDED",
                "WAITING_REVIEW",
                "REVIEW_REJECTED",
                "FAILED",
                "CANCELLED",
            }
            ended = utc_value(job.finished_at or job.last_progress_at) if terminal else now
            return max(0, int(((ended or now) - started).total_seconds()))

        workers = list((await db.scalars(select(AssetWorker).order_by(AssetWorker.id))).all())
        jobs_query = select(AssetJob)
        if active_only:
            # Unknown/future states are deliberately returned. The load-test
            # watchdog validates known active states and therefore fails
            # closed instead of silently hiding a new non-terminal state.
            jobs_query = jobs_query.where(~AssetJob.status.in_(TERMINAL_ASSET_WORK_STATUSES))
        jobs = list(
            (
                await db.scalars(
                    jobs_query.order_by(AssetJob.created_at.desc()).limit(bounded_limit)
                )
            ).all()
        )
        substance_node = await db.get(Node, SUBSTANCE_GPU_NODE_ID)
        substance_labels = dict(substance_node.labels or {}) if substance_node is not None else {}
        pending_substance_job_ids, pending_substance_expires_at = substance_pending_reservation(
            substance_labels, now
        )
        substance_reservation_owned = (
            substance_node is not None
            and substance_labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
        )
        if not substance_reservation_owned:
            pending_substance_job_ids = []
        fenced_substance_job_ids = substance_fence_job_ids(substance_labels)

        def substance_resource_wait(job: AssetJob) -> dict[str, Any] | None:
            if job.job_type != "SUBSTANCE_BAKE_V1" or job.status not in {
                "QUEUED",
                "CLAIMED",
                "RUNNING",
            }:
                return None

            comfyui_current_jobs = (
                int(substance_node.current_jobs) if substance_node is not None else 0
            )
            reservation_active = job.id in pending_substance_job_ids
            fence_active = job.id in fenced_substance_job_ids
            if fence_active:
                code = "SUBSTANCE_GPU_ACTIVE"
                message = "3090-B 已切换至 Windows Substance Baker，烘焙正在执行"
            elif reservation_active and comfyui_current_jobs > 0:
                code = "WAITING_FOR_COMFYUI_FRAME"
                message = "已获 3090-B 下一轮优先权，等待当前 ComfyUI 帧安全结束后切换烘焙"
            elif reservation_active:
                code = "WAITING_FOR_BAKER_CLAIM"
                message = "已获 3090-B 下一轮优先权，等待 Windows Baker 领取任务"
            elif substance_node is None or substance_node.health != "ONLINE":
                code = "WAITING_FOR_3090B_ONLINE"
                message = "等待 3090-B 恢复在线；尚未取得物理 GPU 执行权"
            elif substance_labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL):
                code = "WAITING_FOR_3090B_RECOVERY"
                message = "等待 3090-B 完成安全恢复确认；尚未取得物理 GPU 执行权"
            elif substance_node.manual_reserved:
                code = "WAITING_FOR_ADMINISTRATIVE_RELEASE"
                message = "3090-B 当前由管理员保留；等待释放后取得烘焙执行权"
            elif substance_node.external_busy:
                code = "WAITING_FOR_EXTERNAL_GPU_ACTIVITY"
                message = "3090-B 检测到外部 GPU 活动；等待资源安全释放"
            elif substance_node.foreign_queue_detected:
                code = "WAITING_FOR_FOREIGN_COMFYUI_QUEUE"
                message = "3090-B 检测到未纳管的 ComfyUI 队列；等待队列安全收口"
            elif comfyui_current_jobs > 0:
                code = "WAITING_FOR_3090B_RESERVATION"
                message = "等待取得 3090-B 下一轮执行权；当前 ComfyUI 帧仍在运行"
            else:
                code = "WAITING_FOR_BAKER_CLAIM"
                message = "等待 3090-B Windows Baker 领取任务"

            return {
                "code": code,
                "message": message,
                "node_id": SUBSTANCE_GPU_NODE_ID,
                "reservation_active": reservation_active,
                "fence_active": fence_active,
                "comfyui_current_jobs": comfyui_current_jobs,
            }

        job_ids = [job.id for job in jobs]
        artifacts = (
            list(
                (
                    await db.scalars(
                        select(AssetArtifact)
                        .where(AssetArtifact.job_id.in_(job_ids))
                        .order_by(AssetArtifact.created_at)
                    )
                ).all()
            )
            if job_ids
            else []
        )
        artifacts_by_job: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            artifacts_by_job.setdefault(artifact.job_id, []).append(
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "filename": artifact.filename,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "content_type": artifact.content_type,
                    "download_url": (
                        f"/admin/asset-jobs/{artifact.job_id}/artifacts/{artifact.id}"
                    ),
                }
            )
        status_rows = (
            await db.execute(
                select(AssetJob.status, func.count(AssetJob.id)).group_by(AssetJob.status)
            )
        ).all()
        counts = {str(status): int(count) for status, count in status_rows}

        def worker_online(worker: AssetWorker) -> bool:
            heartbeat = worker.last_heartbeat_at
            if heartbeat is None:
                return False
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            return worker.status == "ONLINE" and heartbeat >= heartbeat_cutoff

        online_workers = [worker for worker in workers if worker_online(worker)]
        node_ids = {worker.node_id for worker in online_workers if worker.node_id}
        worker_nodes = (
            list((await db.scalars(select(Node).where(Node.id.in_(node_ids)))).all())
            if node_ids
            else []
        )
        nodes_by_id = {node.id: node for node in worker_nodes}

        def is_substance_worker(worker: AssetWorker) -> bool:
            return worker.id == SUBSTANCE_WORKER_ID or worker.id.startswith(
                SUBSTANCE_WORKER_ID_PREFIX
            )

        schedulable_cpu_workers = [
            worker
            for worker in online_workers
            if not is_substance_worker(worker)
            and worker.node_id in nodes_by_id
            and worker.agent_instance_id is not None
            and worker.agent_started_at is not None
            and linux_asset_claim_allowed(nodes_by_id[worker.node_id], now)
        ]
        substance_physical_available = bool(
            substance_node is not None
            and (
                substance_node.mode == NodeMode.ACTIVE.value
                or (
                    substance_node.mode == NodeMode.DRAINING.value
                    and substance_reservation_owned
                    and (pending_substance_job_ids or fenced_substance_job_ids)
                )
            )
            and substance_node.health == "ONLINE"
            and substance_node.current_jobs == 0
            and not substance_labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL)
            and not substance_node.manual_reserved
            and not substance_node.external_busy
            and not substance_node.foreign_queue_detected
        )
        schedulable_substance_workers = (
            [
                worker
                for worker in online_workers
                if is_substance_worker(worker)
                and worker.node_id == SUBSTANCE_GPU_NODE_ID
                and worker.agent_instance_id is not None
                and worker.agent_started_at is not None
            ]
            if substance_physical_available
            else []
        )
        cpu_total_slots = sum(
            worker.max_concurrency for worker in schedulable_cpu_workers
        )
        cpu_used_slots = sum(worker.current_jobs for worker in schedulable_cpu_workers)
        substance_physical_slots = max(
            0, SUBSTANCE_MAX_PARALLEL - len(fenced_substance_job_ids)
        )
        substance_total_slots = min(
            sum(worker.max_concurrency for worker in schedulable_substance_workers),
            SUBSTANCE_MAX_PARALLEL,
        )
        substance_used_slots = min(
            max(
                sum(worker.current_jobs for worker in schedulable_substance_workers),
                len(fenced_substance_job_ids),
            ),
            substance_total_slots,
        )
        schedulable_workers = [
            *schedulable_cpu_workers,
            *schedulable_substance_workers,
        ]
        return {
            "schema_version": "asset-admin.v4",
            "as_of": now.isoformat(),
            "summary": {
                "counts": counts,
                "online_workers": len(online_workers),
                "schedulable_workers": len(schedulable_workers),
                "reported_total_slots": sum(
                    worker.max_concurrency for worker in online_workers
                ),
                "total_slots": cpu_total_slots + substance_total_slots,
                "used_slots": cpu_used_slots + substance_used_slots,
                "available_slots": max(0, cpu_total_slots - cpu_used_slots)
                + min(
                    max(0, substance_total_slots - substance_used_slots),
                    substance_physical_slots,
                ),
                "qa_failed": counts.get("FAILED", 0),
            },
            "jobs_scope": {
                "active_only": active_only,
                "limit": bounded_limit,
                "returned": len(jobs),
                "saturated": len(jobs) >= bounded_limit,
            },
            "substance_gpu": {
                "node_id": SUBSTANCE_GPU_NODE_ID,
                "health": substance_node.health if substance_node else "OFFLINE",
                "mode": substance_node.mode if substance_node else "UNKNOWN",
                "sharing_policy": "exclusive_turn_with_comfyui",
                "queue_policy": "production_bake_next_turn_priority",
                "comfyui_current_jobs": (int(substance_node.current_jobs) if substance_node else 0),
                "reserved_job_ids": pending_substance_job_ids,
                "reservation_expires_at": (
                    pending_substance_expires_at.isoformat()
                    if pending_substance_job_ids and pending_substance_expires_at is not None
                    else None
                ),
                "active_bake_job_ids": fenced_substance_job_ids,
                "recovery_required": bool(substance_labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL)),
                "manual_reserved": bool(
                    substance_node.manual_reserved if substance_node else False
                ),
                "external_busy": bool(substance_node.external_busy if substance_node else False),
                "foreign_queue_detected": bool(
                    substance_node.foreign_queue_detected if substance_node else False
                ),
                "free_vram_mb": (int(substance_node.free_vram_mb) if substance_node else None),
                "total_vram_mb": (int(substance_node.total_vram_mb) if substance_node else None),
            },
            "workers": [
                {
                    "id": worker.id,
                    "display_name": worker.display_name,
                    "node_id": worker.node_id,
                    "hostname": worker.hostname,
                    "status": "ONLINE" if worker_online(worker) else "OFFLINE",
                    "reported_status": worker.status,
                    "blender_version": worker.blender_version,
                    "skill_version": worker.skill_version,
                    "cpu_count": worker.cpu_count,
                    "current_jobs": worker.current_jobs,
                    "max_concurrency": worker.max_concurrency,
                    "codex_cli_version": worker.codex_cli_version,
                    "codex_auth_status": worker.codex_auth_status,
                    "codex_probe_status": worker.codex_probe_status,
                    "codex_probe_latency_ms": worker.codex_probe_latency_ms,
                    "codex_last_checked_at": worker.codex_last_checked_at.isoformat()
                    if worker.codex_last_checked_at
                    else None,
                    "codex_last_success_at": worker.codex_last_success_at.isoformat()
                    if worker.codex_last_success_at
                    else None,
                    "codex_error_code": worker.codex_error_code,
                    "retopoflow_version": worker.retopoflow_version,
                    "retopoflow_revision": worker.retopoflow_revision,
                    "retopoflow_probe_status": worker.retopoflow_probe_status,
                    "retopoflow_probe_latency_ms": worker.retopoflow_probe_latency_ms,
                    "retopoflow_last_checked_at": worker.retopoflow_last_checked_at.isoformat()
                    if worker.retopoflow_last_checked_at
                    else None,
                    "retopoflow_error_code": worker.retopoflow_error_code,
                    "last_heartbeat_at": worker.last_heartbeat_at.isoformat()
                    if worker.last_heartbeat_at
                    else None,
                }
                for worker in workers
            ],
            "jobs": [
                {
                    "job_id": job.id,
                    "external_asset_id": job.external_asset_id,
                    "client_id": job.client_id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "source_filename": job.source_filename,
                    "input_sha256": job.input_sha256,
                    "options": job.options,
                    "worker_id": job.worker_id,
                    "progress": job.progress,
                    "stage": job.stage,
                    "stage_message": job.stage_message,
                    "resource_wait": substance_resource_wait(job),
                    "timing": {
                        "elapsed_seconds": elapsed_seconds(job),
                        "estimated_remaining_seconds": job.estimated_remaining_seconds,
                        "last_progress_at": job.last_progress_at.isoformat()
                        if job.last_progress_at
                        else None,
                    },
                    "attempt_count": job.attempt_count,
                    "error": (
                        {"code": job.error_code, "message": job.error_message}
                        if job.error_code
                        else None
                    ),
                    "delivery_ready": job.status == "SUCCEEDED",
                    "review_required": False,
                    "artifacts_role": (
                        "diagnostic"
                        if job.status == "FAILED"
                        and job.error_code
                        in {"RETOPOLOGY_AUDIT_FAILED", "RETOPOLOGY_QUALITY_GATE_FAILED"}
                        else "delivery"
                        if job.status == "SUCCEEDED"
                        else "retained"
                    ),
                    "created_at": job.created_at.isoformat(),
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    "artifacts": artifacts_by_job.get(job.id, []),
                }
                for job in jobs
            ],
            "contracts": {
                "uv": {
                    "submit": "/api/v1/assets/uv/process",
                    "formats": [".fbx", ".obj", ".glb", ".gltf", ".blend"],
                    "artifact_count": 5,
                    "status": "/api/v1/assets/jobs/{job_id}",
                    "events": "/api/v1/assets/jobs/{job_id}/events",
                },
                "retopology_audit": {
                    "submit": "/api/v1/assets/retopology/audit",
                    "format": ".blend",
                    "success_status": "SUCCEEDED",
                },
                "retopology_process": {
                    "submit": "/api/v1/assets/retopology/process",
                    "format": ".blend + optional reference images",
                    "success_status": "SUCCEEDED",
                    "delivery_policy": "strict_qa_auto_publish",
                    "views": ["front", "side", "top", "perspective"],
                    "roles": ["high", "reference", "generated"],
                    "reference_views_optional": True,
                    "maximum_reference_views": 32,
                    "status": "/api/v1/assets/jobs/{job_id}",
                    "events": "/api/v1/assets/jobs/{job_id}/events",
                },
                "roughness": {
                    "submit": "/api/v1/services/modelview-roughness",
                    "format": "multipart image",
                    "runtime": "GPU: control-4090 / worker-3090-a / worker-3090-b",
                    "response": "final image/png",
                },
                "substance_bake": {
                    "submit": "/api/v1/assets/bake/process",
                    "format": "low mesh + high mesh + Base Color/Roughness/Metallic textures",
                    "profile": "li3d-pbr-full-v2",
                    "runtime": "native Windows 3090-B",
                    "artifact_count": 12,
                    "status": "/api/v1/assets/jobs/{job_id}",
                    "events": "/api/v1/assets/jobs/{job_id}/events",
                },
            },
        }

    @app.post("/admin/asset-jobs/{job_id}/cancel")
    async def admin_cancel_asset_job(
        job_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        """Cancel a CPU asset job from the unified operations console."""
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        job = await db.get(AssetJob, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        terminal_statuses = {
            "SUCCEEDED",
            "WAITING_REVIEW",
            "REVIEW_REJECTED",
            "FAILED",
            "CANCELLED",
        }
        if job.status not in terminal_statuses:
            before = {"status": job.status, "cancel_requested": job.cancel_requested}
            job.cancel_requested = True
            if job.status == "QUEUED":
                job.status = "CANCELLED"
                job.stage = "CANCELLED"
                job.stage_message = "管理员已在执行前取消任务"
                job.estimated_remaining_seconds = 0
                job.finished_at = datetime.now(UTC)
            else:
                job.status = "CANCELLING"
                job.stage = "CANCELLING"
                job.stage_message = "管理员已请求取消，等待 Worker 到达安全点"
            job.last_progress_at = datetime.now(UTC)
            await append_admin_asset_event(
                db,
                job,
                event="asset.admin_cancel_requested",
                details={"reason": body.reason, "actor": principal.id},
            )
            await audit(
                db,
                request,
                principal,
                "asset_job.cancel",
                "asset_job",
                job.id,
                before,
                {
                    "status": job.status,
                    "cancel_requested": True,
                    "reason": body.reason,
                },
            )
            await db.commit()
        return {
            "job_id": job.id,
            "status": job.status,
            "cancel_requested": job.cancel_requested,
        }

    @app.get("/admin/asset-jobs/{job_id}/artifacts/{artifact_id}")
    async def admin_asset_artifact_file(
        job_id: str,
        artifact_id: str,
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> FileResponse:
        job = await db.get(AssetJob, job_id)
        if job is None:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        artifact = await db.scalar(
            select(AssetArtifact).where(
                AssetArtifact.id == artifact_id,
                AssetArtifact.job_id == job_id,
            )
        )
        if artifact is None:
            raise HTTPException(404, detail={"code": "ASSET_ARTIFACT_NOT_FOUND"})
        path = Path(artifact.path).resolve()
        root = cfg.asset_root.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, detail={"code": "ASSET_ARTIFACT_FILE_MISSING"})
        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=artifact.filename,
            headers={
                "X-Artifact-SHA256": artifact.sha256,
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )

    @app.get("/admin/batches/{batch_id}")
    async def admin_batch_detail(
        batch_id: str,
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        batch = await db.get(JobBatch, batch_id)
        if batch is None:
            raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND"})
        payload = await batch_payload(batch, db, admin=True)
        owner = await db.get(ApiClient, batch.tenant_id)
        payload.update(
            {
                "kind": "batch",
                "job_id": batch.id,
                "tenant_id": batch.tenant_id,
                "client_kind": owner.client_kind if owner else "production",
            }
        )
        return payload

    @app.get("/admin/batches/{batch_id}/items")
    async def admin_batch_items(
        batch_id: str,
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        batch = await db.get(JobBatch, batch_id)
        if batch is None:
            raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND"})
        bounded_offset = max(offset, 0)
        bounded_limit = min(max(limit, 1), 500)
        items = (
            await db.scalars(
                select(JobBatchItem)
                .where(JobBatchItem.batch_id == batch.id)
                .order_by(JobBatchItem.ordinal)
                .offset(bounded_offset)
                .limit(bounded_limit)
            )
        ).all()
        return {
            "batch_id": batch.id,
            "total": batch.total_items,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "items": [
                {
                    "ordinal": item.ordinal,
                    "input_relative_path": item.input_relative_path,
                    "output_relative_path": item.output_relative_path,
                    "status": item.status,
                    "job_id": item.job_id,
                    "node_id": item.node_id,
                    "attempts": item.attempts,
                    "input_sha256": item.input_sha256,
                    "output_sha256": item.output_sha256,
                    "error": {"code": item.error_code, "message": item.error_message}
                    if item.error_code
                    else None,
                }
                for item in items
            ],
        }

    @app.get("/admin/batches/{batch_id}/artifacts/{artifact_id}")
    async def admin_batch_artifact_file(
        batch_id: str,
        artifact_id: str,
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> FileResponse:
        batch = await db.get(JobBatch, batch_id)
        if batch is None:
            raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND"})
        if batch.status != BatchStatus.SUCCEEDED.value:
            raise HTTPException(409, detail={"code": "ARTIFACT_NOT_READY"})
        artifact = await db.scalar(
            select(BatchArtifact).where(
                BatchArtifact.id == artifact_id,
                BatchArtifact.batch_id == batch.id,
            )
        )
        if artifact is None:
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        root = Path(batch.batch_dir).resolve()
        path = (root / artifact.relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, detail={"code": "ARTIFACT_NOT_FOUND"})
        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=artifact.filename,
            headers={
                "X-Artifact-SHA256": artifact.sha256,
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )

    @app.post("/admin/batches/{batch_id}/cancel")
    async def admin_cancel_batch(
        batch_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=192)
        ] = None,
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        batch = await db.get(JobBatch, batch_id, with_for_update=True)
        if batch is None:
            raise HTTPException(404, detail={"code": "BATCH_NOT_FOUND"})
        existing_operation = await db.scalar(
            select(BatchCancelOperation).where(BatchCancelOperation.batch_id == batch.id)
        )
        if existing_operation is not None:
            return await cancel_response_payload(batch, db, admin=True)
        effective_idempotency_key = idempotency_key or f"admin:{batch.id}:cancel"
        operation_for_key = await db.scalar(
            select(BatchCancelOperation).where(
                BatchCancelOperation.tenant_id == batch.tenant_id,
                BatchCancelOperation.idempotency_key == effective_idempotency_key,
            )
        )
        if operation_for_key is not None:
            raise HTTPException(
                409,
                detail={
                    "code": "IDEMPOTENCY_CONFLICT",
                    "message": "取消幂等键已用于其他批次",
                },
            )
        if BatchStatus(batch.status) not in TERMINAL_BATCH_STATUSES:
            before = {"status": batch.status, "cancel_requested": batch.cancel_requested}
            now = datetime.now(UTC)
            operation = BatchCancelOperation(
                id=str(uuid.uuid4()),
                batch_id=batch.id,
                tenant_id=batch.tenant_id,
                idempotency_key=effective_idempotency_key,
                request_id=str(request.state.request_id),
                requested_by=principal.id,
                source="admin_api",
                source_ip=request.client.host if request.client else "",
                reason=body.reason,
                status="REQUESTED",
                requested_at=now,
                accepted_at=now,
                completed_items=batch.succeeded_items + batch.failed_items,
                cancelled_items=batch.cancelled_items,
                not_started_items=batch.pending_items + batch.queued_items,
            )
            db.add(operation)
            batch.cancel_requested = True
            if batch.status != BatchStatus.CANCELLING.value:
                await transition_batch(
                    db, batch, BatchStatus.CANCELLING, "admin.batch_cancel_requested"
                )
            await audit(
                db,
                request,
                principal,
                "batch.cancel",
                "batch",
                batch.id,
                before,
                {
                    "status": batch.status,
                    "cancel_requested": True,
                    "cancel_operation_id": operation.id,
                    "idempotency_key": operation.idempotency_key,
                    "reason": body.reason,
                    "source": operation.source,
                },
            )
            await db.commit()
            await _notify(
                request.app,
                "gpu-control:wakeup",
                {"event": "batch.cancel", "batch_id": batch.id},
            )
        return await cancel_response_payload(batch, db, admin=True)

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
        now = datetime.now(UTC)
        worker_heartbeat_cutoff = now - timedelta(
            seconds=cfg.asset_worker_heartbeat_timeout_seconds
        )
        codex_probe_cutoff = now - timedelta(seconds=cfg.asset_codex_probe_max_age_seconds)

        def utc_value(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

        rows = (await db.scalars(select(Node).order_by(Node.pool, Node.id))).all()
        workers = list(
            (await db.scalars(select(AssetWorker).order_by(AssetWorker.updated_at.desc()))).all()
        )
        codex_jobs = list(
            (
                await db.scalars(
                    select(AssetJob)
                    .where(AssetJob.job_type == "RETOPOLOGY_PROCESS_V1")
                    .order_by(AssetJob.created_at.desc())
                    .limit(500)
                )
            ).all()
        )
        # The Linux/WSL Codex Worker has the deterministic identity
        # ``asset-${node.id}``.  Selecting the newest heartbeat by node can
        # accidentally surface one of 3090-B's Windows Baker processes, which
        # shares the node_id but intentionally reports no Codex runtime.
        worker_by_id = {asset_worker.id: asset_worker for asset_worker in workers}
        codex_task_by_worker: dict[str, AssetJob] = {}
        for job in codex_jobs:
            if not job.worker_id:
                continue
            current = codex_task_by_worker.get(job.worker_id)
            active = job.status in {"CLAIMED", "RUNNING"}
            current_active = bool(current and current.status in {"CLAIMED", "RUNNING"})
            if current is None or (active and not current_active):
                codex_task_by_worker[job.worker_id] = job
        payload: list[dict[str, Any]] = []
        for row in rows:
            item = {column.name: getattr(row, column.name) for column in Node.__table__.columns}
            labels = row.labels or {}
            worker = worker_by_id.get(f"asset-{row.id}")
            installed = bool(labels.get("codex_cli_installed"))
            auth_status = worker.codex_auth_status if worker else "UNKNOWN"
            probe_status = worker.codex_probe_status if worker else "NOT_RUN"
            codex_task = codex_task_by_worker.get(worker.id) if worker else None
            worker_heartbeat_at = utc_value(worker.last_heartbeat_at) if worker else None
            probe_checked_at = utc_value(worker.codex_last_checked_at) if worker else None
            heartbeat_fresh = bool(
                worker
                and worker.status == "ONLINE"
                and worker_heartbeat_at
                and worker_heartbeat_at >= worker_heartbeat_cutoff
            )
            probe_fresh = bool(probe_checked_at and probe_checked_at >= codex_probe_cutoff)
            scheduler_eligible = bool(
                heartbeat_fresh
                and auth_status == "AUTHENTICATED"
                and probe_status == "HEALTHY"
                and probe_fresh
            )
            if scheduler_eligible:
                health = "HEALTHY"
                eligibility_reason = "ELIGIBLE"
            elif worker is None:
                health = "UNAVAILABLE"
                eligibility_reason = "ASSET_WORKER_NOT_REGISTERED"
            elif worker.status != "ONLINE":
                health = "UNAVAILABLE"
                eligibility_reason = "ASSET_WORKER_OFFLINE"
            elif not heartbeat_fresh:
                health = "STALE"
                eligibility_reason = "ASSET_WORKER_HEARTBEAT_STALE"
            elif auth_status != "AUTHENTICATED":
                health = "DEGRADED"
                eligibility_reason = "CODEX_AUTH_NOT_READY"
            elif probe_status == "NOT_RUN":
                health = "CHECKING"
                eligibility_reason = "CODEX_PROBE_NOT_RUN"
            elif probe_status != "HEALTHY":
                health = "DEGRADED"
                eligibility_reason = "CODEX_PROBE_UNHEALTHY"
            elif not probe_fresh:
                health = "STALE"
                eligibility_reason = "CODEX_PROBE_STALE"
            else:
                health = "UNAVAILABLE"
                eligibility_reason = "CODEX_RUNTIME_UNAVAILABLE"
            item["codex_cli"] = {
                "health": health,
                "host_entry_installed": installed,
                "host_version": labels.get("codex_cli_version") or None,
                "runtime_version": worker.codex_cli_version if worker else None,
                "auth_status": auth_status,
                "probe_status": probe_status,
                "probe_latency_ms": worker.codex_probe_latency_ms if worker else None,
                "last_checked_at": worker.codex_last_checked_at.isoformat()
                if worker and worker.codex_last_checked_at
                else None,
                "last_success_at": worker.codex_last_success_at.isoformat()
                if worker and worker.codex_last_success_at
                else None,
                "worker_status": worker.status if worker else None,
                "worker_last_heartbeat_at": (
                    worker.last_heartbeat_at.isoformat()
                    if worker and worker.last_heartbeat_at
                    else None
                ),
                "heartbeat_fresh": heartbeat_fresh,
                "probe_fresh": probe_fresh,
                "eligibility_reason": eligibility_reason,
                "error_code": (
                    worker.codex_error_code
                    if worker and worker.codex_error_code
                    else labels.get("codex_cli_error") or None
                ),
                "task": (
                    {
                        "job_id": codex_task.id,
                        "external_asset_id": codex_task.external_asset_id,
                        "status": codex_task.status,
                        "stage": codex_task.stage,
                        "input": {
                            "filename": codex_task.source_filename,
                            "sha256": codex_task.input_sha256,
                            "high_object": codex_task.options.get("high_object"),
                            "reference_object": codex_task.options.get("reference_object"),
                            "low_object": codex_task.options.get("low_object"),
                            "reference_view_count": len(
                                codex_task.options.get("reference_views") or []
                            ),
                            "user_request": codex_task.options.get("user_request"),
                        },
                        "output_contract": [
                            "retopology_candidate.blend",
                            "retopology_candidate.fbx",
                            "retopology_process_report.json",
                            "retopology_final_audit.json",
                            "retopology_manifest.json",
                            "retopology_agent_prompt.txt",
                            "retopology_agent_events.jsonl",
                            "high/reference/generated × front/side/top/perspective PNG",
                        ],
                        "is_active": codex_task.status in {"CLAIMED", "RUNNING"},
                    }
                    if codex_task
                    else None
                ),
                "scheduler_eligible": scheduler_eligible,
            }
            payload.append(item)
        return payload

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
        substance_interlock = substance_gpu_interlock(node, datetime.now(UTC))
        substance_owner_transferred = take_operator_drain_ownership(node)
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
            {
                "mode": node.mode,
                "active_leases": active_leases,
                "substance_interlock": substance_interlock,
                "substance_owner_transferred": substance_owner_transferred,
                "reason": reason,
            },
        )
        await db.commit()
        if active_leases or node.current_jobs or substance_interlock["active"]:
            raise HTTPException(
                409,
                detail={
                    "code": "NODE_DRAINING",
                    "message": "节点已进入 DRAINING；等待活动任务结束后再次执行操作",
                    "substance_interlock": substance_interlock,
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
        substance_owner_transferred = take_operator_drain_ownership(node)
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
            {
                "mode": node.mode,
                "substance_owner_transferred": substance_owner_transferred,
                "reason": body.reason,
            },
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
        # Discover parent ids without locking child rows, then take locks in
        # the same parent-before-child order used by Scheduler batch sync.
        # Locking Job first and JobBatch second creates a PostgreSQL cycle with
        # Scheduler's JobBatch -> Job transaction.
        hinted_batch_ids = {
            str(batch_id)
            for batch_id in (
                await db.scalars(
                    select(Job.batch_id).where(
                        Job.node_id == node_id,
                        Job.status.in_(ACTIVE_STATUSES),
                        Job.batch_id.is_not(None),
                    )
                )
            ).all()
            if batch_id is not None
        }
        parent_batches: list[JobBatch] = []
        cancel_operations: list[BatchCancelOperation] = []
        try:
            if hinted_batch_ids:
                parent_batches = list(
                    (
                        await db.scalars(
                            select(JobBatch)
                            .where(JobBatch.id.in_(hinted_batch_ids))
                            .order_by(JobBatch.id)
                            .with_for_update(nowait=True)
                        )
                    ).all()
                )
                cancel_operations = list(
                    (
                        await db.scalars(
                            select(BatchCancelOperation).where(
                                BatchCancelOperation.batch_id.in_(hinted_batch_ids)
                            )
                        )
                    ).all()
                )
            jobs = list(
                (
                    await db.scalars(
                        select(Job)
                        .where(Job.node_id == node_id, Job.status.in_(ACTIVE_STATUSES))
                        .order_by(Job.id)
                        .with_for_update(nowait=True)
                    )
                ).all()
            )
        except DBAPIError as exc:
            if not is_postgres_lock_not_available(exc):
                raise
            # Holding Node while waiting on Scheduler-owned Batch/Job rows can
            # form Node <-> Job or Node <-> Batch lock cycles. Fail fast and
            # let the operator retry after the in-flight state transition.
            await db.rollback()
            await audit(
                db,
                request,
                principal,
                "node.interrupt.busy_retry",
                "node",
                node_id,
                before,
                {
                    "result": "REJECTED",
                    "reason": "scheduler-owned batch/job row lock",
                },
                result="REJECTED",
            )
            await db.commit()
            raise HTTPException(
                409,
                detail={
                    "code": "NODE_INTERRUPT_BUSY_RETRY",
                    "message": "节点任务正在提交状态，请稍后重试",
                },
            ) from exc
        batch_ids = {str(job.batch_id) for job in jobs if job.batch_id is not None}
        unexpected_batch_ids = sorted(batch_ids - hinted_batch_ids)
        if unexpected_batch_ids:
            # Never acquire a newly discovered parent lock after child locks.
            # The caller can retry; no task or node state has been mutated.
            await db.rollback()
            await audit(
                db,
                request,
                principal,
                "node.interrupt.retry_required",
                "node",
                node_id,
                before,
                {
                    "result": "REJECTED",
                    "reason": "active batch set changed during lock acquisition",
                    "batch_ids": unexpected_batch_ids,
                },
                result="REJECTED",
            )
            await db.commit()
            raise HTTPException(
                409,
                detail={
                    "code": "NODE_INTERRUPT_RETRY_REQUIRED",
                    "message": "节点任务集合在锁定期间发生变化，请重试",
                    "batch_ids": unexpected_batch_ids,
                },
            )
        if batch_ids:
            parents_by_id = {batch.id: batch for batch in parent_batches}
            operation_batch_ids = {operation.batch_id for operation in cancel_operations}
            unsafe_batch_ids = sorted(
                batch_id
                for batch_id in batch_ids
                if batch_id not in parents_by_id
                or not parents_by_id[batch_id].cancel_requested
                or batch_id not in operation_batch_ids
            )
            if unsafe_batch_ids:
                unsafe_job_ids = sorted(job.id for job in jobs if job.batch_id in unsafe_batch_ids)
                await audit(
                    db,
                    request,
                    principal,
                    "node.interrupt.rejected_batch_child",
                    "node",
                    node_id,
                    before,
                    {
                        "result": "REJECTED",
                        "reason": "batch child requires an explicit parent cancel operation",
                        "batch_ids": unsafe_batch_ids,
                        "job_ids": unsafe_job_ids,
                    },
                    result="REJECTED",
                )
                await db.commit()
                raise HTTPException(
                    409,
                    detail={
                        "code": "BATCH_CHILD_INTERRUPT_FORBIDDEN",
                        "message": "节点包含未通过父批次取消的活动子任务",
                        "batch_ids": unsafe_batch_ids,
                        "job_ids": unsafe_job_ids,
                    },
                )
        substance_owner_transferred = take_operator_drain_ownership(node)
        node.mode = NodeMode.DRAINING.value
        node.manual_reserved = False
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
            {
                "mode": node.mode,
                "jobs": [job.id for job in jobs],
                "substance_owner_transferred": substance_owner_transferred,
                "reason": body.reason,
            },
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
        previous_error = {"code": job.error_code, "message": job.error_message}
        before = {
            "status": job.status,
            "attempt": job.attempt_count,
            "node_id": job.node_id,
            "prompt_id": job.prompt_id,
            "error": previous_error,
        }
        await transition_job(
            db,
            job,
            JobStatus.RETRY_WAIT,
            "admin.retry",
            {"reason": body.reason, "previous_error": previous_error},
        )
        await transition_job(db, job, JobStatus.QUEUED, "admin.requeued")
        job.node_id = None
        job.prompt_id = None
        job.submission_client_id = None
        job.submission_intent_at = None
        job.claimed_at = None
        job.started_at = None
        job.finished_at = None
        job.progress = 0
        job.cancel_requested = False
        job.not_before = None
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
        await reject_batch_child_cancel(job, request, principal, db, source="admin_api")
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
                "client_kind": row.client_kind,
                "enabled": row.enabled,
                "max_queued": row.max_queued,
                "max_running": row.max_running,
                "daily_quota": row.daily_quota,
                "weight": row.weight,
                "allowed_ips": row.allowed_ips,
                "last_seen_ip": row.last_seen_ip,
                "last_seen_at": row.last_seen_at,
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
        if body.allowed_ips:
            clients = list((await db.scalars(select(ApiClient))).all())
            used_ips = {str(value) for row in clients for value in (row.allowed_ips or [])}
            conflicts = sorted(set(body.allowed_ips) & used_ips)
            if conflicts:
                raise HTTPException(
                    409,
                    detail={
                        "code": "CLIENT_IP_CONFLICT",
                        "message": f"来源 IP 已绑定其他客户: {', '.join(conflicts)}",
                    },
                )
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
            client_kind=body.client_kind,
            max_queued=body.max_queued,
            max_running=body.max_running,
            daily_quota=body.daily_quota,
            weight=body.weight,
            allowed_ips=body.allowed_ips,
            callback_hosts=body.callback_hosts,
        )
        db.add(client)
        db.add(RateLimitPolicy(client_id=body.id, requests_per_second=5, burst=10))
        await audit(
            db, request, principal, "client.create", "api_client", body.id, {}, body.model_dump()
        )
        await db.commit()
        return {"id": client.id, "name": client.name}

    @app.put("/admin/clients/{client_id}")
    async def update_client(
        client_id: str,
        body: ClientUpdateRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        # Changing client_kind changes whether new work is production or load
        # traffic.  Serialize that classification change with the same global
        # admission transaction lock used by both job admission paths, then
        # lock the target client row.  The order is always global -> client;
        # this endpoint must not acquire a tenant advisory lock first.
        await request.app.state.db.acquire_global_admission_transaction_lock(db)
        client = await db.get(ApiClient, client_id, with_for_update=True)
        if client is None or client.role != "client":
            raise HTTPException(404, detail={"code": "CLIENT_NOT_FOUND"})
        if body.allowed_ips:
            clients = list((await db.scalars(select(ApiClient))).all())
            used_ips = {
                str(value)
                for row in clients
                if row.id != client_id
                for value in (row.allowed_ips or [])
            }
            conflicts = sorted(set(body.allowed_ips) & used_ips)
            if conflicts:
                raise HTTPException(
                    409,
                    detail={
                        "code": "CLIENT_IP_CONFLICT",
                        "message": f"来源 IP 已绑定其他客户: {', '.join(conflicts)}",
                    },
                )
        for host in body.callback_hosts:
            if any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
                for char in host
            ):
                raise HTTPException(
                    422, detail={"code": "INPUT_INVALID", "message": "回调域名格式错误"}
                )
        before = {
            "name": client.name,
            "client_kind": client.client_kind,
            "enabled": client.enabled,
            "max_queued": client.max_queued,
            "max_running": client.max_running,
            "daily_quota": client.daily_quota,
            "weight": client.weight,
            "allowed_ips": client.allowed_ips,
            "callback_hosts": client.callback_hosts,
        }
        client.name = body.name
        client.client_kind = body.client_kind
        client.enabled = body.enabled
        client.max_queued = body.max_queued
        client.max_running = body.max_running
        client.daily_quota = body.daily_quota
        client.weight = body.weight
        client.allowed_ips = body.allowed_ips
        client.callback_hosts = body.callback_hosts
        after = body.model_dump(exclude={"reason", "confirm"})
        await audit(
            db,
            request,
            principal,
            "client.update",
            "api_client",
            client_id,
            before,
            {**after, "reason": body.reason},
        )
        await db.commit()
        return {"id": client.id, **after}

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
        from packages.gpu_control_core.workflow import node_compatibility_reasons

        for node in nodes:
            reasons = node_compatibility_reasons(
                min_vram_mb=version.min_vram_mb,
                required_labels=version.node_labels,
                allowed_class_types=version.allowed_class_types,
                total_vram_mb=node.total_vram_mb,
                reported_labels=node.labels,
            )
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
                    "alert.delivery_failed",
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
