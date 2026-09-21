import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
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
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
    INTERACTIVE_WORKFLOW_KEYS,
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
    BatchItemStatus,
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
    ProviderInstance,
    ProviderOperation,
    RateLimitPolicy,
    SystemSetting,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import ACTIVE_STATUSES, transition_job
from packages.gpu_control_core.scheduling import (
    GPU_SPECIALIZATION_LABEL,
    IMAGECLIP_INPAINT_PREEMPTION_CODE,
    IMAGECLIP_WORKFLOW_KEY,
    MODELVIEW_INPAINT_NODE_ID,
    MODELVIEW_INPAINT_WORKFLOW_KEY,
    MODELVIEW_MASK_WORKFLOW_KEYS,
    MODELVIEW_SINGLE_VIEW_INPAINT_WORKFLOW_KEY,
    MODELVIEW_SINGLE_VIEW_WORKFLOW_KEY,
    MODELVIEW_WORKFLOW_KEYS,
    SUBSTANCE_DRAIN_OWNER,
    SUBSTANCE_DRAIN_OWNER_LABEL,
    SUBSTANCE_GPU_NODE_ID,
    SUBSTANCE_MAX_PARALLEL,
    SUBSTANCE_RECOVERY_REQUIRED_LABEL,
    SUBSTANCE_SPECIALIZATION_KEY,
    SUBSTANCE_WORKER_ID,
    SUBSTANCE_WORKER_ID_PREFIX,
    linux_asset_claim_allowed,
    refresh_gpu_specialization,
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
    mask_red_channel_has_edit_region,
    safe_filename,
)
from packages.gpu_control_core.workflow import WorkflowManifest, render_workflow


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def utc_aware(value: datetime) -> datetime:
    """Normalize PostgreSQL-aware and SQLite-naive timestamps to UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_postgres_lock_not_available(exc: DBAPIError) -> bool:
    """Recognize PostgreSQL NOWAIT lock conflicts without hiding other DB errors."""

    original = exc.orig
    return (
        getattr(original, "sqlstate", None) == "55P03"
        or getattr(original, "pgcode", None) == "55P03"
    )


def canonical_load_session_id(value: str) -> str:
    """Return one canonical UUIDv4 load-session identifier or reject it."""

    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            422,
            detail={"code": "LOAD_SESSION_INVALID", "message": "压测 session 必须是 UUIDv4"},
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise HTTPException(
            422,
            detail={"code": "LOAD_SESSION_INVALID", "message": "压测 session 必须是规范 UUIDv4"},
        )
    return value


REQUESTS = Counter(
    "gpu_control_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
DURATION = Histogram(
    "gpu_control_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)

# ComfyUI's browser-side randomizer uses integers that remain exact in JSON and
# JavaScript.  Keep the server-generated value in the same 50-bit range while
# using a cryptographically strong generator so separate jobs do not share the
# fixed seed saved in the UI workflow.
MODELVIEW_NOISE_SEED_EXCLUSIVE_MAX = 1 << 50
MODELVIEW_NOISE_SEED_PARAMETER = "noise_seed"


def service_queue_policy(workflow_key: str) -> tuple[Priority, bool]:
    """Return the server-owned scheduling class for a synchronous service."""

    if workflow_key in INTERACTIVE_WORKFLOW_KEYS:
        return Priority.CRITICAL, True
    return Priority.NORMAL, False


def inject_server_owned_workflow_parameters(
    workflow_key: str,
    bindings: dict[str, Any],
    parameters: dict[str, Any],
) -> None:
    """Add execution parameters that callers must not choose themselves.

    The binding check keeps a rolling deployment compatible with the previous
    ModelView workflow version: API code may be updated before the new immutable
    workflow is enabled without breaking jobs that still use the old template.
    """

    if (
        workflow_key not in MODELVIEW_WORKFLOW_KEYS
        or MODELVIEW_NOISE_SEED_PARAMETER not in bindings
    ):
        return
    if MODELVIEW_NOISE_SEED_PARAMETER in parameters:
        raise ValueError("noise_seed 由任务中心为每个新任务生成，客户端不能传入")
    parameters[MODELVIEW_NOISE_SEED_PARAMETER] = secrets.randbelow(
        MODELVIEW_NOISE_SEED_EXCLUSIVE_MAX
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


def provider_operation_request(
    product: Literal["app", "pro"],
    instance_id: str,
    desired_state: Literal["running", "stopped"],
    operation_id: str,
    *,
    should_dispatch: bool,
    bootstrap_profile: str | None,
) -> tuple[Literal["GET", "POST"], str, dict[str, str] | None]:
    """Build one fenced provider request without ever carrying a shell command."""

    path = f"/internal/v1/providers/autodl/instances/{product}/{instance_id}/state"
    if not should_dispatch:
        return "GET", path, None
    payload = {"desired_state": desired_state, "correlation_id": operation_id}
    if desired_state == "running" and bootstrap_profile is not None:
        if bootstrap_profile != "comfyui-6006-v1":
            raise ValueError("AutoDL bootstrap profile is not allowlisted")
        payload["bootstrap_profile"] = bootstrap_profile
    return "POST", path, payload


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


class ProviderScheduleRequest(BaseModel):
    scheduled_start_at: datetime | None = None
    scheduled_stop_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)
    confirm: bool

    @field_validator("scheduled_start_at", "scheduled_stop_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("定时启停时间必须包含时区")
        return value.astimezone(UTC)


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
    # Compatibility field: daily admission quotas are disabled globally.
    daily_quota: int = Field(0, ge=0, le=1_000_000)
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
    daily_quota: int = Field(0, ge=0, le=1_000_000)
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


def _register_job_waiter(app: FastAPI, job_id: str) -> asyncio.Event:
    """Register one in-process waiter for a durable scheduler job.

    More than one synchronous request can legitimately wait on the same job
    through an idempotency key, so waiters are a set instead of a single event.
    Redis is only a wake-up hint: callers always re-read PostgreSQL before
    returning a result.
    """
    waiter = asyncio.Event()
    waiters: dict[str, set[asyncio.Event]] = app.state.job_terminal_waiters
    waiters.setdefault(job_id, set()).add(waiter)
    return waiter


def _discard_job_waiter(app: FastAPI, job_id: str, waiter: asyncio.Event) -> None:
    waiters: dict[str, set[asyncio.Event]] = app.state.job_terminal_waiters
    registered = waiters.get(job_id)
    if registered is None:
        return
    registered.discard(waiter)
    if not registered:
        waiters.pop(job_id, None)


def _wake_job_waiters(app: FastAPI, job_id: str) -> None:
    waiters: dict[str, set[asyncio.Event]] = app.state.job_terminal_waiters
    for waiter in tuple(waiters.get(job_id, ())):
        waiter.set()


async def job_event_listener_loop(app: FastAPI) -> None:
    """Wake synchronous API calls from scheduler events instead of polling at 1 Hz.

    The event channel is deliberately advisory.  Reconnects and missed
    messages are handled by the caller's periodic database read, preserving
    PostgreSQL as the sole source of truth while removing the usual 0--1 s
    terminal polling tail.
    """
    while True:
        redis: Redis | None = getattr(app.state, "redis", None)
        if redis is None:
            await asyncio.sleep(1)
            continue
        try:
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("gpu-control:events")
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1,
                    )
                    if not message:
                        await asyncio.sleep(0)
                        continue
                    try:
                        payload = json.loads(str(message.get("data") or "{}"))
                    except (TypeError, ValueError):
                        continue
                    job_id = payload.get("job_id") if isinstance(payload, dict) else None
                    if isinstance(job_id, str) and job_id:
                        _wake_job_waiters(app, job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger().warning(
                "redis.job_event_listener_failed",
                error_code="REDIS_UNAVAILABLE",
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(0.25)


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


def clear_idle_substance_specialization_on_manual_active(node: Node, now: datetime) -> bool:
    """Let an explicit operator ACTIVE action end only the soft Baker hold.

    Pending reservations, active Baker fences, recovery-required state and an
    occupied/externally busy GPU remain authoritative. This gives operators a
    work-conserving escape hatch after a completed bake without weakening the
    physical-GPU mutual exclusion that protects ComfyUI and Substance.
    """

    if node.id != SUBSTANCE_GPU_NODE_ID or node.current_jobs:
        return False
    if node.external_busy or node.foreign_queue_detected:
        return False
    if substance_gpu_interlock(node, now)["active"]:
        return False
    labels = dict(node.labels or {})
    raw_specialization = labels.get(GPU_SPECIALIZATION_LABEL)
    if (
        not isinstance(raw_specialization, dict)
        or raw_specialization.get("key") != SUBSTANCE_SPECIALIZATION_KEY
    ):
        return False
    # Remove both a live idle hold and an already-expired stale label. The
    # hard interlock checks above remain authoritative in either case.
    labels.pop(GPU_SPECIALIZATION_LABEL, None)
    node.labels = labels
    return True


async def _ensure_provider_instance(
    db: AsyncSession,
    *,
    provider: str,
    product: str,
    instance_id: str,
    with_for_update: bool = False,
) -> ProviderInstance:
    """Atomically create a provider row, then return the durable winner."""
    values = {
        "provider": provider,
        "product": product,
        "instance_id": instance_id,
    }
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    conflict_columns = ("provider", "product", "instance_id")
    if dialect_name == "postgresql":
        await db.execute(
            postgresql_insert(ProviderInstance)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
        )
    elif dialect_name == "sqlite":
        await db.execute(
            sqlite_insert(ProviderInstance)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
        )
    else:
        instance = await db.scalar(
            select(ProviderInstance).where(
                ProviderInstance.provider == provider,
                ProviderInstance.product == product,
                ProviderInstance.instance_id == instance_id,
            )
        )
        if instance is None:
            try:
                async with db.begin_nested():
                    db.add(ProviderInstance(**values))
                    await db.flush()
            except IntegrityError:
                pass

    query = select(ProviderInstance).where(
        ProviderInstance.provider == provider,
        ProviderInstance.product == product,
        ProviderInstance.instance_id == instance_id,
    )
    if with_for_update:
        query = query.with_for_update()
    instance = await db.scalar(query)
    if instance is None:
        raise RuntimeError("Provider instance upsert did not produce a durable row")
    return instance


AUTODL_ACCESS_HOST_SUFFIXES = (".autodl.com", ".autodl.art", ".seetacloud.com")
AUTODL_COMFYUI_PORTS = {443, 8443}


def _safe_autodl_comfyui_endpoint(value: Any) -> str | None:
    """Return a credential-free official AutoDL ComfyUI origin.

    Provider inventory is external input.  A bound node may follow its updated
    6006 address automatically, but it must never be redirected to arbitrary
    hosts, paths or embedded credentials.
    """

    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port or 443
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in AUTODL_COMFYUI_PORTS
        or parsed.path not in {"", "/"}
        or parsed.query
        or not any(
            hostname == suffix[1:] or hostname.endswith(suffix)
            for suffix in AUTODL_ACCESS_HOST_SUFFIXES
        )
    ):
        return None
    # Fragments are browser-only workspace hints and must not enter the
    # scheduler's base URL.  The provider inventory response remains the
    # authoritative source for the Web UI launch link.
    return urlunsplit(("https", parsed.netloc, "/", "", ""))


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging("api", cfg.environment)
        app.state.settings = cfg
        app.state.db = Database(cfg)
        app.state.storage = LocalJobStorage(cfg.job_root)
        app.state.redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        app.state.provider_http = httpx.AsyncClient(
            base_url=cfg.provider_controller_url,
            timeout=httpx.Timeout((cfg.autodl_read_retries + 2) * cfg.autodl_timeout_seconds + 10),
            follow_redirects=False,
        )
        app.state.tenant_locks = {}
        app.state.node_heartbeat_nonces = {}
        app.state.job_terminal_waiters = {}
        app.state.provider_reconcile_event = asyncio.Event()
        app.state.provider_inventory_sync_at = 0.0
        try:
            await app.state.redis.ping()
        except Exception:
            await app.state.redis.aclose()
            app.state.redis = None
        app.state.job_event_listener_task = (
            asyncio.create_task(job_event_listener_loop(app))
            if app.state.redis is not None
            else None
        )
        app.state.alert_delivery_task = asyncio.create_task(alert_delivery_loop(app))
        app.state.provider_reconcile_task = (
            asyncio.create_task(provider_reconcile_loop(app)) if cfg.autodl_enabled else None
        )
        app.state.provider_inventory_sync_task = (
            asyncio.create_task(provider_inventory_sync_loop(app)) if cfg.autodl_enabled else None
        )
        yield
        app.state.alert_delivery_task.cancel()
        tasks = [app.state.alert_delivery_task]
        if app.state.job_event_listener_task is not None:
            app.state.job_event_listener_task.cancel()
            tasks.append(app.state.job_event_listener_task)
        if app.state.provider_reconcile_task is not None:
            app.state.provider_reconcile_task.cancel()
            tasks.append(app.state.provider_reconcile_task)
        if app.state.provider_inventory_sync_task is not None:
            app.state.provider_inventory_sync_task.cancel()
            tasks.append(app.state.provider_inventory_sync_task)
        await asyncio.gather(*tasks, return_exceptions=True)
        if app.state.redis is not None:
            await app.state.redis.aclose()
        await app.state.provider_http.aclose()
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
                        daily_quota=0,
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

    async def refresh_bound_autodl_node_endpoint(
        db: AsyncSession,
        instance: ProviderInstance,
        inventory_item: dict[str, Any],
        *,
        observed_at: datetime,
        actor_id: str,
        source_ip: str,
        request_id: str,
    ) -> bool:
        """Follow the official 6006 address for an explicitly bound instance."""

        if not instance.node_id:
            return False
        access = inventory_item.get("access")
        endpoint = _safe_autodl_comfyui_endpoint(
            access.get("service_6006") if isinstance(access, dict) else None
        )
        if endpoint is None:
            return False
        node = await db.get(Node, instance.node_id, with_for_update=True)
        if node is None:
            return False
        labels = dict(node.labels or {})
        expected_ref = f"{instance.product}:{instance.instance_id}"
        if labels.get("provider") != "autodl" or labels.get("provider_ref") not in {
            None,
            expected_ref,
        }:
            return False

        before = {
            "base_url": node.base_url,
            "provider_service_6006_url": labels.get("provider_service_6006_url"),
            "provider_data_path": labels.get("provider_data_path"),
            "comfyui_browser_proxy_port": labels.get("comfyui_browser_proxy_port"),
            "comfyui_browser_fragment": labels.get("comfyui_browser_fragment"),
        }
        changed = (
            node.base_url != endpoint
            or labels.get("provider_service_6006_url") != endpoint
            or labels.get("provider_data_path") != "direct_https"
            or "comfyui_browser_proxy_port" in labels
            or "comfyui_browser_fragment" in labels
        )
        if not changed:
            return False

        labels.pop("comfyui_browser_proxy_port", None)
        labels.pop("comfyui_browser_fragment", None)
        labels["provider_service_6006_url"] = endpoint
        labels["provider_data_path"] = "direct_https"
        labels["provider_endpoint_observed_at"] = observed_at.isoformat()
        node.base_url = endpoint
        node.labels = labels
        after = {
            "base_url": endpoint,
            "provider_service_6006_url": endpoint,
            "provider_data_path": "direct_https",
            "provider_ref": expected_ref,
        }
        db.add(
            AuditLog(
                actor_id=actor_id,
                action="cloud.node.endpoint.refresh",
                target_type="node",
                target_id=node.id,
                before=before,
                after=after,
                source_ip=source_ip,
                request_id=request_id,
                result="SUCCESS",
            )
        )
        return True

    async def provider_controller_request(
        app_instance: FastAPI,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
            if payload is not None
            else b""
        )
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        signing_path = path
        ordered_query = sorted(query.items()) if query else None
        if ordered_query:
            signing_path += "?" + urlencode(ordered_query)
        signature = sign_agent_request(
            method,
            signing_path,
            body,
            timestamp,
            nonce,
            cfg.provider_controller_hmac_secret.get_secret_value(),
        )
        try:
            response = await app_instance.state.provider_http.request(
                method,
                path,
                params=ordered_query,
                content=body if payload is not None else None,
                headers={
                    "Content-Type": "application/json",
                    "X-GPU-Timestamp": timestamp,
                    "X-GPU-Nonce": nonce,
                    "X-GPU-Signature": signature,
                },
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                503,
                detail={
                    "code": "PROVIDER_CONTROLLER_UNAVAILABLE",
                    "message": "云服务器控制服务暂时不可用",
                },
            ) from exc
        try:
            decoded = response.json() if response.content else {}
        except ValueError as exc:
            raise HTTPException(
                502,
                detail={
                    "code": "PROVIDER_INVALID_RESPONSE",
                    "message": "云服务返回格式无效",
                },
            ) from exc
        if not response.is_success:
            detail = decoded.get("detail") if isinstance(decoded, dict) else None
            if not isinstance(detail, dict):
                detail = {
                    "code": "AUTODL_PROVIDER_ERROR",
                    "message": "AutoDL 操作失败",
                }
            status_code = response.status_code
            if status_code in {401, 403}:
                status_code = 502
                detail = {
                    "code": "PROVIDER_CONTROLLER_AUTH_FAILED",
                    "message": "云服务器控制服务内部鉴权失败",
                }
            raise HTTPException(status_code, detail=detail)
        if not isinstance(decoded, dict):
            raise HTTPException(
                502,
                detail={"code": "PROVIDER_INVALID_RESPONSE", "message": "云服务返回格式无效"},
            )
        return decoded

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
            labels = dict(node.labels or {})
            host = str(labels.get("host", ""))
            try:
                host = str(ipaddress.ip_address(host))
            except ValueError:
                continue
            groups.append(
                {
                    "targets": [f"{host}:9100"],
                    "labels": {"exporter": "node", "node_id": node.id},
                }
            )
            dcgm_enabled = labels.get("dcgm_exporter_enabled")
            if dcgm_enabled is True or (dcgm_enabled is None and not labels.get("wsl_runtime")):
                groups.append(
                    {
                        "targets": [f"{host}:9400"],
                        "labels": {"exporter": "dcgm", "node_id": node.id},
                    }
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
        *,
        pinned: bool = False,
        additional_images: tuple[tuple[str, UploadFile], ...] = (),
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
        if priority == Priority.CRITICAL and not pinned:
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
        try:
            inject_server_owned_workflow_parameters(
                workflow_key,
                {str(key): value for key, value in workflow.bindings.items()},
                parameters,
            )
        except ValueError as exc:
            raise HTTPException(
                422,
                detail={"code": "INPUT_INVALID", "message": str(exc)},
            ) from exc
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
        upload_fields = (("image", input_image), ("mask", mask), *additional_images)
        field_names = [field_name for field_name, upload in upload_fields if upload is not None]
        if len(field_names) != len(set(field_names)):
            storage.remove_tree(root)
            raise HTTPException(
                500,
                detail={"code": "UPLOAD_BINDING_INVALID", "message": "上传字段绑定重复"},
            )
        for field_name, upload in upload_fields:
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
            if (
                workflow_key in MODELVIEW_MASK_WORKFLOW_KEYS
                and field_name == "mask"
                and not mask_red_channel_has_edit_region(destination)
            ):
                storage.remove_tree(root)
                raise HTTPException(
                    422,
                    detail={
                        "code": "MASK_EMPTY",
                        "message": "蒙版红色通道不能是全黑；白色或灰色区域才会参与重绘",
                    },
                )
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
        if (
            workflow_key in MODELVIEW_WORKFLOW_KEYS
            and "normal_image" in image_dimensions
            and image_dimensions.get("image") != image_dimensions["normal_image"]
        ):
            storage.remove_tree(root)
            raise HTTPException(
                422,
                detail={"code": "INPUT_INVALID", "message": "法线图尺寸必须与输入图片一致"},
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
        # Daily task counts (including batch children) do not gate admission.
        # Queue, concurrency and request-rate limits remain independent.
        system_queue_limit = cfg.test_system_max_queued if is_load_test else cfg.system_max_queued
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
            pinned=pinned,
            parameters=parameters,
            request_hash=request_hash,
            request_id=request_id,
            trace_id=trace_id,
            job_dir=str(root),
            max_attempts=cfg.job_max_attempts,
        )
        db.add(job)
        await db.flush()
        if workflow_key in MODELVIEW_WORKFLOW_KEYS and not is_load_test:
            # The arrival itself renews the control 4090's guaranteed ModelView
            # response lane. This does not pin the job: both compatible 24 GiB
            # 3090 nodes may still claim ModelView work in parallel.
            modelview_guard_node = await db.scalar(
                select(Node).where(Node.id == MODELVIEW_INPAINT_NODE_ID).with_for_update()
            )
            if modelview_guard_node is not None:
                refresh_gpu_specialization(
                    modelview_guard_node,
                    MODELVIEW_INPAINT_WORKFLOW_KEY,
                    job_now,
                    owner="gpu-api",
                )
                active_imageclip = await db.scalar(
                    select(Job)
                    .where(
                        Job.node_id == MODELVIEW_INPAINT_NODE_ID,
                        Job.workflow_key == IMAGECLIP_WORKFLOW_KEY,
                        Job.status.in_(
                            {
                                JobStatus.CLAIMED.value,
                                JobStatus.UPLOADING.value,
                                JobStatus.SUBMITTED.value,
                                JobStatus.RUNNING.value,
                            }
                        ),
                    )
                    .order_by(Job.claimed_at, Job.id)
                    .with_for_update()
                )
                if active_imageclip is not None:
                    active_imageclip.error_code = IMAGECLIP_INPAINT_PREEMPTION_CODE
                    active_imageclip.error_message = (
                        "4090 ModelView 交互任务优先：当前抠图执行尝试将安全中断并改派其他 GPU"
                    )
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
        *,
        mask: UploadFile | None = None,
        additional_images: tuple[tuple[str, UploadFile], ...] = (),
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
        if mask is not None and "mask_filename" not in workflow.bindings:
            raise HTTPException(
                503,
                detail={
                    "code": "WORKFLOW_CONTRACT_MISMATCH",
                    "message": "当前启用的工作流版本尚未声明蒙版输入",
                },
            )
        accepted_additional_images = tuple(
            (field_name, upload)
            for field_name, upload in additional_images
            if f"{field_name}_filename" in workflow.bindings
        )
        if any(name == "normal_image" for name, _ in additional_images) and (
            "normal_image_filename" not in workflow.bindings
        ):
            raise HTTPException(
                503,
                detail={
                    "code": "WORKFLOW_CONTRACT_MISMATCH",
                    "message": "当前启用的工作流版本尚未声明法线图输入",
                },
            )
        tenant_lock = request.app.state.tenant_locks.setdefault(principal.id, asyncio.Lock())
        # ModelView generation is an interactive operation. It must take the first
        # compatible GPU slot released by an already-running job instead of
        # aging behind the much older animation-matting batch backlog.  The
        # durable queue is still used for safety and observability; pinning is
        # deliberately non-preemptive, so an in-flight production frame is
        # allowed to finish before the ModelView job is claimed.
        service_priority, pinned = service_queue_policy(workflow_key)
        async with tenant_lock:
            queued = await _create_job(
                request,
                workflow.workflow_key,
                workflow.version,
                parameters,
                service_priority,
                idempotency_key,
                principal,
                db,
                image,
                mask,
                None,
                pinned=pinned,
                additional_images=accepted_additional_images,
            )
        job_id = str(json.loads(bytes(queued.body))["job_id"])
        deadline = asyncio.get_running_loop().time() + workflow.timeout_seconds + 60
        waiter = _register_job_waiter(request.app, job_id)
        try:
            while asyncio.get_running_loop().time() < deadline:
                # Clear before the durable read so a scheduler event racing
                # with the query remains visible to the following wait.
                waiter.clear()
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
                                detail={
                                    "code": "OUTPUT_MISSING",
                                    "message": "任务成功但没有图片产物",
                                },
                            )
                        path = Path(job.job_dir) / artifact.relative_path
                        media_type = (
                            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                        )
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
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    # Redis normally wakes this immediately at terminal commit.
                    # A bounded timeout keeps correctness if the event channel
                    # is unavailable or a message was published before signup.
                    await asyncio.wait_for(waiter.wait(), timeout=min(1.0, remaining))
                except TimeoutError:
                    pass
            raise HTTPException(
                504,
                detail={
                    "code": "SERVICE_TIMEOUT",
                    "message": "图片生成等待超时",
                    "job_id": job_id,
                },
            )
        finally:
            _discard_job_waiter(request.app, job_id, waiter)

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
        material_image: Annotated[UploadFile, File()],
        mask: Annotated[UploadFile, File()],
        normal_image: Annotated[UploadFile, File()],
        viewport_reference: Annotated[UploadFile | None, File(deprecated=True)] = None,
        parameters: Annotated[str, Form()] = "{}",
        prompt: Annotated[str | None, Form(max_length=4096)] = None,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> FileResponse:
        return await run_modelview_service_request(
            request,
            MODELVIEW_INPAINT_WORKFLOW_KEY,
            principal,
            db,
            image,
            material_image,
            parameters,
            prompt,
            idempotency_key,
            mask=mask,
            viewport_reference=viewport_reference,
            normal_image=normal_image,
        )

    @app.post("/api/v1/services/modelview-single-view", response_class=FileResponse)
    async def modelview_single_view_service(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        image: Annotated[UploadFile, File()],
        material_image: Annotated[UploadFile, File()],
        normal_image: Annotated[UploadFile, File()],
        parameters: Annotated[str, Form()] = "{}",
        prompt: Annotated[str | None, Form(max_length=4096)] = None,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> FileResponse:
        return await run_modelview_service_request(
            request,
            MODELVIEW_SINGLE_VIEW_WORKFLOW_KEY,
            principal,
            db,
            image,
            material_image,
            parameters,
            prompt,
            idempotency_key,
            normal_image=normal_image,
        )

    @app.post(
        "/api/v1/services/modelview-single-view-inpaint",
        response_class=FileResponse,
    )
    async def modelview_single_view_inpaint_service(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        image: Annotated[UploadFile, File()],
        material_image: Annotated[UploadFile, File()],
        mask: Annotated[UploadFile, File()],
        normal_image: Annotated[UploadFile, File()],
        parameters: Annotated[str, Form()] = "{}",
        prompt: Annotated[str | None, Form(max_length=4096)] = None,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=128)
        ] = None,
    ) -> FileResponse:
        return await run_modelview_service_request(
            request,
            MODELVIEW_SINGLE_VIEW_INPAINT_WORKFLOW_KEY,
            principal,
            db,
            image,
            material_image,
            parameters,
            prompt,
            idempotency_key,
            mask=mask,
            normal_image=normal_image,
        )

    async def run_modelview_service_request(
        request: Request,
        workflow_key: str,
        principal: Principal,
        db: AsyncSession,
        image: UploadFile,
        material_image: UploadFile,
        parameters: str,
        prompt: str | None,
        idempotency_key: str | None,
        *,
        mask: UploadFile | None = None,
        viewport_reference: UploadFile | None = None,
        normal_image: UploadFile | None = None,
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
            workflow_key,
            principal,
            db,
            image,
            parameters,
            idempotency_key,
            mask=mask,
            additional_images=(
                ("material_image", material_image),
                *((("normal_image", normal_image),) if normal_image is not None else ()),
                *((("viewport_reference", viewport_reference),) if viewport_reference else ()),
            ),
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
        if batch.status in {BatchStatus.SUCCEEDED.value, BatchStatus.PARTIAL_SUCCESS.value}:
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

        failed_items = [
            {
                "ordinal": item.ordinal,
                "input_relative_path": item.input_relative_path,
                "input_sha256": item.input_sha256,
                "code": item.error_code or "CHILD_JOB_FAILED",
                "message": item.error_message or "frame processing failed",
                "node_id": item.node_id,
                "attempts": item.attempts,
                "attempted_node_ids": [
                    attempt.node_id for attempt in attempts_by_job.get(item.job_id or "", [])
                ],
            }
            for item in items
            if item.status == BatchItemStatus.FAILED.value
        ]

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
            "failed_items": failed_items,
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
            cfg.test_system_max_queued if client_kind == "test" else cfg.system_max_queued
        )
        production_preempting = client_kind == "test" and await active_production_work_exists(db)
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
                        select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value)
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
        # FastAPI keeps yield-dependency resources alive until a streaming
        # response finishes. End the authorization read transaction before
        # opening the long-lived SSE stream so it cannot pin PostgreSQL's
        # vacuum horizon for hours or days.
        await db.rollback()

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
        if batch.status not in {BatchStatus.SUCCEEDED.value, BatchStatus.PARTIAL_SUCCESS.value}:
            raise HTTPException(
                409,
                detail={
                    "code": "ARTIFACT_NOT_READY",
                    "message": "批次尚未生成已验证结果包",
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
        # Do not keep the request-scoped authorization transaction open for
        # the lifetime of this SSE connection.
        await db.rollback()

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
            # Dashboard success totals include batches that published a
            # verified success subset; the detailed batch payload preserves
            # the PARTIAL_SUCCESS distinction and failed-frame list.
            BatchStatus.PARTIAL_SUCCESS.value: JobStatus.SUCCEEDED.value,
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
        detail: Literal["summary", "full"] = "full",
        include_performance: bool = False,
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
        idempotency_rows = (
            list(
                (
                    await db.scalars(
                        select(IdempotencyKey)
                        .where(IdempotencyKey.job_id.in_([row.id for row in job_rows]))
                        .order_by(IdempotencyKey.created_at.desc())
                    )
                ).all()
            )
            if job_rows
            else []
        )
        idempotency_keys_by_job: dict[str, list[str]] = {}
        for idempotency in idempotency_rows:
            idempotency_keys_by_job.setdefault(idempotency.job_id, []).append(idempotency.key)
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
        batch_ids = [row.id for row in batch_rows]
        batch_distributions: dict[str, dict[str, int]] = {}
        batch_attempts: dict[str, int] = {}
        batch_artifacts: dict[str, list[BatchArtifact]] = {}
        batch_performance: dict[str, dict[str, Any]] = {}
        if detail == "summary" and batch_ids:
            distribution_rows = (
                await db.execute(
                    select(
                        JobBatchItem.batch_id,
                        JobBatchItem.node_id,
                        func.count(JobBatchItem.id),
                        func.coalesce(func.sum(JobBatchItem.attempts), 0),
                    )
                    .where(JobBatchItem.batch_id.in_(batch_ids))
                    .group_by(JobBatchItem.batch_id, JobBatchItem.node_id)
                )
            ).all()
            for batch_id, node_id, count, attempts in distribution_rows:
                batch_attempts[str(batch_id)] = batch_attempts.get(str(batch_id), 0) + int(attempts)
                if node_id:
                    batch_distributions.setdefault(str(batch_id), {})[str(node_id)] = int(count)
            artifact_rows = list(
                (
                    await db.scalars(
                        select(BatchArtifact)
                        .where(BatchArtifact.batch_id.in_(batch_ids))
                        .order_by(BatchArtifact.batch_id, BatchArtifact.created_at)
                    )
                ).all()
            )
            for artifact in artifact_rows:
                batch_artifacts.setdefault(artifact.batch_id, []).append(artifact)
            if include_performance:
                # Performance analysis needs authoritative GPU attempt timing,
                # but not the per-frame diagnostics returned by batch_payload.
                # Load the compact scalar projection in two bulk queries so the
                # analysis screen does not reintroduce the former N+1 path.
                performance_items = (
                    await db.execute(
                        select(
                            JobBatchItem.batch_id,
                            JobBatchItem.job_id,
                            JobBatchItem.status,
                            JobBatchItem.node_id,
                            JobBatchItem.width,
                            JobBatchItem.height,
                        ).where(JobBatchItem.batch_id.in_(batch_ids))
                    )
                ).all()
                performance_attempts = (
                    await db.execute(
                        select(
                            JobBatchItem.batch_id,
                            JobAttempt.job_id,
                            JobAttempt.attempt,
                            JobAttempt.node_id,
                            JobAttempt.gpu_started_at,
                            JobAttempt.gpu_finished_at,
                        )
                        .join(JobAttempt, JobAttempt.job_id == JobBatchItem.job_id)
                        .where(JobBatchItem.batch_id.in_(batch_ids))
                        .order_by(
                            JobBatchItem.batch_id,
                            JobAttempt.job_id,
                            JobAttempt.attempt,
                        )
                    )
                ).all()
                items_by_batch: dict[str, list[Any]] = {}
                attempts_by_batch: dict[str, list[Any]] = {}
                for item in performance_items:
                    items_by_batch.setdefault(str(item[0]), []).append(item)
                for attempt in performance_attempts:
                    attempts_by_batch.setdefault(str(attempt[0]), []).append(attempt)

                def summary_duration_ms(
                    started_at: datetime | None, finished_at: datetime | None
                ) -> int | None:
                    if started_at is None or finished_at is None:
                        return None
                    elapsed = int(
                        (utc_aware(finished_at) - utc_aware(started_at)).total_seconds() * 1000
                    )
                    return elapsed if elapsed >= 0 else None

                for batch in batch_rows:
                    item_rows = items_by_batch.get(batch.id, [])
                    attempt_rows = attempts_by_batch.get(batch.id, [])
                    gpu_attempts = [row for row in attempt_rows if row[4] or row[5]]
                    durations = [
                        duration
                        for row in gpu_attempts
                        if (duration := summary_duration_ms(row[4], row[5])) is not None
                    ]
                    completed_assignments = {
                        (str(row[1]), str(row[3]))
                        for row in gpu_attempts
                        if row[1] and row[3] and summary_duration_ms(row[4], row[5]) is not None
                    }
                    successful_items_complete = all(
                        row[1] and row[3] and (str(row[1]), str(row[3])) in completed_assignments
                        for row in item_rows
                        if row[2] == BatchItemStatus.SUCCEEDED.value
                    )
                    measurements_complete = (
                        bool(gpu_attempts)
                        and len(durations) == len(gpu_attempts)
                        and successful_items_complete
                    )
                    service_ms = sum(durations) if durations else None
                    can_compute_throughput = (
                        batch.status == BatchStatus.SUCCEEDED.value
                        and measurements_complete
                        and bool(service_ms)
                    )
                    input_pixels = sum(int(row[4] or 0) * int(row[5] or 0) for row in item_rows)
                    attempts_by_job: dict[str, list[Any]] = {}
                    for row in attempt_rows:
                        attempts_by_job.setdefault(str(row[1]), []).append(row)
                    reassignments = sum(
                        previous[3] != current[3]
                        for job_attempts in attempts_by_job.values()
                        for previous, current in zip(job_attempts, job_attempts[1:], strict=False)
                    )
                    batch_performance[batch.id] = {
                        "schema_version": "1.0-summary",
                        "input_pixels_total": input_pixels,
                        "gpu_service_ms_total": service_ms,
                        "gpu_service_measurements_complete": measurements_complete,
                        "queue_ms": summary_duration_ms(batch.queued_at, batch.started_at),
                        "execution_ms": summary_duration_ms(
                            batch.started_at, batch.execution_finished_at
                        ),
                        "assembly_ms": summary_duration_ms(
                            batch.assembling_at, batch.artifact_ready_at
                        ),
                        "artifact_publish_ms": summary_duration_ms(
                            batch.artifact_ready_at, batch.finished_at
                        ),
                        "frames_per_gpu_minute": (
                            round(batch.succeeded_items * 60_000 / service_ms, 6)
                            if can_compute_throughput and service_ms is not None
                            else None
                        ),
                        "megapixels_per_gpu_second": (
                            round(input_pixels / 1_000_000 / (service_ms / 1000), 6)
                            if can_compute_throughput and service_ms is not None
                            else None
                        ),
                        "scheduler_restarts": None,
                        "reassignments": reassignments,
                        "straggler_ratio": None,
                        "nodes": [],
                    }
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
            job_idempotency_keys = idempotency_keys_by_job.get(job.id, [])
            payload.update(
                {
                    "tenant_id": job.tenant_id,
                    "client_kind": owner.client_kind if owner else "production",
                    "request_id": job.request_id,
                    # Admin-only recovery identity. A job with zero or multiple
                    # historical keys remains deliberately ambiguous.
                    "idempotency_key": (
                        job_idempotency_keys[0] if len(job_idempotency_keys) == 1 else None
                    ),
                }
            )
            rows.append(payload)
        for batch in batch_rows:
            if detail == "full":
                payload = await batch_payload(batch, db, admin=True)
                attempt_count = int(
                    await db.scalar(
                        select(func.coalesce(func.sum(JobBatchItem.attempts), 0)).where(
                            JobBatchItem.batch_id == batch.id
                        )
                    )
                    or 0
                )
            else:
                identity = {
                    "workflow_key": batch.workflow_key,
                    "workflow_version": batch.workflow_version,
                    "pipeline_commit": batch.pipeline_commit,
                    "pipeline_sha256": batch.pipeline_sha256,
                    "output_node": batch.output_node,
                }
                artifacts = [
                    {
                        "id": artifact.id,
                        "kind": artifact.kind,
                        "filename": artifact.filename,
                        "content_type": artifact.content_type,
                        "size_bytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                        **identity,
                        "download_url": (f"/admin/batches/{batch.id}/artifacts/{artifact.id}"),
                    }
                    for artifact in batch_artifacts.get(batch.id, [])
                ]
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
                    "node_distribution": batch_distributions.get(batch.id, {}),
                    "created_at": batch.created_at.isoformat(),
                    "validated_at": (
                        batch.validated_at.isoformat() if batch.validated_at else None
                    ),
                    "queued_at": batch.queued_at.isoformat() if batch.queued_at else None,
                    "started_at": batch.started_at.isoformat() if batch.started_at else None,
                    "last_progress_at": (
                        batch.last_progress_at.isoformat() if batch.last_progress_at else None
                    ),
                    "execution_finished_at": (
                        batch.execution_finished_at.isoformat()
                        if batch.execution_finished_at
                        else None
                    ),
                    "assembling_at": (
                        batch.assembling_at.isoformat() if batch.assembling_at else None
                    ),
                    "artifact_ready_at": (
                        batch.artifact_ready_at.isoformat() if batch.artifact_ready_at else None
                    ),
                    "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
                    "updated_at": batch.updated_at.isoformat(),
                    "error": (
                        {"code": batch.error_code, "message": batch.error_message}
                        if batch.error_code
                        else None
                    ),
                    "artifact": artifacts[0] if artifacts else None,
                    "artifacts": artifacts,
                    # Heavy per-frame diagnostics are loaded only from the
                    # dedicated batch detail endpoint when an operator opens a row.
                    "failed_items": [],
                    "performance": batch_performance.get(batch.id),
                }
                attempt_count = batch_attempts.get(batch.id, 0)
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
                    "attempt": attempt_count,
                }
            )
            rows.append(payload)
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[:bounded_limit]

    @app.get("/admin/load-sessions/{load_session_id}/collisions")
    async def admin_load_session_collisions(
        load_session_id: str,
        _: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        """Check one exact load namespace without scanning capped history pages."""

        session_id = canonical_load_session_id(load_session_id)
        roughness_prefix = f"lt:{session_id}:mvr:"
        imageclip_prefix = f"loadtest:{session_id}:imageclip_batch:"
        asset_prefix = f"loadtest:{session_id}:"
        counts = {
            "gpu_jobs": int(
                await db.scalar(
                    select(func.count(Job.id)).where(Job.request_id.like(f"{roughness_prefix}%"))
                )
                or 0
            ),
            "gpu_batches": int(
                await db.scalar(
                    select(func.count(JobBatch.id)).where(
                        JobBatch.external_batch_id.like(f"{imageclip_prefix}%")
                    )
                )
                or 0
            ),
            "asset_jobs": int(
                await db.scalar(
                    select(func.count(AssetJob.id)).where(
                        AssetJob.external_asset_id.like(f"{asset_prefix}%")
                    )
                )
                or 0
            ),
        }
        collision_count = sum(counts.values())
        return {
            "schema_version": "gpu-control-load-session-collision.v1",
            "session_id": session_id,
            "collision_free": collision_count == 0,
            "collision_count": collision_count,
            "counts": counts,
            "scope": "exact_global_session_namespace",
        }

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
        cpu_total_slots = sum(worker.max_concurrency for worker in schedulable_cpu_workers)
        cpu_used_slots = sum(worker.current_jobs for worker in schedulable_cpu_workers)
        substance_physical_slots = max(0, SUBSTANCE_MAX_PARALLEL - len(fenced_substance_job_ids))
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
                "reported_total_slots": sum(worker.max_concurrency for worker in online_workers),
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
                    "engine_contract": "retopology-v6",
                    "format": ".fbx/.obj/.glb/.gltf/.blend + optional reference images",
                    "success_status": "SUCCEEDED",
                    "budget_mode": "automatic",
                    "user_target_faces_allowed": False,
                    "delivery_policy": "all_eight_gates_required",
                    "shape_authority": "high_poly_only",
                    "views": ["front", "back", "left", "right", "top", "bottom", "perspective"],
                    "roles": ["source_high", "formal_low"],
                    "reference_views_optional": True,
                    "maximum_reference_views": 16,
                    "status": "/api/v1/assets/jobs/{job_id}",
                    "events": "/api/v1/assets/jobs/{job_id}/events",
                },
                "roughness": {
                    "submit": "/api/v1/services/modelview-roughness",
                    "format": "multipart image",
                    "runtime": (
                        "GPU: control-4090 / worker-3090-a / worker-3090-b / "
                        "worker-4070ti-animation-host-01"
                    ),
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

    @app.post("/admin/asset-jobs/{job_id}/retry")
    async def admin_retry_substance_asset_job(
        job_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        """Retry one failed Substance job only after durable host recovery evidence."""
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        job = await db.get(AssetJob, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        admin_retry_count = int((job.options or {}).get("admin_retry_count", 0))
        if (
            job.job_type != "SUBSTANCE_BAKE_V1"
            or job.status != "FAILED"
            or job.stage not in {"RECOVERY_REQUIRED", "FAILED"}
            or job.error_code
            not in {
                "SUBSTANCE_COMFYUI_CONTINUITY_FAILED",
                "SUBSTANCE_EXECUTION_FAILED",
            }
            or admin_retry_count >= 1
        ):
            raise HTTPException(409, detail={"code": "ASSET_JOB_NOT_RETRYABLE"})
        if not Path(job.input_path).is_file():
            raise HTTPException(409, detail={"code": "ASSET_RETRY_INPUT_MISSING"})
        artifact_count = int(
            await db.scalar(
                select(func.count(AssetArtifact.id)).where(AssetArtifact.job_id == job.id)
            )
            or 0
        )
        if artifact_count:
            raise HTTPException(409, detail={"code": "ASSET_RETRY_ARTIFACT_CONFLICT"})

        active_bakes = int(
            await db.scalar(
                select(func.count(AssetJob.id)).where(
                    AssetJob.id != job.id,
                    AssetJob.job_type == "SUBSTANCE_BAKE_V1",
                    AssetJob.status.not_in(TERMINAL_ASSET_WORK_STATUSES),
                )
            )
            or 0
        )
        now = datetime.now(UTC)
        node = await db.scalar(
            select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
        )
        node_heartbeat = (
            node.last_heartbeat_at.replace(tzinfo=UTC)
            if node is not None
            and node.last_heartbeat_at is not None
            and node.last_heartbeat_at.tzinfo is None
            else (node.last_heartbeat_at if node is not None else None)
        )
        node_safe = bool(
            node is not None
            and node.health == "ONLINE"
            and node.mode == "ACTIVE"
            and node.current_jobs == 0
            and not node.manual_reserved
            and not node.external_busy
            and not node.foreign_queue_detected
            and node_heartbeat is not None
            and (now - node_heartbeat).total_seconds() <= cfg.node_heartbeat_timeout_seconds
            and not substance_gpu_interlock(node, now)["active"]
        )
        worker_rows = list(
            (
                await db.scalars(
                    select(AssetWorker)
                    .where(AssetWorker.id.like(f"{SUBSTANCE_WORKER_ID_PREFIX}%"))
                    .order_by(AssetWorker.id)
                    .with_for_update()
                )
            ).all()
        )
        expected_workers = {
            f"{SUBSTANCE_WORKER_ID_PREFIX}{index:02d}"
            for index in range(1, SUBSTANCE_MAX_PARALLEL + 1)
        }
        workers_safe = {worker.id for worker in worker_rows} == expected_workers
        for worker in worker_rows:
            heartbeat = worker.last_heartbeat_at
            if heartbeat is not None and heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            probe_checked = worker.substance_process_probe_checked_at
            if probe_checked is not None and probe_checked.tzinfo is None:
                probe_checked = probe_checked.replace(tzinfo=UTC)
            workers_safe = workers_safe and bool(
                worker.status == "ONLINE"
                and worker.skill_version == "substance-baker-2026.08.12-v7"
                and worker.current_jobs == 0
                and worker.agent_instance_id
                and heartbeat is not None
                and (now - heartbeat).total_seconds() <= cfg.asset_worker_heartbeat_timeout_seconds
                and worker.substance_process_probe_status == "HEALTHY"
                and worker.substance_active_processes == 0
                and probe_checked is not None
                and (now - probe_checked).total_seconds()
                <= cfg.asset_worker_heartbeat_timeout_seconds
            )
        if active_bakes or not node_safe or not workers_safe:
            raise HTTPException(
                409,
                detail={
                    "code": "SUBSTANCE_ADMIN_RETRY_UNSAFE",
                    "active_bakes": active_bakes,
                    "node_safe": node_safe,
                    "workers_safe": workers_safe,
                },
            )

        previous = {
            "status": job.status,
            "stage": job.stage,
            "attempt_count": job.attempt_count,
            "worker_id": job.worker_id,
            "worker_instance_id": job.worker_instance_id,
            "error_code": job.error_code,
            "error_message": job.error_message,
        }
        job.status = "QUEUED"
        job.stage = "RETRY_QUEUED"
        job.stage_message = "管理员确认宿主恢复，任务已安全返回烘焙队列"
        job.progress = 0
        job.estimated_remaining_seconds = None
        job.worker_id = None
        job.worker_instance_id = None
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.cancel_requested = False
        job.error_code = None
        job.error_message = None
        job.options = {
            **dict(job.options or {}),
            "admin_retry_count": admin_retry_count + 1,
            "admin_retry_last_reason": body.reason,
        }
        job.started_at = None
        job.finished_at = None
        job.last_progress_at = now
        await append_admin_asset_event(
            db,
            job,
            event="asset.admin_retry",
            details={
                "reason": body.reason,
                "actor": principal.id,
                "previous": previous,
            },
        )
        await audit(
            db,
            request,
            principal,
            "asset_job.retry",
            "asset_job",
            job.id,
            previous,
            {
                "status": job.status,
                "stage": job.stage,
                "attempt_count": job.attempt_count,
                "reason": body.reason,
            },
        )
        await db.commit()
        return {
            "job_id": job.id,
            "status": job.status,
            "stage": job.stage,
            "attempt_count": job.attempt_count,
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
        if batch.status not in {BatchStatus.SUCCEEDED.value, BatchStatus.PARTIAL_SUCCESS.value}:
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

    @app.get("/admin/providers/autodl")
    async def admin_autodl_inventory(
        request: Request,
        principal: Annotated[Principal, Depends(admin_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        force: bool = False,
    ) -> dict[str, Any]:
        result = await provider_controller_request(
            request.app,
            "GET",
            "/internal/v1/providers/autodl/inventory",
            query={"force": "true" if force else "false"},
        )
        now = datetime.now(UTC)
        rows = result.get("instances") if isinstance(result.get("instances"), list) else []
        persisted: dict[tuple[str, str], ProviderInstance] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            product = str(item.get("product") or "")
            instance_id = str(item.get("instance_id") or "")
            if (
                product not in {"app", "pro"}
                or re.fullmatch(r"pro-[A-Za-z0-9]+", instance_id) is None
            ):
                continue
            instance = await _ensure_provider_instance(
                db,
                provider="autodl",
                product=product,
                instance_id=instance_id,
                with_for_update=True,
            )
            instance.display_name = str(item.get("name") or "")[:256]
            instance.observed_state = str(item.get("state") or "unknown")[:24]
            instance.provider_status = str(item.get("provider_status") or "unknown")[:64]
            instance.last_seen_at = now
            instance.revision = int(instance.revision or 0) + 1
            await refresh_bound_autodl_node_endpoint(
                db,
                instance,
                item,
                observed_at=now,
                actor_id=principal.id,
                source_ip=request.client.host if request.client else "",
                request_id=str(request.state.request_id),
            )
            persisted[(product, instance_id)] = instance
        await db.flush()
        active_operations = list(
            (
                await db.scalars(
                    select(ProviderOperation)
                    .where(
                        ProviderOperation.provider == "autodl",
                        ProviderOperation.status.in_(
                            {"PENDING", "IN_FLIGHT", "WAITING", "UNCERTAIN"}
                        ),
                    )
                    .order_by(ProviderOperation.created_at.desc())
                )
            ).all()
        )
        operation_by_ref: dict[tuple[str, str], ProviderOperation] = {}
        for operation in active_operations:
            operation_by_ref.setdefault((operation.product, operation.instance_id), operation)
        for item in rows:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("product") or ""), str(item.get("instance_id") or ""))
            instance = persisted.get(key)
            operation = operation_by_ref.get(key)
            item["scheduled_start_at"] = instance.scheduled_start_at if instance else None
            item["scheduled_stop_at"] = instance.scheduled_stop_at if instance else None
            item["management"] = {
                "managed": bool(instance and instance.managed),
                "scheduling_enabled": bool(instance and instance.scheduling_enabled),
                "bootstrap_profile": instance.bootstrap_profile if instance else None,
                "desired_state": instance.desired_state if instance else None,
                "node_id": instance.node_id if instance else None,
                "scheduled_start_at": instance.scheduled_start_at if instance else None,
                "scheduled_stop_at": instance.scheduled_stop_at if instance else None,
                "schedule_updated_by": instance.schedule_updated_by if instance else None,
                "schedule_reason": instance.schedule_reason if instance else None,
                "operation": (
                    {
                        "id": operation.id,
                        "status": operation.status,
                        "desired_state": operation.desired_state,
                    }
                    if operation
                    else None
                ),
            }
        await db.commit()
        return result

    async def change_autodl_instance_state(
        product: Literal["app", "pro"],
        instance_id: str,
        desired_state: Literal["running", "stopped"],
        body: RetryRequest,
        request: Request,
        principal: Principal,
        db: AsyncSession,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        if re.fullmatch(r"pro-[A-Za-z0-9]+", instance_id) is None:
            raise HTTPException(422, detail={"code": "AUTODL_REF_INVALID"})
        effective_key = idempotency_key or str(request.state.request_id)
        if (
            not 8 <= len(effective_key) <= 192
            or re.fullmatch(r"[A-Za-z0-9._:-]+", effective_key) is None
        ):
            raise HTTPException(422, detail={"code": "IDEMPOTENCY_KEY_INVALID"})
        request_hash = hashlib.sha256(
            f"{product}:{instance_id}:{desired_state}".encode()
        ).hexdigest()
        # Stop admission and Scheduler claims share this lock.  The lock order
        # is always global admission -> provider instance -> node so a due
        # shutdown cannot race a fresh lease onto the same cloud GPU.
        if desired_state == "stopped":
            await request.app.state.db.acquire_global_admission_transaction_lock(db)
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:provider_ref))"),
                {"provider_ref": f"autodl:{product}:{instance_id}"},
            )
        existing = await db.scalar(
            select(ProviderOperation).where(
                ProviderOperation.provider == "autodl",
                ProviderOperation.idempotency_key == effective_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            return {
                "operation_id": existing.id,
                "status": existing.status,
                "desired_state": existing.desired_state,
                "accepted": existing.status not in {"FAILED"},
                "correlation_id": existing.request_id,
            }
        active = await db.scalar(
            select(ProviderOperation)
            .where(
                ProviderOperation.provider == "autodl",
                ProviderOperation.product == product,
                ProviderOperation.instance_id == instance_id,
                ProviderOperation.status.in_({"PENDING", "IN_FLIGHT", "WAITING", "UNCERTAIN"}),
            )
            .order_by(ProviderOperation.created_at.desc())
            .with_for_update()
        )
        if active is not None:
            if active.desired_state != desired_state:
                raise HTTPException(
                    409,
                    detail={
                        "code": "PROVIDER_OPERATION_IN_PROGRESS",
                        "message": "该实例已有相反的电源操作正在收敛",
                    },
                )
            return {
                "operation_id": active.id,
                "status": active.status,
                "desired_state": active.desired_state,
                "accepted": True,
                "correlation_id": active.request_id,
            }
        instance = await _ensure_provider_instance(
            db,
            provider="autodl",
            product=product,
            instance_id=instance_id,
            with_for_update=True,
        )
        instance.managed = True
        instance.desired_state = desired_state
        instance.revision = int(instance.revision or 0) + 1
        if desired_state == "stopped" and instance.node_id:
            node = await db.get(Node, instance.node_id, with_for_update=True)
            active_leases = await db.scalar(
                select(func.count(NodeLease.id)).where(
                    NodeLease.node_id == instance.node_id, NodeLease.active.is_(True)
                )
            )
            if node is not None and (
                node.current_jobs > 0
                or node.external_busy
                or node.foreign_queue_detected
                or bool(active_leases)
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "CLOUD_INSTANCE_BUSY",
                        "message": "实例仍有任务、租约或外部队列，已拒绝关机",
                    },
                )
            if node is not None:
                node.mode = NodeMode.DRAINING.value
        operation = ProviderOperation(
            id=str(uuid.uuid4()),
            provider="autodl",
            product=product,
            instance_id=instance_id,
            desired_state=desired_state,
            status="PENDING",
            idempotency_key=effective_key,
            request_hash=request_hash,
            request_id=str(request.state.request_id),
            requested_by=principal.id,
            source_ip=request.client.host if request.client else "",
            reason=body.reason,
            next_attempt_at=datetime.now(UTC),
        )
        db.add(operation)
        await audit(
            db,
            request,
            principal,
            f"cloud.instance.{('start' if desired_state == 'running' else 'stop')}",
            "cloud_instance",
            f"{product}:{instance_id}",
            {"desired_state": instance.observed_state},
            {
                "operation_id": operation.id,
                "desired_state": desired_state,
                "status": operation.status,
                "reason": body.reason,
            },
        )
        await db.commit()
        request.app.state.provider_reconcile_event.set()
        return {
            "operation_id": operation.id,
            "status": operation.status,
            "desired_state": operation.desired_state,
            "accepted": True,
            "correlation_id": operation.request_id,
        }

    @app.get("/admin/providers/autodl/operations/{operation_id}")
    async def admin_autodl_operation(
        operation_id: str,
        _: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        operation = await db.get(ProviderOperation, operation_id)
        if operation is None or operation.provider != "autodl":
            raise HTTPException(
                404,
                detail={"code": "PROVIDER_OPERATION_NOT_FOUND", "message": "云操作不存在"},
            )
        return {
            "operation_id": operation.id,
            "product": operation.product,
            "instance_id": operation.instance_id,
            "desired_state": operation.desired_state,
            "status": operation.status,
            "attempt_count": operation.attempt_count,
            "provider_status": operation.provider_status,
            "error_code": operation.error_code,
            "error_message": operation.error_message,
            "created_at": operation.created_at,
            "started_at": operation.started_at,
            "dispatch_attempted_at": operation.dispatch_attempted_at,
            "dispatch_ack_at": operation.dispatch_ack_at,
            "confirmation_deadline_at": operation.confirmation_deadline_at,
            "completed_at": operation.completed_at,
            "updated_at": operation.updated_at,
        }

    @app.post("/admin/providers/autodl/instances/{product}/{instance_id}/start")
    async def admin_start_autodl_instance(
        product: Literal["app", "pro"],
        instance_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return await change_autodl_instance_state(
            product, instance_id, "running", body, request, principal, db, idempotency_key
        )

    @app.post("/admin/providers/autodl/instances/{product}/{instance_id}/stop")
    async def admin_stop_autodl_instance(
        product: Literal["app", "pro"],
        instance_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return await change_autodl_instance_state(
            product, instance_id, "stopped", body, request, principal, db, idempotency_key
        )

    @app.put("/admin/providers/autodl/instances/{product}/{instance_id}/schedule")
    async def admin_schedule_autodl_instance(
        product: Literal["app", "pro"],
        instance_id: str,
        body: ProviderScheduleRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        if re.fullmatch(r"pro-[A-Za-z0-9]+", instance_id) is None:
            raise HTTPException(422, detail={"code": "AUTODL_REF_INVALID"})
        now = datetime.now(UTC)
        latest = now + timedelta(days=365)
        for field_name, value in (
            ("scheduled_start_at", body.scheduled_start_at),
            ("scheduled_stop_at", body.scheduled_stop_at),
        ):
            if value is not None and (value < now + timedelta(minutes=1) or value > latest):
                raise HTTPException(
                    422,
                    detail={
                        "code": "AUTODL_SCHEDULE_TIME_INVALID",
                        "message": f"{field_name} 必须在 1 分钟后到 365 天内",
                    },
                )
        if (
            body.scheduled_start_at is not None
            and body.scheduled_stop_at is not None
            and body.scheduled_stop_at <= body.scheduled_start_at
        ):
            raise HTTPException(
                422,
                detail={
                    "code": "AUTODL_SCHEDULE_ORDER_INVALID",
                    "message": "定时关机必须晚于定时开机",
                },
            )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:provider_ref))"),
                {"provider_ref": f"autodl:{product}:{instance_id}"},
            )
        instance = await _ensure_provider_instance(
            db,
            provider="autodl",
            product=product,
            instance_id=instance_id,
            with_for_update=True,
        )
        before = {
            "scheduled_start_at": (
                instance.scheduled_start_at.isoformat()
                if instance.scheduled_start_at is not None
                else None
            ),
            "scheduled_stop_at": (
                instance.scheduled_stop_at.isoformat()
                if instance.scheduled_stop_at is not None
                else None
            ),
        }
        instance.managed = True
        instance.scheduled_start_at = body.scheduled_start_at
        instance.scheduled_stop_at = body.scheduled_stop_at
        instance.schedule_updated_by = principal.id
        instance.schedule_reason = body.reason
        instance.revision = int(instance.revision or 0) + 1
        after = {
            "scheduled_start_at": (
                instance.scheduled_start_at.isoformat()
                if instance.scheduled_start_at is not None
                else None
            ),
            "scheduled_stop_at": (
                instance.scheduled_stop_at.isoformat()
                if instance.scheduled_stop_at is not None
                else None
            ),
            "reason": body.reason,
        }
        await audit(
            db,
            request,
            principal,
            "cloud.instance.schedule.update",
            "cloud_instance",
            f"{product}:{instance_id}",
            before,
            after,
        )
        await db.commit()
        request.app.state.provider_reconcile_event.set()
        return {
            "product": product,
            "instance_id": instance_id,
            "scheduled_start_at": instance.scheduled_start_at,
            "scheduled_stop_at": instance.scheduled_stop_at,
            "reason": body.reason,
            "updated_by": principal.id,
        }

    @app.post("/admin/providers/autodl/instances/{product}/{instance_id}/ssh-credentials")
    async def admin_autodl_ssh_credentials(
        product: Literal["app", "pro"],
        instance_id: str,
        body: RetryRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_operator)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> JSONResponse:
        if not body.confirm:
            raise HTTPException(409, detail={"code": "CONFIRMATION_REQUIRED"})
        result = await provider_controller_request(
            request.app,
            "POST",
            f"/internal/v1/providers/autodl/instances/{product}/{instance_id}/ssh-credentials",
        )
        await audit(
            db,
            request,
            principal,
            "cloud.instance.ssh_credentials",
            "cloud_instance",
            f"{product}:{instance_id}",
            {},
            {
                "host": result.get("host"),
                "port": result.get("port"),
                "username": result.get("username"),
                "reason": body.reason,
            },
        )
        await db.commit()
        return JSONResponse(result, headers={"Cache-Control": "no-store, max-age=0"})

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
        codex_worker_ids = [f"asset-{row.id}" for row in rows]
        workers = list(
            (
                await db.scalars(select(AssetWorker).where(AssetWorker.id.in_(codex_worker_ids)))
            ).all()
        )
        active_codex_jobs = list(
            (
                await db.scalars(
                    select(AssetJob)
                    .where(
                        AssetJob.worker_id.in_(codex_worker_ids),
                        AssetJob.job_type.in_({"RETOPOLOGY_PROCESS_V1", "RETOPOLOGY_PROCESS_V2"}),
                        AssetJob.status.in_({"CLAIMED", "RUNNING"}),
                    )
                    .order_by(AssetJob.created_at.desc())
                )
            ).all()
        )
        recent_inactive_codex_jobs = list(
            (
                await db.scalars(
                    select(AssetJob)
                    .where(
                        AssetJob.worker_id.in_(codex_worker_ids),
                        AssetJob.job_type.in_({"RETOPOLOGY_PROCESS_V1", "RETOPOLOGY_PROCESS_V2"}),
                        AssetJob.status.not_in({"CLAIMED", "RUNNING"}),
                    )
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
        for job in active_codex_jobs:
            if not job.worker_id:
                continue
            codex_task_by_worker.setdefault(job.worker_id, job)
        for job in recent_inactive_codex_jobs:
            if job.worker_id:
                codex_task_by_worker.setdefault(job.worker_id, job)
        payload: list[dict[str, Any]] = []
        for row in rows:
            item = {column.name: getattr(row, column.name) for column in Node.__table__.columns}
            labels = row.labels or {}
            for metric_key in ("gpu_temperature_c", "gpu_power_w"):
                metric_value = labels.get(metric_key)
                item[metric_key] = (
                    float(metric_value)
                    if isinstance(metric_value, int | float)
                    and not isinstance(metric_value, bool)
                    and math.isfinite(float(metric_value))
                    else None
                )
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
                "heartbeat_timeout_seconds": cfg.asset_worker_heartbeat_timeout_seconds,
                "probe_max_age_seconds": cfg.asset_codex_probe_max_age_seconds,
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
                        "output_contract": (
                            [
                                "final_low.blend",
                                "final_low.fbx",
                                "execution_plan.json",
                                "qa_report.json",
                                "comparison_contact_sheet.png",
                                "wireframe_contact_sheet.png",
                                "manifest.json",
                                "result.json",
                            ]
                            if codex_task.job_type == "RETOPOLOGY_PROCESS_V2"
                            else [
                                "retopology_candidate.blend",
                                "retopology_candidate.fbx",
                                "retopology_process_report.json",
                                "retopology_final_audit.json",
                                "retopology_manifest.json",
                                "retopology_agent_prompt.txt",
                                "retopology_agent_events.jsonl",
                                "high/reference/generated × front/side/top/perspective PNG",
                            ]
                        ),
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
        before = {
            "mode": node.mode,
            "manual_reserved": node.manual_reserved,
            "gpu_specialization": dict(node.labels or {}).get(GPU_SPECIALIZATION_LABEL),
        }
        substance_owner_transferred = take_operator_drain_ownership(node)
        node.mode = body.mode.value
        node.manual_reserved = body.mode == NodeMode.RESERVED
        substance_specialization_released = False
        if body.mode == NodeMode.ACTIVE:
            substance_specialization_released = (
                clear_idle_substance_specialization_on_manual_active(node, datetime.now(UTC))
            )
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
                "substance_specialization_released": (substance_specialization_released),
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
                "daily_quota": 0,
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
            daily_quota=0,
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
        client.daily_quota = 0
        client.weight = body.weight
        client.allowed_ips = body.allowed_ips
        client.callback_hosts = body.callback_hosts
        after = body.model_dump(exclude={"reason", "confirm"})
        after["daily_quota"] = 0
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
                node_id=node.id,
                workflow_key=version.workflow_key,
                workflow_version=version.version,
                template_sha256=version.template_sha256,
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

    async def sync_autodl_inventory(app_instance: FastAPI) -> None:
        """Refresh provider truth independently of anyone opening the Web UI."""

        result = await provider_controller_request(
            app_instance,
            "GET",
            "/internal/v1/providers/autodl/inventory",
            query={"force": "true"},
        )
        rows = result.get("instances") if isinstance(result.get("instances"), list) else []
        now = datetime.now(UTC)
        async with app_instance.state.db.session() as inventory_db:
            for item in rows:
                if not isinstance(item, dict):
                    continue
                product = str(item.get("product") or "")
                instance_id = str(item.get("instance_id") or "")
                if (
                    product not in {"app", "pro"}
                    or re.fullmatch(r"pro-[A-Za-z0-9]+", instance_id) is None
                ):
                    continue
                instance = await _ensure_provider_instance(
                    inventory_db,
                    provider="autodl",
                    product=product,
                    instance_id=instance_id,
                    with_for_update=True,
                )
                display_name = str(item.get("name") or "")[:256]
                observed_state = str(item.get("state") or "unknown")[:24]
                provider_status = str(item.get("provider_status") or "unknown")[:64]
                changed = (
                    instance.display_name != display_name
                    or instance.observed_state != observed_state
                    or instance.provider_status != provider_status
                )
                last_seen_at = instance.last_seen_at
                if last_seen_at is not None and last_seen_at.tzinfo is None:
                    last_seen_at = last_seen_at.replace(tzinfo=UTC)
                freshness_due = last_seen_at is None or now - last_seen_at >= timedelta(seconds=60)
                if changed:
                    instance.display_name = display_name
                    instance.observed_state = observed_state
                    instance.provider_status = provider_status
                    instance.revision = int(instance.revision or 0) + 1
                if changed or freshness_due:
                    instance.last_seen_at = now
                await refresh_bound_autodl_node_endpoint(
                    inventory_db,
                    instance,
                    item,
                    observed_at=now,
                    actor_id="system:provider-inventory",
                    source_ip="",
                    request_id=str(uuid.uuid4()),
                )
            await inventory_db.commit()

    async def provider_inventory_sync_loop(app_instance: FastAPI) -> None:
        while True:
            try:
                await sync_autodl_inventory(app_instance)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger().warning(
                    "provider.inventory_sync_failed",
                    error_type=type(exc).__name__,
                )
            # Provider state changes are also reconciled immediately by the
            # operation event path. A 30-second background safety poll avoids
            # rewriting every instance row eight times per minute while still
            # recovering external state changes promptly.
            await asyncio.sleep(30)

    async def enqueue_due_provider_schedule(app_instance: FastAPI) -> bool:
        """Turn the latest due one-shot schedule into one durable operation.

        A control-plane restart may span both a start and a later stop time. In
        that case only the latest due intent is applied, avoiding a wasteful
        start immediately followed by a stop. A due stop drains the node under
        the global admission lock and waits for all work/fences to clear.
        """

        now = datetime.now(UTC)
        async with app_instance.state.db.session() as schedule_db:
            candidate_id = await schedule_db.scalar(
                select(ProviderInstance.id)
                .where(
                    ProviderInstance.provider == "autodl",
                    ProviderInstance.managed.is_(True),
                    or_(
                        ProviderInstance.scheduled_start_at <= now,
                        ProviderInstance.scheduled_stop_at <= now,
                    ),
                )
                .order_by(ProviderInstance.updated_at, ProviderInstance.id)
                .limit(1)
            )
            if candidate_id is None:
                return False
            await app_instance.state.db.acquire_global_admission_transaction_lock(schedule_db)
            instance = await schedule_db.get(ProviderInstance, candidate_id, with_for_update=True)
            if instance is None:
                return False
            due_events: list[tuple[datetime, Literal["running", "stopped"]]] = []
            if (
                instance.scheduled_start_at is not None
                and utc_aware(instance.scheduled_start_at) <= now
            ):
                due_events.append((utc_aware(instance.scheduled_start_at), "running"))
            if (
                instance.scheduled_stop_at is not None
                and utc_aware(instance.scheduled_stop_at) <= now
            ):
                due_events.append((utc_aware(instance.scheduled_stop_at), "stopped"))
            if not due_events:
                return False
            event_at, desired_state = max(due_events, key=lambda item: item[0])
            active = await schedule_db.scalar(
                select(ProviderOperation.id).where(
                    ProviderOperation.provider == "autodl",
                    ProviderOperation.product == instance.product,
                    ProviderOperation.instance_id == instance.instance_id,
                    ProviderOperation.status.in_({"PENDING", "IN_FLIGHT", "WAITING", "UNCERTAIN"}),
                )
            )
            if active is not None:
                return False
            if desired_state == "stopped" and instance.node_id:
                node = await schedule_db.get(Node, instance.node_id, with_for_update=True)
                active_leases = await schedule_db.scalar(
                    select(func.count(NodeLease.id)).where(
                        NodeLease.node_id == instance.node_id,
                        NodeLease.active.is_(True),
                    )
                )
                if node is not None:
                    node.mode = NodeMode.DRAINING.value
                    if (
                        node.current_jobs > 0
                        or node.external_busy
                        or node.foreign_queue_detected
                        or bool(active_leases)
                    ):
                        await schedule_db.commit()
                        return False
            operation_id = str(uuid.uuid4())
            operation_key = (
                f"schedule:{instance.product}:{instance.instance_id}:"
                f"{int(event_at.timestamp())}:{desired_state}"
            )
            request_hash = hashlib.sha256(
                f"{instance.product}:{instance.instance_id}:{desired_state}".encode()
            ).hexdigest()
            instance.desired_state = desired_state
            if (
                instance.scheduled_start_at is not None
                and utc_aware(instance.scheduled_start_at) <= now
            ):
                instance.scheduled_start_at = None
            if (
                instance.scheduled_stop_at is not None
                and utc_aware(instance.scheduled_stop_at) <= now
            ):
                instance.scheduled_stop_at = None
            instance.revision = int(instance.revision or 0) + 1
            schedule_db.add(
                ProviderOperation(
                    id=operation_id,
                    provider="autodl",
                    product=instance.product,
                    instance_id=instance.instance_id,
                    desired_state=desired_state,
                    status="PENDING",
                    idempotency_key=operation_key,
                    request_hash=request_hash,
                    request_id=str(uuid.uuid4()),
                    requested_by=instance.schedule_updated_by or "system:schedule",
                    source_ip="",
                    reason=instance.schedule_reason or "scheduled lifecycle policy",
                    next_attempt_at=now,
                )
            )
            schedule_db.add(
                AuditLog(
                    actor_id=instance.schedule_updated_by or "system:schedule",
                    action=f"cloud.instance.schedule.{('start' if desired_state == 'running' else 'stop')}",
                    target_type="cloud_instance",
                    target_id=f"{instance.product}:{instance.instance_id}",
                    before={"scheduled_for": event_at.isoformat()},
                    after={"operation_id": operation_id, "desired_state": desired_state},
                    source_ip="",
                    request_id=str(uuid.uuid4()),
                    result="SUCCESS",
                )
            )
            await schedule_db.commit()
            return True

    async def reconcile_one_provider_operation(app_instance: FastAPI) -> bool:
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(seconds=90)
        async with app_instance.state.db.session() as operation_db:
            operation = await operation_db.scalar(
                select(ProviderOperation)
                .where(
                    ProviderOperation.next_attempt_at <= now,
                    or_(
                        ProviderOperation.status.in_({"PENDING", "WAITING", "UNCERTAIN"}),
                        (
                            (ProviderOperation.status == "IN_FLIGHT")
                            & (ProviderOperation.updated_at <= stale_cutoff)
                        ),
                    ),
                )
                .order_by(ProviderOperation.next_attempt_at, ProviderOperation.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if operation is None:
                return False
            should_dispatch = operation.dispatch_attempted_at is None
            operation.status = "IN_FLIGHT"
            operation.started_at = operation.started_at or now
            operation.attempt_count += 1
            if should_dispatch:
                # Persist the fence before crossing the network. AutoDL does
                # not accept an idempotency token for power writes, so after
                # this point every retry is a read-only state observation.
                operation.dispatch_attempted_at = now
                operation.confirmation_deadline_at = now + timedelta(minutes=10)
            operation_id = operation.id
            product = operation.product
            instance_id = operation.instance_id
            desired_state = operation.desired_state
            request_id = operation.request_id
            bootstrap_profile = None
            if should_dispatch and desired_state == "running":
                bootstrap_profile = await operation_db.scalar(
                    select(ProviderInstance.bootstrap_profile).where(
                        ProviderInstance.provider == "autodl",
                        ProviderInstance.product == product,
                        ProviderInstance.instance_id == instance_id,
                    )
                )
            await operation_db.commit()

        result: dict[str, Any] | None = None
        failure: HTTPException | None = None
        try:
            method, path, payload = provider_operation_request(
                product,
                instance_id,
                desired_state,
                operation_id,
                should_dispatch=should_dispatch,
                bootstrap_profile=bootstrap_profile,
            )
            result = await provider_controller_request(
                app_instance,
                method,
                path,
                payload,
            )
        except ValueError:
            failure = HTTPException(
                422,
                detail={
                    "code": "AUTODL_BOOTSTRAP_PROFILE_INVALID",
                    "message": "云实例启动配置不在允许列表",
                },
            )
        except HTTPException as exc:
            failure = exc

        async with app_instance.state.db.session() as operation_db:
            current = await operation_db.get(ProviderOperation, operation_id, with_for_update=True)
            if current is None or current.status != "IN_FLIGHT":
                return True
            observed_state = str((result or {}).get("state") or "unknown")[:24]
            provider_status = str((result or {}).get("provider_status") or "")[:64]
            current.provider_status = provider_status
            observed_request_id = str((result or {}).get("request_id") or "")[:128]
            if observed_request_id:
                current.provider_request_id = observed_request_id
            if should_dispatch and result is not None:
                current.dispatch_ack_at = datetime.now(UTC)
            instance = await operation_db.scalar(
                select(ProviderInstance)
                .where(
                    ProviderInstance.provider == "autodl",
                    ProviderInstance.product == product,
                    ProviderInstance.instance_id == instance_id,
                )
                .with_for_update()
            )
            if instance is not None and result is not None:
                instance.observed_state = observed_state
                instance.provider_status = provider_status
                instance.last_seen_at = datetime.now(UTC)
                instance.revision = int(instance.revision or 0) + 1
            if failure is None and result is not None:
                current.error_code = None
                current.error_message = None
                if observed_state == desired_state:
                    current.status = "CONFIRMED"
                    current.completed_at = datetime.now(UTC)
                    if instance is not None:
                        instance.desired_state = desired_state
                        if instance.node_id:
                            node = await operation_db.get(
                                Node, instance.node_id, with_for_update=True
                            )
                            if node is not None:
                                if desired_state == "stopped":
                                    node.mode = NodeMode.DISABLED.value
                                elif instance.scheduling_enabled:
                                    node.mode = NodeMode.ACTIVE.value
                else:
                    current.status = "WAITING"
                    current.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2)
            else:
                detail = failure.detail if failure is not None else {}
                if not isinstance(detail, dict):
                    detail = {}
                code = str(detail.get("code") or "PROVIDER_CONTROLLER_UNAVAILABLE")[:64]
                message = str(detail.get("message") or "云服务器状态暂时无法确认")[:1000]
                current.error_code = code
                current.error_message = message
                permanent = failure is not None and failure.status_code in {401, 403, 404, 422}
                if permanent:
                    current.status = "FAILED"
                    current.completed_at = datetime.now(UTC)
                else:
                    current.status = "UNCERTAIN" if should_dispatch else "WAITING"
                    delay = min(60, 2 ** min(current.attempt_count, 5))
                    current.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            deadline = current.confirmation_deadline_at
            if (
                current.status in {"WAITING", "UNCERTAIN"}
                and deadline is not None
                and datetime.now(UTC) >= utc_aware(deadline)
            ):
                current.status = "FAILED"
                current.error_code = "PROVIDER_CONFIRMATION_TIMEOUT"
                current.error_message = "电源命令已停止重发，但实例状态未在期限内收敛"
                current.completed_at = datetime.now(UTC)
            if (
                current.status == "FAILED"
                and desired_state == "stopped"
                and instance is not None
                and instance.node_id
                and instance.observed_state == "running"
                and instance.scheduling_enabled
            ):
                node = await operation_db.get(Node, instance.node_id, with_for_update=True)
                if node is not None:
                    node.mode = NodeMode.ACTIVE.value
            if current.status in {"CONFIRMED", "FAILED"}:
                operation_db.add(
                    AuditLog(
                        actor_id=current.requested_by,
                        action="cloud.instance.operation.completed",
                        target_type="cloud_instance",
                        target_id=f"{product}:{instance_id}",
                        before={"operation_id": current.id},
                        after={
                            "status": current.status,
                            "desired_state": current.desired_state,
                            "provider_status": current.provider_status,
                            "provider_request_id": current.provider_request_id,
                            "error_code": current.error_code,
                        },
                        source_ip=current.source_ip,
                        request_id=request_id,
                        result="SUCCESS" if current.status == "CONFIRMED" else "FAILED",
                    )
                )
            await operation_db.commit()
        return True

    async def provider_reconcile_loop(app_instance: FastAPI) -> None:
        while True:
            try:
                # Clear before checking PostgreSQL so an operation committed
                # during the query leaves the event set and cannot miss the
                # fast wake-up. The timeout remains a cross-process fallback.
                app_instance.state.provider_reconcile_event.clear()
                scheduled = await enqueue_due_provider_schedule(app_instance)
                if scheduled:
                    continue
                reconciled = await reconcile_one_provider_operation(app_instance)
                if not reconciled:
                    try:
                        await asyncio.wait_for(
                            app_instance.state.provider_reconcile_event.wait(),
                            timeout=1,
                        )
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger().warning(
                    "provider.reconcile_failed",
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(2)

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
