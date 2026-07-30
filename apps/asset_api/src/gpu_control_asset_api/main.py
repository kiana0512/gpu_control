import asyncio
import hashlib
import hmac
import importlib.metadata
import ipaddress
import json
import os
import re
import secrets
import shutil
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from packages.gpu_control_core.assets import (
    AssetCreateMetadata,
    RetopologyAuditMetadata,
    RetopologyProcessMetadata,
    SubstanceBakeMetadata,
    asset_request_hash,
    lease_token_hash,
    retopology_audit_request_hash,
    retopology_process_request_hash,
    substance_bake_request_hash,
    uv_process_request_hash,
    validate_asset_filename,
    validate_baker_filename,
    validate_baker_texture_filename,
    validate_reference_image_filename,
)
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import (
    ApiClient,
    ApiKey,
    AssetArtifact,
    AssetIdempotencyKey,
    AssetJob,
    AssetJobEvent,
    AssetWorker,
    Node,
)
from packages.gpu_control_core.security import sign_agent_request, verify_api_key
from packages.gpu_control_core.settings import Settings, get_settings

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
TERMINAL_ASSET_STATUSES = frozenset(
    {"SUCCEEDED", "WAITING_REVIEW", "REVIEW_REJECTED", "FAILED", "CANCELLED"}
)
DOWNLOADABLE_ASSET_STATUSES = frozenset({"SUCCEEDED", "WAITING_REVIEW"})
UV_REQUIRED_ARTIFACTS = {
    "blend": ("model_PBR_UV.blend", "application/octet-stream"),
    "fbx": ("model_PBR_UV.fbx", "application/octet-stream"),
    "report": ("model_report.json", "application/json"),
    "qa": ("model_QA.json", "application/json"),
}
SUBSTANCE_BAKE_OUTPUTS = {
    "ao-self-v1": {
        "ao": ("asset_ao.png", "image/png"),
        "result": ("baker_result.json", "application/json"),
        "log": ("baker.log", "text/plain; charset=utf-8"),
    },
    "normal-dx-v1": {
        "normal_dx": ("asset_normal_dx.png", "image/png"),
        "result": ("baker_result.json", "application/json"),
        "log": ("baker.log", "text/plain; charset=utf-8"),
    },
    "pbr-core-v1": {
        "ao": ("asset_ao.png", "image/png"),
        "normal_dx": ("asset_normal_dx.png", "image/png"),
        "result": ("baker_result.json", "application/json"),
        "log": ("baker.log", "text/plain; charset=utf-8"),
    },
    "li3d-pbr-full-v2": {
        "base_color": ("asset_base_color.png", "image/png"),
        "roughness": ("asset_roughness.png", "image/png"),
        "metallic": ("asset_metallic.png", "image/png"),
        "ao": ("asset_ao.png", "image/png"),
        "normal_dx": ("asset_normal_dx.png", "image/png"),
        "normal_gl": ("asset_normal_gl.png", "image/png"),
        "world_normal": ("asset_world_normal.png", "image/png"),
        "curvature": ("asset_curvature.png", "image/png"),
        "thickness": ("asset_thickness.png", "image/png"),
        "position": ("asset_position.png", "image/png"),
        "result": ("baker_result.json", "application/json"),
        "log": ("baker.log", "text/plain; charset=utf-8"),
    },
}
SUBSTANCE_WORKER_ID = "asset-worker-3090-b-windows"
SUBSTANCE_WORKER_ID_PREFIX = f"{SUBSTANCE_WORKER_ID}-"
SUBSTANCE_GPU_NODE_ID = "worker-3090-b"
SUBSTANCE_VERSION = "substance-15.1.0"
SUBSTANCE_FENCE_LABEL = "substance_bake_fence_job_ids"
SUBSTANCE_LEGACY_FENCE_LABEL = "substance_bake_fence_job_id"
RETOPOLOGY_AUDIT_ARTIFACTS = {
    "audit": ("retopology_audit.json", "application/json"),
    "manifest": ("retopology_manifest.json", "application/json"),
}
RETOPOLOGY_PROCESS_REQUIRED_FILENAMES = {
    "retopology_candidate.blend": ("candidate_blend", "application/octet-stream"),
    "retopology_candidate.fbx": ("candidate_fbx", "application/octet-stream"),
    "retopology_process_report.json": ("process_report", "application/json"),
    "retopology_baseline_audit.json": ("baseline_audit", "application/json"),
    "retopology_final_audit.json": ("audit", "application/json"),
    "retopology_manifest.json": ("manifest", "application/json"),
    "retopology_comparison.png": ("comparison", "image/png"),
    "retopology_agent_plan.json": ("agent_plan", "application/json"),
    "retopology_agent_prompt.txt": ("agent_prompt", "text/plain; charset=utf-8"),
    "retopology_agent_events.jsonl": ("agent_events", "application/x-ndjson"),
    **{
        f"{role}_{view}.png": (f"view_{role}_{view}", "image/png")
        for role in ("high", "reference", "generated")
        for view in ("front", "side", "top", "perspective")
    },
}
RETOPOLOGY_PROCESS_OPTIONAL_FILENAMES = {
    "reference_images.png": ("reference_images", "image/png"),
}
RETOPOLOGY_PROCESS_REQUIRED_ARTIFACTS = {
    kind: (filename, content_type)
    for filename, (kind, content_type) in RETOPOLOGY_PROCESS_REQUIRED_FILENAMES.items()
}
RETOPOLOGY_PROCESS_OPTIONAL_ARTIFACTS = {
    kind: (filename, content_type)
    for filename, (kind, content_type) in RETOPOLOGY_PROCESS_OPTIONAL_FILENAMES.items()
}
RETOPOLOGY_FINAL_MODEL_ARTIFACTS = {
    "candidate_blend": ("blend", "retopology_final.blend"),
    "candidate_fbx": ("fbx", "retopology_final.fbx"),
}
RETOPOLOGY_DIAGNOSTIC_ERROR_CODES = frozenset(
    {"RETOPOLOGY_AUDIT_FAILED", "RETOPOLOGY_QUALITY_GATE_FAILED"}
)


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive test values and PostgreSQL values."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def is_substance_worker_id(worker_id: str | None) -> bool:
    return bool(
        worker_id
        and (
            worker_id == SUBSTANCE_WORKER_ID
            or worker_id.startswith(SUBSTANCE_WORKER_ID_PREFIX)
        )
    )


def substance_fence_job_ids(labels: dict[str, Any]) -> list[str]:
    raw = labels.get(SUBSTANCE_FENCE_LABEL, [])
    job_ids = [str(value) for value in raw] if isinstance(raw, list) else []
    legacy = labels.get(SUBSTANCE_LEGACY_FENCE_LABEL)
    if legacy and str(legacy) not in job_ids:
        job_ids.append(str(legacy))
    return job_ids


async def release_substance_gpu_fence(
    db: AsyncSession, job: AssetJob, *, restore_active: bool = True
) -> None:
    """Release only this Baker job's fence.

    A stale lease is not proof that Windows restored the WSL ComfyUI container,
    so that path deliberately keeps the physical node drained for recovery.
    """
    if job.job_type != "SUBSTANCE_BAKE_V1":
        return
    node = await db.scalar(
        select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
    )
    if node is None:
        return
    labels = dict(node.labels or {})
    fenced_job_ids = substance_fence_job_ids(labels)
    if job.id not in fenced_job_ids:
        return
    fenced_job_ids.remove(job.id)
    if fenced_job_ids:
        labels[SUBSTANCE_FENCE_LABEL] = fenced_job_ids
    else:
        labels.pop(SUBSTANCE_FENCE_LABEL, None)
    labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
    node.labels = labels
    if (
        restore_active
        and not fenced_job_ids
        and node.mode == "DRAINING"
        and not node.manual_reserved
    ):
        node.mode = "ACTIVE"


class Principal(BaseModel):
    id: str


class WorkerHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(pattern=r"^asset-(?:control|worker)-[a-z0-9-]+$", max_length=64)
    node_id: str = Field(pattern=r"^(?:control|worker)-[a-z0-9-]+$", max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=128)
    blender_version: str = Field(min_length=1, max_length=32)
    skill_version: str = Field(min_length=1, max_length=64)
    cpu_count: int = Field(ge=1, le=1024)
    max_concurrency: int = Field(ge=1, le=32)
    current_jobs: int = Field(ge=0, le=32)
    load_1m: float = Field(ge=0, le=4096)
    available_memory_mb: int = Field(ge=0)
    codex_cli_version: str | None = Field(default=None, max_length=64)
    codex_auth_status: str = Field(default="UNKNOWN", max_length=24)
    codex_probe_status: str = Field(default="NOT_RUN", max_length=24)
    codex_probe_latency_ms: int | None = Field(default=None, ge=0, le=600000)
    codex_last_checked_at: datetime | None = None
    codex_last_success_at: datetime | None = None
    codex_error_code: str | None = Field(default=None, max_length=64)
    retopoflow_version: str | None = Field(default=None, max_length=32)
    retopoflow_revision: str | None = Field(default=None, max_length=64)
    retopoflow_probe_status: str = Field(default="NOT_RUN", max_length=24)
    retopoflow_probe_latency_ms: int | None = Field(default=None, ge=0, le=600000)
    retopoflow_last_checked_at: datetime | None = None
    retopoflow_error_code: str | None = Field(default=None, max_length=64)


class WorkerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=64)
    load_1m: float = Field(ge=0, le=4096)
    available_memory_mb: int = Field(ge=0)


class WorkerProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: float = Field(ge=0, le=99.9)
    stage: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,31}$")
    message: str = Field(min_length=1, max_length=500)
    estimated_remaining_seconds: int | None = Field(default=None, ge=0, le=604800)


class WorkerFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Z0-9_]{3,64}$")
    message: str = Field(min_length=1, max_length=4000)
    retryable: bool = True


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    try:
        runtime_version = importlib.metadata.version("gpu-control")
    except importlib.metadata.PackageNotFoundError:
        runtime_version = "development"
    build_version = os.getenv("GPU_CONTROL_BUILD_VERSION", runtime_version)
    source_revision = os.getenv("GPU_CONTROL_BUILD_REVISION", "unknown")
    asset_secret = cfg.asset_worker_hmac_secret.strip()
    if (
        len(asset_secret) < 32
        or asset_secret == "development-only-change-me"
        or asset_secret.startswith("CHANGE_ME")
    ):
        raise ValueError(
            "ASSET_WORKER_HMAC_SECRET must be a dedicated secret of at least 32 characters"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(cfg)
        cfg.asset_root.mkdir(parents=True, exist_ok=True)
        yield
        await app.state.db.close()

    app = FastAPI(
        title="Unified Scheduling Center - Asset API",
        version=runtime_version,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def session(request: Request) -> AsyncIterator[AsyncSession]:
        async with request.app.state.db.session() as db:
            yield db

    @app.get("/version")
    @app.get("/api/v1/assets/version")
    async def version_info() -> dict[str, Any]:
        return {
            "component": "asset-api",
            "package_version": runtime_version,
            "build_version": build_version,
            "source_revision": source_revision,
            "version_aligned": runtime_version == build_version,
            "provenance_complete": runtime_version == build_version
            and re.fullmatch(r"[0-9a-f]{40}", source_revision) is not None,
        }

    async def api_principal(
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> Principal:
        client: ApiClient | None = None
        source_ip = str(
            ipaddress.ip_address(request.client.host if request.client else "127.0.0.1")
        )
        if x_api_key:
            parts = x_api_key.split("_", 2)
            if len(parts) != 3 or parts[0] != "gpc":
                raise HTTPException(401, detail={"code": "AUTH_FAILED"})
            key = await db.scalar(
                select(ApiKey).where(ApiKey.prefix == parts[1], ApiKey.enabled.is_(True))
            )
            if (
                key is None
                or (key.expires_at and key.expires_at <= datetime.now(UTC))
                or not verify_api_key(key.secret_hash, parts[2], cfg.api_key_pepper)
            ):
                raise HTTPException(401, detail={"code": "AUTH_FAILED"})
            client = await db.get(ApiClient, key.client_id)
            key.last_used_at = datetime.now(UTC)
        else:
            clients = list(
                (await db.scalars(select(ApiClient).where(ApiClient.role == "client"))).all()
            )
            matches = [candidate for candidate in clients if source_ip in (candidate.allowed_ips or [])]
            if len(matches) == 1:
                client = matches[0]
            elif len(matches) > 1:
                raise HTTPException(409, detail={"code": "CLIENT_IP_CONFLICT"})
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
                    try:
                        await db.flush()
                    except IntegrityError:
                        await db.rollback()
                        client = await db.get(ApiClient, auto_id)
        if client is None or not client.enabled or client.role != "client":
            raise HTTPException(403, detail={"code": "AUTH_FAILED"})
        client.last_seen_ip = source_ip
        client.last_seen_at = datetime.now(UTC)
        await db.commit()
        return Principal(id=client.id)

    async def verify_worker(request: Request, raw_body: bytes) -> None:
        timestamp = request.headers.get("x-asset-timestamp", "")
        nonce = request.headers.get("x-asset-nonce", "")
        signature = request.headers.get("x-asset-signature", "")
        try:
            stamp = int(timestamp)
        except ValueError as exc:
            raise HTTPException(401, detail={"code": "WORKER_AUTH_FAILED"}) from exc
        if abs(int(time.time()) - stamp) > 30 or not nonce:
            raise HTTPException(401, detail={"code": "WORKER_AUTH_EXPIRED"})
        expected = sign_agent_request(
            request.method,
            request.url.path,
            raw_body,
            timestamp,
            nonce,
            cfg.asset_worker_hmac_secret,
        )
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(401, detail={"code": "WORKER_AUTH_FAILED"})

    def artifact_payload(artifact: AssetArtifact) -> dict[str, Any]:
        return {
            "id": artifact.id,
            "kind": artifact.kind,
            "filename": artifact.filename,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "download_url": f"/api/v1/assets/jobs/{artifact.job_id}/artifacts/{artifact.id}",
        }

    def public_asset_error(job: AssetJob) -> dict[str, str] | None:
        """Return a stable caller-facing error without exposing Blender internals.

        Full worker diagnostics remain available to administrators through the
        control-plane overview.  API callers get a concise, actionable contract
        and never need to interpret Blender stdout/stderr.
        """
        if not job.error_code:
            return None
        messages = {
            "UV_QA_FAILED": (
                "自动展 UV 未通过严格交付 QA；系统已保留输入与诊断，"
                "不会发布不合格结果。请联系服务端管理员重试或修复。"
            ),
            "RETOPOLOGY_AUDIT_FAILED": (
                "自动重拓扑候选未通过严格交付 QA；诊断制品已保留，"
                "未发布为最终结果。"
            ),
            "RETOPOLOGY_QUALITY_GATE_FAILED": (
                "自动重拓扑候选未通过严格交付 QA；诊断制品已保留，"
                "未发布为最终结果。"
            ),
            "BLENDER_EXECUTION_FAILED": (
                "Blender 资产处理执行失败；系统已保留任务诊断，"
                "请联系服务端管理员处理后重试。"
            ),
            "SUBSTANCE_EXECUTION_FAILED": (
                "Substance 3D Baker 执行失败；系统已保留输入和原生 Windows 日志，"
                "未发布不完整贴图。"
            ),
            "SUBSTANCE_RESULT_INVALID": (
                "Substance 3D Baker 输出未通过完整性校验，未发布为最终贴图。"
            ),
        }
        return {
            "code": job.error_code,
            "message": messages.get(
                job.error_code,
                "资产处理未完成；系统已保留诊断，请联系服务端管理员。",
            ),
        }

    async def append_asset_event(
        db: AsyncSession,
        job: AssetJob,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        sequence = int(
            await db.scalar(
                select(func.coalesce(func.max(AssetJobEvent.sequence), 0)).where(
                    AssetJobEvent.job_id == job.id
                )
            )
            or 0
        ) + 1
        db.add(
            AssetJobEvent(
                job_id=job.id,
                sequence=sequence,
                status=job.status,
                stage=job.stage,
                progress=job.progress,
                message=job.stage_message,
                estimated_remaining_seconds=job.estimated_remaining_seconds,
                details=details or {},
            )
        )

    async def queue_timing(job: AssetJob, db: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)
        terminal_at = job.finished_at or job.last_progress_at
        timing_end = (
            as_utc(terminal_at)
            if job.status in TERMINAL_ASSET_STATUSES and terminal_at is not None
            else now
        )
        elapsed = (
            max(0, int((timing_end - as_utc(job.started_at)).total_seconds()))
            if job.started_at
            else 0
        )
        queue_position: int | None = None
        estimated_start_seconds: int | None = None
        if job.status == "QUEUED":
            queue_query = select(func.count(AssetJob.id)).where(
                AssetJob.status == "QUEUED",
                AssetJob.created_at <= job.created_at,
            )
            worker_query = select(
                func.coalesce(func.sum(AssetWorker.max_concurrency - AssetWorker.current_jobs), 0)
            ).where(AssetWorker.status == "ONLINE")
            if job.job_type == "SUBSTANCE_BAKE_V1":
                queue_query = queue_query.where(AssetJob.job_type == "SUBSTANCE_BAKE_V1")
                worker_query = worker_query.where(
                    (AssetWorker.id == SUBSTANCE_WORKER_ID)
                    | AssetWorker.id.startswith(SUBSTANCE_WORKER_ID_PREFIX)
                )
            else:
                queue_query = queue_query.where(AssetJob.job_type != "SUBSTANCE_BAKE_V1")
                worker_query = worker_query.where(
                    AssetWorker.id != SUBSTANCE_WORKER_ID,
                    ~AssetWorker.id.startswith(SUBSTANCE_WORKER_ID_PREFIX),
                )
            queue_position = int(await db.scalar(queue_query) or 1)
            slots = int(
                await db.scalar(worker_query)
                or 0
            )
            typical_seconds = {
                "UV_UNWRAP": 180,
                "UV_PROCESS_V2": 240,
                "RETOPOLOGY_AUDIT": 120,
                "RETOPOLOGY_PROCESS_V1": 900,
                "SUBSTANCE_BAKE_V1": 600,
            }.get(job.job_type, 300)
            estimated_start_seconds = (
                0
                if slots > 0 and queue_position <= slots
                else ((queue_position - 1) // max(slots, 1)) * typical_seconds
            )
        return {
            "queue_position": queue_position,
            "estimated_start_seconds": estimated_start_seconds,
            "elapsed_seconds": elapsed,
            "estimated_remaining_seconds": job.estimated_remaining_seconds,
            "last_progress_at": job.last_progress_at.isoformat()
            if job.last_progress_at
            else None,
        }

    def artifacts_are_downloadable(job: AssetJob) -> bool:
        return job.status in DOWNLOADABLE_ASSET_STATUSES or (
            job.status == "FAILED" and job.error_code in RETOPOLOGY_DIAGNOSTIC_ERROR_CODES
        )

    async def job_payload(job: AssetJob, db: AsyncSession) -> dict[str, Any]:
        artifacts: list[dict[str, Any]] = []
        if artifacts_are_downloadable(job):
            rows = list(
                (
                    await db.scalars(
                        select(AssetArtifact)
                        .where(AssetArtifact.job_id == job.id)
                        .order_by(AssetArtifact.kind)
                    )
                ).all()
            )
            artifacts = [artifact_payload(item) for item in rows]
        return {
            "job_id": job.id,
            "status_url": f"/api/v1/assets/jobs/{job.id}",
            "events_url": f"/api/v1/assets/jobs/{job.id}/events",
            "cancel_url": f"/api/v1/assets/jobs/{job.id}/cancel",
            "external_asset_id": job.external_asset_id,
            "job_type": job.job_type,
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
            "stage_message": job.stage_message,
            "timing": await queue_timing(job, db),
            "source_filename": job.source_filename,
            "input_sha256": job.input_sha256,
            "options": job.options,
            "worker_id": job.worker_id,
            "attempt_count": job.attempt_count,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": public_asset_error(job),
            "delivery_ready": job.status == "SUCCEEDED",
            "review_required": False,
            "artifacts_role": (
                "diagnostic"
                if job.status == "FAILED"
                and job.error_code in RETOPOLOGY_DIAGNOSTIC_ERROR_CODES
                else "delivery"
                if job.status == "SUCCEEDED"
                else "retained"
            ),
            "artifacts": artifacts,
        }

    async def owned_job(job_id: str, principal: Principal, db: AsyncSession) -> AssetJob:
        job = await db.get(AssetJob, job_id)
        if job is None or job.client_id != principal.id:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        return job

    async def persist_upload(
        upload: UploadFile,
        destination: Path,
        *,
        bytes_already_received: int = 0,
    ) -> tuple[str, int]:
        """Stream one upload to disk and return its immutable digest and size."""
        digest = hashlib.sha256()
        size = 0
        with destination.open("xb") as handle:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if bytes_already_received + size > cfg.asset_max_upload_bytes:
                    raise HTTPException(413, detail={"code": "ASSET_TOO_LARGE"})
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if size == 0:
            raise HTTPException(422, detail={"code": "ASSET_EMPTY"})
        return digest.hexdigest(), size

    async def create_uploaded_job(
        *,
        request: Request,
        principal: Principal,
        db: AsyncSession,
        upload: UploadFile,
        filename: str,
        external_asset_id: str,
        options: dict[str, Any],
        job_type: str,
        idempotency_key: str,
        request_hash_builder: Callable[[str], str],
    ) -> JSONResponse:
        staging = cfg.asset_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        source = staging / filename
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("xb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > cfg.asset_max_upload_bytes:
                        raise HTTPException(413, detail={"code": "ASSET_TOO_LARGE"})
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if size == 0:
                raise HTTPException(422, detail={"code": "ASSET_EMPTY"})
            input_sha = digest.hexdigest()
            request_hash = request_hash_builder(input_sha)
            existing = await db.scalar(
                select(AssetIdempotencyKey).where(
                    AssetIdempotencyKey.client_id == principal.id,
                    AssetIdempotencyKey.key == idempotency_key,
                    AssetIdempotencyKey.expires_at > datetime.now(UTC),
                )
            )
            if existing is not None:
                shutil.rmtree(staging)
                if existing.request_hash != request_hash:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                old_job = await db.get(AssetJob, existing.job_id)
                if old_job is None:
                    raise HTTPException(500, detail={"code": "ASSET_JOB_NOT_FOUND"})
                return JSONResponse(await job_payload(old_job, db), 200)
            duplicate_external = await db.scalar(
                select(AssetJob).where(
                    AssetJob.client_id == principal.id,
                    AssetJob.external_asset_id == external_asset_id,
                )
            )
            if duplicate_external is not None:
                raise HTTPException(409, detail={"code": "EXTERNAL_ASSET_CONFLICT"})
            job_id = str(uuid.uuid4())
            job_root = cfg.asset_root / job_id
            staging.rename(job_root)
            job = AssetJob(
                id=job_id,
                client_id=principal.id,
                external_asset_id=external_asset_id,
                job_type=job_type,
                status="QUEUED",
                source_filename=filename,
                input_path=str(job_root / filename),
                input_sha256=input_sha,
                input_size_bytes=size,
                options=options,
                request_hash=request_hash,
                request_id=str(request.state.request_id),
            )
            db.add(job)
            # AssetIdempotencyKey references AssetJob, but the ORM models do not
            # declare a relationship that SQLAlchemy can use to order these two
            # inserts.  PostgreSQL therefore needs the parent row flushed before
            # the idempotency row is staged (SQLite does not expose this ordering
            # bug while foreign-key enforcement is disabled in tests).
            await db.flush()
            await append_asset_event(
                db, job, details={"event": "asset.queued", "request_id": job.request_id}
            )
            db.add(
                AssetIdempotencyKey(
                    client_id=principal.id,
                    key=idempotency_key,
                    request_hash=request_hash,
                    job_id=job_id,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
            await db.commit()
            return JSONResponse(await job_payload(job, db), 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, str]:
        await request.app.state.db.ping()
        return {"status": "ready", "database": "ok"}

    @app.post("/api/v1/assets/uv/unwrap")
    async def create_uv_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        asset: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form()],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> JSONResponse:
        try:
            parsed = AssetCreateMetadata.model_validate_json(metadata)
            filename = validate_asset_filename(asset.filename or "")
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "ASSET_INPUT_INVALID", "message": str(exc)}) from exc
        return await create_uploaded_job(
            request=request,
            principal=principal,
            db=db,
            upload=asset,
            filename=filename,
            external_asset_id=parsed.external_asset_id,
            options=parsed.options.model_dump(mode="json"),
            job_type="UV_UNWRAP",
            idempotency_key=idempotency_key,
            request_hash_builder=lambda input_sha: asset_request_hash(parsed, input_sha),
        )

    @app.post("/api/v1/assets/bake/process")
    async def create_substance_bake_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        low_mesh: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form()],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
        high_mesh: Annotated[UploadFile | None, File()] = None,
        cage_mesh: Annotated[UploadFile | None, File()] = None,
        base_color_texture: Annotated[UploadFile | None, File()] = None,
        roughness_texture: Annotated[UploadFile | None, File()] = None,
        metallic_texture: Annotated[UploadFile | None, File()] = None,
    ) -> JSONResponse:
        """Queue one fixed-profile Substance 3D Baker job for native Windows 3090-B."""
        try:
            parsed = SubstanceBakeMetadata.model_validate_json(metadata)
            low_name = validate_baker_filename(low_mesh.filename or "")
            high_name = (
                validate_baker_filename(high_mesh.filename or "") if high_mesh else None
            )
            cage_name = (
                validate_baker_filename(cage_mesh.filename or "") if cage_mesh else None
            )
            if parsed.options.profile in {"normal-dx-v1", "pbr-core-v1", "li3d-pbr-full-v2"} and not high_mesh:
                raise ValueError(f"{parsed.options.profile} requires high_mesh")
            texture_uploads = {
                "base_color": base_color_texture,
                "roughness": roughness_texture,
                "metallic": metallic_texture,
            }
            if parsed.options.profile == "li3d-pbr-full-v2":
                missing = [role for role, upload in texture_uploads.items() if upload is None]
                if missing:
                    raise ValueError(
                        "li3d-pbr-full-v2 requires base_color_texture, roughness_texture and metallic_texture"
                    )
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "BAKE_INPUT_INVALID", "message": str(exc)}
            ) from exc

        staging = cfg.asset_root / f".staging-{uuid.uuid4().hex}"
        input_root = staging / "input"
        input_root.mkdir(parents=True, exist_ok=False)
        try:
            input_sha: dict[str, str] = {}
            received = 0
            role_uploads = [
                ("low", low_mesh, low_name),
                ("high", high_mesh, high_name),
                ("cage", cage_mesh, cage_name),
            ]
            for role, upload in texture_uploads.items():
                if upload is not None:
                    role_uploads.append(
                        (role, upload, validate_baker_texture_filename(upload.filename or ""))
                    )
            bundle_files: dict[str, str] = {}
            for role, upload, original_name in role_uploads:
                if upload is None or original_name is None:
                    continue
                bundle_name = f"asset_{role}{Path(original_name).suffix.lower()}"
                digest, size = await persist_upload(
                    upload,
                    input_root / bundle_name,
                    bytes_already_received=received,
                )
                received += size
                input_sha[role] = digest
                bundle_files[role] = bundle_name

            request_hash = substance_bake_request_hash(parsed, input_sha)
            existing = await db.scalar(
                select(AssetIdempotencyKey).where(
                    AssetIdempotencyKey.client_id == principal.id,
                    AssetIdempotencyKey.key == idempotency_key,
                    AssetIdempotencyKey.expires_at > datetime.now(UTC),
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                old_job = await db.get(AssetJob, existing.job_id)
                if old_job is None:
                    raise HTTPException(500, detail={"code": "ASSET_JOB_NOT_FOUND"})
                return JSONResponse(await job_payload(old_job, db), 200)
            duplicate_external = await db.scalar(
                select(AssetJob).where(
                    AssetJob.client_id == principal.id,
                    AssetJob.external_asset_id == parsed.external_asset_id,
                )
            )
            if duplicate_external is not None:
                raise HTTPException(409, detail={"code": "EXTERNAL_ASSET_CONFLICT"})

            request_document = {
                "schema_version": 1,
                "job_type": "SUBSTANCE_BAKE_V1",
                "external_asset_id": parsed.external_asset_id,
                "options": parsed.options.model_dump(mode="json"),
                "files": bundle_files,
                "input_sha256": input_sha,
            }
            (staging / "request.json").write_text(
                json.dumps(request_document, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            bundle = staging / "substance_bake_input.zip"
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(staging / "request.json", "request.json")
                for bundle_name in bundle_files.values():
                    archive.write(input_root / bundle_name, f"input/{bundle_name}")
            bundle_sha = sha256_path(bundle)
            bundle_size = bundle.stat().st_size

            job_id = str(uuid.uuid4())
            job_root = cfg.asset_root / job_id
            job_root.mkdir(parents=True, exist_ok=False)
            bundle.rename(job_root / bundle.name)
            options = parsed.options.model_dump(mode="json")
            options["files"] = bundle_files
            options["input_sha256"] = input_sha
            job = AssetJob(
                id=job_id,
                client_id=principal.id,
                external_asset_id=parsed.external_asset_id,
                job_type="SUBSTANCE_BAKE_V1",
                status="QUEUED",
                source_filename=bundle.name,
                input_path=str(job_root / bundle.name),
                input_sha256=bundle_sha,
                input_size_bytes=bundle_size,
                options=options,
                request_hash=request_hash,
                request_id=str(request.state.request_id),
            )
            db.add(job)
            await db.flush()
            await append_asset_event(
                db,
                job,
                details={
                    "event": "asset.queued",
                    "request_id": job.request_id,
                    "profile": parsed.options.profile,
                    "runtime": "worker-3090-b-windows",
                },
            )
            db.add(
                AssetIdempotencyKey(
                    client_id=principal.id,
                    key=idempotency_key,
                    request_hash=request_hash,
                    job_id=job_id,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
            await db.commit()
            return JSONResponse(await job_payload(job, db), 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/api/v1/assets/retopology/audit")
    async def create_retopology_audit_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        project: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form()],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> JSONResponse:
        try:
            parsed = RetopologyAuditMetadata.model_validate_json(metadata)
            filename = validate_asset_filename(project.filename or "")
            if not filename.lower().endswith(".blend"):
                raise ValueError("retopology audit requires one BLEND project")
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "ASSET_INPUT_INVALID", "message": str(exc)}
            ) from exc
        return await create_uploaded_job(
            request=request,
            principal=principal,
            db=db,
            upload=project,
            filename=filename,
            external_asset_id=parsed.external_asset_id,
            options=parsed.options.model_dump(mode="json"),
            job_type="RETOPOLOGY_AUDIT",
            idempotency_key=idempotency_key,
            request_hash_builder=lambda input_sha: retopology_audit_request_hash(
                parsed, input_sha
            ),
        )

    @app.post("/api/v1/assets/retopology/process")
    async def create_retopology_process_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        project: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form()],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
        reference_images: Annotated[list[UploadFile] | None, File()] = None,
    ) -> JSONResponse:
        """Create a source-preserving candidate with deterministic four-view evidence."""
        try:
            parsed = RetopologyProcessMetadata.model_validate_json(metadata)
            project_filename = validate_asset_filename(project.filename or "")
            if not project_filename.lower().endswith(".blend"):
                raise ValueError("retopology process requires one BLEND project")
            uploads = reference_images or []
            upload_names = [
                validate_reference_image_filename(item.filename or "") for item in uploads
            ]
            declared_names = [item.filename for item in parsed.reference_views]
            if len(upload_names) != len(set(upload_names)):
                raise ValueError("reference image upload filenames must be unique")
            if sorted(upload_names) != sorted(declared_names):
                raise ValueError(
                    "reference_images uploads must exactly match metadata.reference_views"
                )
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "ASSET_INPUT_INVALID", "message": str(exc)}
            ) from exc

        staging = cfg.asset_root / f".staging-{uuid.uuid4().hex}"
        bundle_root = staging / "bundle"
        references_root = bundle_root / "references"
        references_root.mkdir(parents=True, exist_ok=False)
        try:
            project_path = bundle_root / project_filename
            project_sha, project_size = await persist_upload(project, project_path)
            received = project_size
            reference_sha: dict[str, str] = {}
            reference_sizes: dict[str, int] = {}
            for upload, filename in zip(uploads, upload_names, strict=True):
                destination = references_root / filename
                digest, size = await persist_upload(
                    upload, destination, bytes_already_received=received
                )
                received += size
                try:
                    with Image.open(destination) as image:
                        if image.width * image.height > cfg.max_image_pixels:
                            raise HTTPException(
                                413,
                                detail={
                                    "code": "REFERENCE_IMAGE_TOO_LARGE",
                                    "filename": filename,
                                },
                            )
                        image.verify()
                except (OSError, UnidentifiedImageError) as exc:
                    raise HTTPException(
                        422,
                        detail={
                            "code": "REFERENCE_IMAGE_INVALID",
                            "filename": filename,
                        },
                    ) from exc
                reference_sha[filename] = digest
                reference_sizes[filename] = size

            request_hash = retopology_process_request_hash(
                parsed, project_sha, reference_sha
            )
            existing = await db.scalar(
                select(AssetIdempotencyKey).where(
                    AssetIdempotencyKey.client_id == principal.id,
                    AssetIdempotencyKey.key == idempotency_key,
                    AssetIdempotencyKey.expires_at > datetime.now(UTC),
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                old_job = await db.get(AssetJob, existing.job_id)
                if old_job is None:
                    raise HTTPException(500, detail={"code": "ASSET_JOB_NOT_FOUND"})
                return JSONResponse(await job_payload(old_job, db), 200)
            duplicate_external = await db.scalar(
                select(AssetJob).where(
                    AssetJob.client_id == principal.id,
                    AssetJob.external_asset_id == parsed.external_asset_id,
                )
            )
            if duplicate_external is not None:
                raise HTTPException(409, detail={"code": "EXTERNAL_ASSET_CONFLICT"})

            reference_by_name = {
                item.filename: item.model_dump(mode="json")
                for item in parsed.reference_views
            }
            input_manifest = {
                "schema_version": "retopology_input.v1",
                "project": {
                    "filename": project_filename,
                    "sha256": project_sha,
                    "size_bytes": project_size,
                },
                "reference_views": [
                    {
                        **reference_by_name[filename],
                        "sha256": reference_sha[filename],
                        "size_bytes": reference_sizes[filename],
                    }
                    for filename in sorted(reference_sha)
                ],
                "user_request": parsed.user_request,
            }
            manifest_path = bundle_root / "input_manifest.json"
            manifest_path.write_text(
                json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            bundle_path = staging / "retopology_input.zip"
            with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(project_path, project_filename)
                archive.write(manifest_path, "input_manifest.json")
                for filename in sorted(reference_sha):
                    archive.write(references_root / filename, f"references/{filename}")
            bundle_sha = sha256_path(bundle_path)
            bundle_size = bundle_path.stat().st_size
            shutil.rmtree(bundle_root)

            job_id = str(uuid.uuid4())
            job_root = cfg.asset_root / job_id
            staging.rename(job_root)
            options = parsed.options.model_dump(mode="json")
            options.update(
                {
                    "project_filename": project_filename,
                    "project_sha256": project_sha,
                    "reference_views": input_manifest["reference_views"],
                    "user_request": parsed.user_request,
                }
            )
            job = AssetJob(
                id=job_id,
                client_id=principal.id,
                external_asset_id=parsed.external_asset_id,
                job_type="RETOPOLOGY_PROCESS_V1",
                status="QUEUED",
                source_filename="retopology_input.zip",
                input_path=str(job_root / "retopology_input.zip"),
                input_sha256=bundle_sha,
                input_size_bytes=bundle_size,
                options=options,
                request_hash=request_hash,
                request_id=str(request.state.request_id),
            )
            db.add(job)
            await db.flush()
            await append_asset_event(
                db, job, details={"event": "asset.queued", "request_id": job.request_id}
            )
            db.add(
                AssetIdempotencyKey(
                    client_id=principal.id,
                    key=idempotency_key,
                    request_hash=request_hash,
                    job_id=job_id,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
            )
            await db.commit()
            return JSONResponse(await job_payload(job, db), 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/api/v1/assets/uv/process")
    async def create_uv_process_job(
        request: Request,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        asset: Annotated[UploadFile, File()],
        metadata: Annotated[str, Form()],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
        ],
    ) -> JSONResponse:
        try:
            parsed = AssetCreateMetadata.model_validate_json(metadata)
            filename = validate_asset_filename(asset.filename or "")
        except ValueError as exc:
            raise HTTPException(
                422, detail={"code": "ASSET_INPUT_INVALID", "message": str(exc)}
            ) from exc
        return await create_uploaded_job(
            request=request,
            principal=principal,
            db=db,
            upload=asset,
            filename=filename,
            external_asset_id=parsed.external_asset_id,
            options=parsed.options.model_dump(mode="json"),
            job_type="UV_PROCESS_V2",
            idempotency_key=idempotency_key,
            request_hash_builder=lambda input_sha: uv_process_request_hash(
                parsed, input_sha
            ),
        )

    @app.get("/api/v1/assets/jobs/{job_id}")
    async def get_job(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        return await job_payload(await owned_job(job_id, principal, db), db)

    @app.get("/api/v1/assets/jobs/{job_id}/events")
    async def asset_job_events(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        await owned_job(job_id, principal, db)
        try:
            initial_sequence = max(0, int(last_event_id or "0"))
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "LAST_EVENT_ID_INVALID"}) from exc

        async def stream() -> AsyncIterator[str]:
            sequence = initial_sequence
            while True:
                async with app.state.db.session() as event_db:
                    rows = list(
                        (
                            await event_db.scalars(
                                select(AssetJobEvent)
                                .where(
                                    AssetJobEvent.job_id == job_id,
                                    AssetJobEvent.sequence > sequence,
                                )
                                .order_by(AssetJobEvent.sequence)
                            )
                        ).all()
                    )
                    terminal = False
                    for item in rows:
                        sequence = item.sequence
                        data = json.dumps(
                            {
                                "job_id": job_id,
                                "status": item.status,
                                "stage": item.stage,
                                "progress": item.progress,
                                "message": item.message,
                                "estimated_remaining_seconds": item.estimated_remaining_seconds,
                                "details": item.details,
                                "created_at": item.created_at.isoformat(),
                            },
                            ensure_ascii=False,
                        )
                        yield f"id: {sequence}\nevent: asset-progress\ndata: {data}\n\n"
                        terminal = item.status in TERMINAL_ASSET_STATUSES
                    if terminal:
                        return
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/assets/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        job = await owned_job(job_id, principal, db)
        if job.status in TERMINAL_ASSET_STATUSES:
            return await job_payload(job, db)
        job.cancel_requested = True
        if job.status == "QUEUED":
            job.status = "CANCELLED"
            job.stage = "CANCELLED"
            job.stage_message = "任务已在执行前取消"
            job.estimated_remaining_seconds = 0
            job.finished_at = datetime.now(UTC)
        else:
            job.status = "CANCELLING"
            job.stage = "CANCELLING"
            job.stage_message = "取消请求已送达，等待当前安全点停止"
        job.last_progress_at = datetime.now(UTC)
        await append_asset_event(db, job, details={"event": "asset.cancel_requested"})
        await db.commit()
        return await job_payload(job, db)

    @app.get("/api/v1/assets/jobs/{job_id}/artifacts/{artifact_id}")
    async def download_artifact(
        job_id: str,
        artifact_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> FileResponse:
        job = await owned_job(job_id, principal, db)
        if not artifacts_are_downloadable(job):
            raise HTTPException(409, detail={"code": "ASSET_NOT_COMPLETE"})
        artifact = await db.get(AssetArtifact, artifact_id)
        if artifact is None or artifact.job_id != job.id:
            raise HTTPException(404, detail={"code": "ASSET_ARTIFACT_NOT_FOUND"})
        response = FileResponse(
            artifact.path,
            media_type=artifact.content_type,
            filename=artifact.filename,
        )
        response.headers["X-Artifact-SHA256"] = artifact.sha256
        return response

    @app.get("/api/v1/assets/capacity")
    async def capacity(
        _: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        cutoff = datetime.now(UTC) - timedelta(seconds=cfg.asset_worker_heartbeat_timeout_seconds)
        workers = list(
            (
                await db.scalars(
                    select(AssetWorker).where(
                        AssetWorker.status == "ONLINE",
                        AssetWorker.last_heartbeat_at >= cutoff,
                    )
                )
            ).all()
        )
        return {
            "schema_version": "1.0",
            "advisory": True,
            "online_workers": len(workers),
            "total_slots": sum(worker.max_concurrency for worker in workers),
            "used_slots": sum(worker.current_jobs for worker in workers),
            "available_slots": sum(
                max(0, worker.max_concurrency - worker.current_jobs) for worker in workers
            ),
            "as_of": datetime.now(UTC).isoformat(),
        }

    @app.post("/internal/v1/assets/workers/heartbeat")
    async def worker_heartbeat(
        body: WorkerHeartbeat,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        await verify_worker(request, await request.body())
        worker = await db.get(AssetWorker, body.worker_id)
        if worker is None:
            worker = AssetWorker(
                id=body.worker_id,
                display_name=body.display_name,
                node_id=body.node_id,
                hostname=body.hostname,
                blender_version=body.blender_version,
                skill_version=body.skill_version,
            )
            db.add(worker)
        worker.display_name = body.display_name
        worker.node_id = body.node_id
        worker.hostname = body.hostname
        worker.blender_version = body.blender_version
        worker.skill_version = body.skill_version
        worker.cpu_count = body.cpu_count
        # Windows Baker concurrency is represented by independent one-job
        # workers.  This keeps leases and process failures isolated while the
        # shared physical-GPU fence remains active until the last bake exits.
        worker.max_concurrency = (
            1 if is_substance_worker_id(body.worker_id) else body.max_concurrency
        )
        worker.current_jobs = body.current_jobs
        worker.codex_cli_version = body.codex_cli_version
        worker.codex_auth_status = body.codex_auth_status
        worker.codex_probe_status = body.codex_probe_status
        worker.codex_probe_latency_ms = body.codex_probe_latency_ms
        worker.codex_last_checked_at = body.codex_last_checked_at
        worker.codex_last_success_at = body.codex_last_success_at
        worker.codex_error_code = body.codex_error_code
        worker.retopoflow_version = body.retopoflow_version
        worker.retopoflow_revision = body.retopoflow_revision
        worker.retopoflow_probe_status = body.retopoflow_probe_status
        worker.retopoflow_probe_latency_ms = body.retopoflow_probe_latency_ms
        worker.retopoflow_last_checked_at = body.retopoflow_last_checked_at
        worker.retopoflow_error_code = body.retopoflow_error_code
        worker.last_heartbeat_at = datetime.now(UTC)
        resource_ok = (
            body.available_memory_mb >= cfg.asset_worker_min_available_memory_mb
            and body.load_1m / body.cpu_count <= cfg.asset_worker_max_load_per_cpu
        )
        runtime_version_ok = (
            body.blender_version == SUBSTANCE_VERSION
            if is_substance_worker_id(body.worker_id)
            else body.blender_version == "5.1.2"
        )
        worker.status = "ONLINE" if runtime_version_ok and resource_ok else "DRAINING"
        await db.commit()
        return {"accepted": True, "status": worker.status}

    @app.post("/internal/v1/assets/jobs/claim")
    async def claim_job(
        body: WorkerClaim,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
    ) -> JSONResponse:
        await verify_worker(request, await request.body())
        expired = list(
            (
                await db.scalars(
                    select(AssetJob)
                    .where(
                        AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                        AssetJob.lease_expires_at < datetime.now(UTC),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for stale in expired:
            previous_worker = await db.get(AssetWorker, stale.worker_id) if stale.worker_id else None
            if previous_worker is not None:
                previous_worker.current_jobs = max(0, previous_worker.current_jobs - 1)
            if stale.cancel_requested:
                stale.status = "CANCELLED"
                stale.stage = "CANCELLED"
                stale.stage_message = "Worker 租约失效后确认取消"
                stale.estimated_remaining_seconds = 0
                stale.finished_at = datetime.now(UTC)
            elif stale.attempt_count < cfg.asset_job_max_attempts:
                stale.status = "QUEUED"
                stale.stage = "RETRY_QUEUED"
                stale.stage_message = "Worker 租约失效，任务已安全返回队列"
                stale.estimated_remaining_seconds = None
                stale.worker_id = None
                stale.error_code = "ASSET_LEASE_EXPIRED"
                stale.error_message = "worker lease expired; job returned to the asset queue"
            else:
                stale.status = "FAILED"
                stale.stage = "FAILED"
                stale.stage_message = "Worker 多次失联，任务已终止"
                stale.estimated_remaining_seconds = 0
                stale.error_code = "ASSET_LEASE_EXPIRED"
                stale.error_message = "worker lease expired after maximum attempts"
                stale.finished_at = datetime.now(UTC)
            stale.lease_token_hash = None
            stale.lease_expires_at = None
            stale.last_progress_at = datetime.now(UTC)
            await release_substance_gpu_fence(db, stale, restore_active=False)
            await append_asset_event(
                db, stale, details={"event": "asset.lease_expired"}
            )
        worker = await db.get(AssetWorker, body.worker_id, with_for_update=True)
        cutoff = datetime.now(UTC) - timedelta(seconds=cfg.asset_worker_heartbeat_timeout_seconds)
        if (
            worker is None
            or worker.status != "ONLINE"
            or worker.last_heartbeat_at is None
            or as_utc(worker.last_heartbeat_at) < cutoff
            or worker.current_jobs >= worker.max_concurrency
            or body.available_memory_mb < cfg.asset_worker_min_available_memory_mb
            or body.load_1m / max(worker.cpu_count, 1) > cfg.asset_worker_max_load_per_cpu
        ):
            return JSONResponse({"job": None}, 200)
        substance_node: Node | None = None
        if is_substance_worker_id(worker.id):
            # Lock the physical GPU node before the asset job row.  The GPU
            # scheduler uses the same lock order, preventing a ComfyUI claim
            # from racing a native Windows Baker claim.
            substance_node = await db.scalar(
                select(Node)
                .where(Node.id == SUBSTANCE_GPU_NODE_ID)
                .with_for_update()
            )
            labels = dict(substance_node.labels or {}) if substance_node else {}
            fenced_job_ids = substance_fence_job_ids(labels)
            if (
                substance_node is None
                or (
                    substance_node.mode != "ACTIVE"
                    and not (substance_node.mode == "DRAINING" and fenced_job_ids)
                )
                or substance_node.health != "ONLINE"
                or substance_node.current_jobs != 0
                or substance_node.manual_reserved
                or substance_node.external_busy
                or substance_node.foreign_queue_detected
            ):
                return JSONResponse({"job": None}, 200)

        claim_query = (
            select(AssetJob)
            .join(ApiClient, ApiClient.id == AssetJob.client_id)
            .where(
                AssetJob.status == "QUEUED",
                AssetJob.cancel_requested.is_(False),
            )
        )
        if is_substance_worker_id(worker.id):
            claim_query = claim_query.where(AssetJob.job_type == "SUBSTANCE_BAKE_V1")
        else:
            claim_query = claim_query.where(AssetJob.job_type != "SUBSTANCE_BAKE_V1")
        job = await db.scalar(
            claim_query
            # Production always wins the next free slot.  This is deliberately
            # evaluated before queue age so an old load-test job cannot delay a
            # real caller.  FIFO remains stable inside each client-kind pool.
            .order_by(
                case((ApiClient.client_kind == "test", 1), else_=0),
                AssetJob.created_at,
                AssetJob.id,
            ).with_for_update(skip_locked=True)
        )
        if job is None:
            return JSONResponse({"job": None}, 200)
        if substance_node is not None:
            labels = dict(substance_node.labels or {})
            fenced_job_ids = substance_fence_job_ids(labels)
            if job.id not in fenced_job_ids:
                fenced_job_ids.append(job.id)
            labels[SUBSTANCE_FENCE_LABEL] = fenced_job_ids
            labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
            substance_node.labels = labels
            substance_node.mode = "DRAINING"
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        job.status = "CLAIMED"
        job.worker_id = worker.id
        job.lease_token_hash = lease_token_hash(token)
        job.lease_expires_at = now + timedelta(seconds=cfg.asset_worker_lease_seconds)
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.progress = max(job.progress, 1)
        job.stage = "CLAIMED"
        job.stage_message = f"任务已分配给 {worker.display_name}"
        job.estimated_remaining_seconds = {
            "UV_UNWRAP": 180,
            "UV_PROCESS_V2": 240,
            "RETOPOLOGY_AUDIT": 120,
            "RETOPOLOGY_PROCESS_V1": 900,
            "SUBSTANCE_BAKE_V1": 600,
        }.get(job.job_type, 300)
        job.last_progress_at = now
        worker.current_jobs += 1
        await append_asset_event(
            db,
            job,
            details={"event": "asset.claimed", "worker_id": worker.id},
        )
        await db.commit()
        return JSONResponse(
            {
                "job": {
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "source_filename": job.source_filename,
                    "input_sha256": job.input_sha256,
                    "options": job.options,
                    "lease_token": token,
                    "input_url": f"/internal/v1/assets/jobs/{job.id}/input",
                }
            },
            200,
        )

    async def leased_job(job_id: str, token: str, db: AsyncSession) -> AssetJob:
        job = await db.get(AssetJob, job_id, with_for_update=True)
        if (
            job is None
            or job.lease_token_hash is None
            or not hmac.compare_digest(job.lease_token_hash, lease_token_hash(token))
            or job.lease_expires_at is None
            or as_utc(job.lease_expires_at) <= datetime.now(UTC)
        ):
            raise HTTPException(409, detail={"code": "ASSET_LEASE_INVALID"})
        return job

    @app.get("/internal/v1/assets/jobs/{job_id}/input")
    async def worker_download_input(
        job_id: str,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> FileResponse:
        job = await leased_job(job_id, lease, db)
        return FileResponse(job.input_path, filename=job.source_filename)

    @app.post("/internal/v1/assets/jobs/{job_id}/progress")
    async def worker_progress(
        job_id: str,
        body: WorkerProgress,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        if job.cancel_requested:
            return {"cancel_requested": True}
        job.status = "RUNNING"
        job.progress = max(job.progress, body.progress)
        job.stage = body.stage
        job.stage_message = body.message
        job.estimated_remaining_seconds = body.estimated_remaining_seconds
        job.last_progress_at = datetime.now(UTC)
        job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=cfg.asset_worker_lease_seconds)
        await append_asset_event(
            db,
            job,
            details={"event": "asset.progress", "worker_id": job.worker_id},
        )
        await db.commit()
        return {"cancel_requested": False}

    @app.post("/internal/v1/assets/jobs/{job_id}/complete")
    async def worker_complete(
        job_id: str,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        blend: Annotated[UploadFile, File()],
        fbx: Annotated[UploadFile, File()],
        report: Annotated[UploadFile, File()],
        qa: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        uploads = {"blend": blend, "fbx": fbx, "report": report, "qa": qa}
        staging = cfg.asset_root / job.id / f".outputs-{uuid.uuid4().hex}"
        final = cfg.asset_root / job.id / "output"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        try:
            for kind, upload in uploads.items():
                filename, content_type = UV_REQUIRED_ARTIFACTS[kind]
                path = staging / filename
                digest = hashlib.sha256()
                size = 0
                with path.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                        destination.write(chunk)
                if size == 0:
                    raise HTTPException(422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind})
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        kind=kind,
                        filename=filename,
                        path=str(final / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                    )
                )
            try:
                qa_payload = json.loads((staging / "model_QA.json").read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise HTTPException(422, detail={"code": "ASSET_QA_INVALID"}) from exc
            hard_failures = qa_payload.get("hard_failures")
            if not isinstance(hard_failures, list) or hard_failures:
                raise HTTPException(422, detail={"code": "ASSET_QA_FAILED"})
            if final.exists():
                raise HTTPException(409, detail={"code": "ASSET_OUTPUT_ALREADY_EXISTS"})
            staging.rename(final)
            db.add_all(created)
            job.status = "SUCCEEDED"
            job.progress = 100
            job.stage = "SUCCEEDED"
            job.stage_message = "UV 结果与 QA 制品已校验并发布"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = datetime.now(UTC)
            job.lease_expires_at = None
            job.lease_token_hash = None
            worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
            if worker is not None:
                worker.current_jobs = max(0, worker.current_jobs - 1)
            await append_asset_event(db, job, details={"event": "asset.succeeded"})
            await db.commit()
            return {"accepted": True}
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/internal/v1/assets/jobs/{job_id}/retopology-complete")
    async def worker_complete_retopology_audit(
        job_id: str,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        audit: Annotated[UploadFile, File()],
        manifest: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        if job.job_type != "RETOPOLOGY_AUDIT":
            raise HTTPException(409, detail={"code": "ASSET_JOB_TYPE_MISMATCH"})
        uploads = {"audit": audit, "manifest": manifest}
        staging = cfg.asset_root / job.id / f".outputs-{uuid.uuid4().hex}"
        final = cfg.asset_root / job.id / "output"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        try:
            for kind, upload in uploads.items():
                filename, content_type = RETOPOLOGY_AUDIT_ARTIFACTS[kind]
                path = staging / filename
                digest = hashlib.sha256()
                size = 0
                with path.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                        destination.write(chunk)
                if size == 0:
                    raise HTTPException(
                        422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind}
                    )
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        kind=kind,
                        filename=filename,
                        path=str(final / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                    )
                )
            try:
                audit_payload = json.loads(
                    (staging / "retopology_audit.json").read_text("utf-8")
                )
                manifest_payload = json.loads(
                    (staging / "retopology_manifest.json").read_text("utf-8")
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AUDIT_INVALID"}
                ) from exc
            if audit_payload.get("schema_version") != 2:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AUDIT_SCHEMA_INVALID"}
                )
            objects = audit_payload.get("objects")
            if not isinstance(objects, dict) or not {"high", "reference", "low"}.issubset(
                objects
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AUDIT_OBJECTS_MISSING"}
                )
            visual_review = audit_payload.get("visual_review_required")
            if not isinstance(visual_review, list) or not {
                "front",
                "side",
                "top",
                "perspective",
            }.issubset(set(visual_review)):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_VISUAL_REVIEW_MISSING"}
                )
            if (
                manifest_payload.get("job_id") != job.id
                or manifest_payload.get("input_sha256") != job.input_sha256
                or manifest_payload.get("job_type") != job.job_type
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_MANIFEST_MISMATCH"}
                )
            if final.exists():
                raise HTTPException(409, detail={"code": "ASSET_OUTPUT_ALREADY_EXISTS"})
            staging.rename(final)
            db.add_all(created)
            audit_passed = audit_payload.get("audit_passed") is True
            job.status = "SUCCEEDED" if audit_passed else "FAILED"
            job.progress = 100
            job.stage = "SUCCEEDED" if audit_passed else "FAILED"
            job.stage_message = (
                "拓扑严格 QA 已通过，审计制品已发布"
                if audit_passed
                else "拓扑硬性 QA 未通过；诊断制品已保留，不可交付"
            )
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = job.last_progress_at
            job.error_code = None if audit_passed else "RETOPOLOGY_AUDIT_FAILED"
            failures = audit_payload.get("failures")
            job.error_message = (
                None
                if job.error_code is None
                else json.dumps(failures if isinstance(failures, list) else [], ensure_ascii=False)
            )
            job.lease_expires_at = None
            job.lease_token_hash = None
            worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
            if worker is not None:
                worker.current_jobs = max(0, worker.current_jobs - 1)
            await release_substance_gpu_fence(db, job)
            await append_asset_event(
                db,
                job,
                details={
                    "event": "asset.succeeded" if audit_passed else "asset.qa_failed",
                    "audit_passed": audit_passed,
                },
            )
            await db.commit()
            return {
                "accepted": True,
                "status": job.status,
                "review_required": False,
                "audit_passed": audit_passed,
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/internal/v1/assets/jobs/{job_id}/retopology-process-complete")
    async def worker_complete_retopology_process(
        job_id: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        if job.job_type != "RETOPOLOGY_PROCESS_V1":
            raise HTTPException(409, detail={"code": "ASSET_JOB_TYPE_MISMATCH"})
        form = await request.form()
        expected = dict(RETOPOLOGY_PROCESS_REQUIRED_ARTIFACTS)
        if job.options.get("reference_views"):
            expected.update(RETOPOLOGY_PROCESS_OPTIONAL_ARTIFACTS)
        uploads: dict[str, StarletteUploadFile] = {}
        for kind in expected:
            upload = form.get(kind)
            if not isinstance(upload, StarletteUploadFile):
                raise HTTPException(
                    422,
                    detail={"code": "ASSET_ARTIFACT_MISSING", "kind": kind},
                )
            uploads[kind] = upload
        unknown = set(form.keys()) - set(expected)
        if unknown:
            raise HTTPException(
                422,
                detail={"code": "ASSET_ARTIFACT_UNEXPECTED", "kinds": sorted(unknown)},
            )

        staging = cfg.asset_root / job.id / f".outputs-{uuid.uuid4().hex}"
        final = cfg.asset_root / job.id / "output"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        try:
            for kind, upload in uploads.items():
                filename, content_type = expected[kind]
                if upload.filename != filename:
                    raise HTTPException(
                        422,
                        detail={
                            "code": "ASSET_ARTIFACT_FILENAME_MISMATCH",
                            "kind": kind,
                        },
                    )
                path = staging / filename
                digest = hashlib.sha256()
                size = 0
                with path.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                        destination.write(chunk)
                if size == 0:
                    raise HTTPException(
                        422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind}
                    )
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        kind=kind,
                        filename=filename,
                        path=str(final / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                    )
                )
            try:
                report_payload = json.loads(
                    (staging / "retopology_process_report.json").read_text("utf-8")
                )
                baseline_payload = json.loads(
                    (staging / "retopology_baseline_audit.json").read_text("utf-8")
                )
                audit_payload = json.loads(
                    (staging / "retopology_final_audit.json").read_text("utf-8")
                )
                manifest_payload = json.loads(
                    (staging / "retopology_manifest.json").read_text("utf-8")
                )
                agent_plan_payload = json.loads(
                    (staging / "retopology_agent_plan.json").read_text("utf-8")
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_PROCESS_JSON_INVALID"}
                ) from exc
            if baseline_payload.get("schema_version") != 2 or audit_payload.get(
                "schema_version"
            ) != 2:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AUDIT_SCHEMA_INVALID"}
                )
            if report_payload.get("schema_version") != "retopology_process_report.v1":
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_PROCESS_REPORT_INVALID"}
                )
            quality_gate = report_payload.get("quality_gate")
            if (
                not isinstance(quality_gate, dict)
                or quality_gate.get("schema_version") != "retopology_quality_gate.v2"
                or not isinstance(quality_gate.get("passed"), bool)
                or not isinstance(quality_gate.get("failures"), list)
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_QUALITY_GATE_INVALID"}
                )
            if (
                agent_plan_payload.get("recommended_algorithm")
                not in {"quadriflow", "cleanup_existing"}
                or not isinstance(agent_plan_payload.get("target_faces"), int)
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AGENT_PLAN_INVALID"}
                )
            if (
                manifest_payload.get("schema_version")
                != "retopology_process_manifest.v1"
                or manifest_payload.get("job_id") != job.id
                or manifest_payload.get("job_type") != job.job_type
                or manifest_payload.get("input_sha256") != job.input_sha256
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_MANIFEST_MISMATCH"}
                )
            expected_objects = {
                "high": job.options["high_object"],
                "reference": job.options["reference_object"],
                "current": job.options["low_object"],
                "generated": job.options["generated_low_object"],
            }
            if manifest_payload.get("objects") != expected_objects:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_MANIFEST_OBJECTS_MISMATCH"}
                )
            # The old key is accepted while workers roll. This is visual
            # evidence for deterministic QA, not a manual approval gate.
            visual_evidence = manifest_payload.get("visual_evidence") or manifest_payload.get(
                "visual_review"
            )
            if (
                not isinstance(visual_evidence, dict)
                or visual_evidence.get("required") is not True
                or set(visual_evidence.get("views", []))
                != {"front", "side", "top", "perspective"}
                or set(visual_evidence.get("roles", []))
                != {"high", "reference", "generated"}
                or visual_evidence.get("manual_review_required", False) is not False
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_VISUAL_EVIDENCE_MISSING"}
                )
            agent_plan = manifest_payload.get("agent_plan")
            if (
                not isinstance(agent_plan, dict)
                or agent_plan.get("required") is not True
                or agent_plan.get("recommended_algorithm")
                != agent_plan_payload.get("recommended_algorithm")
                or agent_plan.get("recommended_target_faces")
                != agent_plan_payload.get("target_faces")
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AGENT_MANIFEST_MISMATCH"}
                )
            if (
                manifest_payload.get("source_preserved") is not True
                or report_payload.get("source_preserved") is not True
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_SOURCE_PROTECTION_FAILED"}
                )
            topology_goal_met = bool(report_payload.get("topology_goal_met"))
            if (
                topology_goal_met != bool(quality_gate.get("passed"))
                or manifest_payload.get("quality_gate") != quality_gate
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_QUALITY_GATE_MISMATCH"}
                )
            if manifest_payload.get("automatic_final_promotion_allowed") != (
                bool(audit_payload.get("audit_passed")) and topology_goal_met
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_AUTOMATIC_DELIVERY_INVALID"}
                )
            for filename, (_, content_type) in {
                **RETOPOLOGY_PROCESS_REQUIRED_FILENAMES,
                **(
                    RETOPOLOGY_PROCESS_OPTIONAL_FILENAMES
                    if job.options.get("reference_views")
                    else {}
                ),
            }.items():
                if content_type != "image/png":
                    continue
                try:
                    with Image.open(staging / filename) as image:
                        if image.width * image.height > cfg.max_image_pixels:
                            raise HTTPException(
                                413,
                                detail={
                                    "code": "RETOPOLOGY_REVIEW_IMAGE_TOO_LARGE",
                                    "filename": filename,
                                },
                            )
                        image.verify()
                except (OSError, UnidentifiedImageError) as exc:
                    raise HTTPException(
                        422,
                        detail={"code": "RETOPOLOGY_REVIEW_IMAGE_INVALID", "filename": filename},
                    ) from exc
            if final.exists():
                raise HTTPException(409, detail={"code": "ASSET_OUTPUT_ALREADY_EXISTS"})
            staging.rename(final)
            db.add_all(created)
            audit_passed = audit_payload.get("audit_passed") is True
            report_promotable = report_payload.get("topology_goal_met") is True
            manifest_promotable = (
                manifest_payload.get("automatic_final_promotion_allowed") is True
            )
            quality_passed = audit_passed and report_promotable and manifest_promotable
            quality_failures: list[str] = []
            reported_failures = audit_payload.get("failures")
            if isinstance(reported_failures, list):
                quality_failures.extend(str(item) for item in reported_failures)
            gate_failures = quality_gate.get("failures")
            if isinstance(gate_failures, list):
                quality_failures.extend(str(item) for item in gate_failures)
            if not report_promotable:
                quality_failures.append(
                    "topology_goal_met=false: target face/topology requirement was not met"
                )
            if not manifest_promotable:
                quality_failures.append(
                    "automatic_final_promotion_allowed=false: candidate is not deliverable"
                )
            # Keep the measured QA result authoritative even when operations
            # temporarily make it advisory.  Advisory mode changes delivery
            # disposition only: artifact integrity, manifest identity and
            # source-fingerprint protection above remain hard failures.
            quality_failures = list(dict.fromkeys(quality_failures))
            advisory_warning = (
                cfg.retopology_qa_enforcement == "advisory" and not quality_passed
            )
            delivery_allowed = quality_passed or advisory_warning
            if delivery_allowed:
                # The worker deliberately uploads versioned candidates. Once
                # the server accepts delivery (strict pass or advisory), expose
                # those exact immutable bytes under the established V5 final
                # model kinds. Keeping candidate_* here makes clients classify
                # usable BLEND/FBX files as diagnostics and report zero final
                # deliverables even though the job succeeded.
                for artifact in created:
                    promoted = RETOPOLOGY_FINAL_MODEL_ARTIFACTS.get(artifact.kind)
                    if promoted is not None:
                        artifact.kind, artifact.filename = promoted
            if advisory_warning:
                job.options = {
                    **job.options,
                    "qa_warning": {
                        "code": "RETOPOLOGY_QUALITY_GATE_WARNING",
                        "enforcement": cfg.retopology_qa_enforcement,
                        "audit_passed": audit_passed,
                        "topology_goal_met": report_promotable,
                        "automatic_final_promotion_allowed": manifest_promotable,
                        "failures": quality_failures,
                    },
                }
            job.status = "SUCCEEDED" if delivery_allowed else "FAILED"
            job.progress = 100
            job.stage = "SUCCEEDED" if delivery_allowed else "FAILED"
            if quality_passed:
                job.stage_message = (
                    "候选已通过严格 QA 与三组四视图生成，自动发布交付"
                )
                completion_event = "asset.succeeded"
            elif advisory_warning:
                job.stage_message = (
                    "候选已生成并交付；严格 QA 未通过，告警与完整报告已保留"
                )
                completion_event = "asset.succeeded_with_warnings"
            else:
                job.stage_message = (
                    "候选未满足拓扑目标或硬性 QA；仅保留诊断制品，不可交付"
                )
                completion_event = "asset.qa_failed"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = job.last_progress_at
            job.error_code = (
                None if delivery_allowed else "RETOPOLOGY_QUALITY_GATE_FAILED"
            )
            job.error_message = (
                None
                if job.error_code is None
                else json.dumps(quality_failures, ensure_ascii=False)
            )
            job.lease_expires_at = None
            job.lease_token_hash = None
            worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
            if worker is not None:
                worker.current_jobs = max(0, worker.current_jobs - 1)
            await release_substance_gpu_fence(db, job)
            await append_asset_event(
                db,
                job,
                details={
                    "event": completion_event,
                    "warning_code": (
                        "RETOPOLOGY_QUALITY_GATE_WARNING"
                        if advisory_warning
                        else None
                    ),
                    "qa_enforcement": cfg.retopology_qa_enforcement,
                    "quality_gate_passed": quality_passed,
                    "quality_failures": quality_failures,
                    "audit_passed": audit_passed,
                    "topology_goal_met": report_promotable,
                    "automatic_final_promotion_allowed": manifest_promotable,
                },
            )
            await db.commit()
            return {
                "accepted": True,
                "status": job.status,
                "review_required": False,
                "audit_passed": audit_passed,
                "quality_gate_passed": quality_passed,
                "qa_enforcement": cfg.retopology_qa_enforcement,
                "delivered_with_warnings": advisory_warning,
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/internal/v1/assets/jobs/{job_id}/uv-v2-complete")
    async def worker_complete_uv_v2(
        job_id: str,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        blend: Annotated[UploadFile, File()],
        fbx: Annotated[UploadFile, File()],
        report: Annotated[UploadFile, File()],
        qa: Annotated[UploadFile, File()],
        fbx_qa: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        if job.job_type != "UV_PROCESS_V2":
            raise HTTPException(409, detail={"code": "ASSET_JOB_TYPE_MISMATCH"})
        stem = Path(job.source_filename).stem
        contract = {
            "blend": (f"{stem}_PBR_UV.blend", "application/octet-stream"),
            "fbx": (f"{stem}_PBR_UV.fbx", "application/octet-stream"),
            "report": (f"{stem}_PBR_UV_report.json", "application/json"),
            "qa": (f"{stem}_PBR_UV_QA.json", "application/json"),
            "fbx_qa": (f"{stem}_PBR_UV_FBX_QA.json", "application/json"),
        }
        uploads = {
            "blend": blend,
            "fbx": fbx,
            "report": report,
            "qa": qa,
            "fbx_qa": fbx_qa,
        }
        staging = cfg.asset_root / job.id / f".outputs-{uuid.uuid4().hex}"
        final = cfg.asset_root / job.id / "output"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        try:
            for kind, upload in uploads.items():
                filename, content_type = contract[kind]
                path = staging / filename
                digest = hashlib.sha256()
                size = 0
                with path.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                        destination.write(chunk)
                if size == 0:
                    raise HTTPException(
                        422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind}
                    )
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        kind=kind,
                        filename=filename,
                        path=str(final / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                    )
                )
            try:
                report_payload = json.loads(
                    (staging / contract["report"][0]).read_text("utf-8")
                )
                blend_qa_payload = json.loads(
                    (staging / contract["qa"][0]).read_text("utf-8")
                )
                fbx_qa_payload = json.loads(
                    (staging / contract["fbx_qa"][0]).read_text("utf-8")
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(422, detail={"code": "ASSET_QA_INVALID"}) from exc
            if report_payload.get("input") not in {None, job.source_filename} and Path(
                str(report_payload.get("input"))
            ).name != job.source_filename:
                raise HTTPException(422, detail={"code": "ASSET_REPORT_INPUT_MISMATCH"})
            for label, payload in (
                ("blend", blend_qa_payload),
                ("fbx_readback", fbx_qa_payload),
            ):
                hard_failures = payload.get("hard_failures")
                if not isinstance(hard_failures, list):
                    raise HTTPException(
                        422, detail={"code": "ASSET_QA_INVALID", "qa": label}
                    )
                if hard_failures or payload.get("passed") is not True:
                    raise HTTPException(
                        422, detail={"code": "ASSET_QA_FAILED", "qa": label}
                    )
            if final.exists():
                raise HTTPException(409, detail={"code": "ASSET_OUTPUT_ALREADY_EXISTS"})
            staging.rename(final)
            db.add_all(created)
            job.status = "SUCCEEDED"
            job.progress = 100
            job.stage = "SUCCEEDED"
            job.stage_message = "PBR UV、FBX 回读与双重 QA 已通过并发布"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = datetime.now(UTC)
            job.lease_expires_at = None
            job.lease_token_hash = None
            worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
            if worker is not None:
                worker.current_jobs = max(0, worker.current_jobs - 1)
            await append_asset_event(db, job, details={"event": "asset.succeeded"})
            await db.commit()
            return {"accepted": True, "status": job.status}
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/internal/v1/assets/jobs/{job_id}/substance-complete")
    async def worker_complete_substance(
        job_id: str,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        result: Annotated[UploadFile, File()],
        log: Annotated[UploadFile, File()],
        ao: Annotated[UploadFile | None, File()] = None,
        normal_dx: Annotated[UploadFile | None, File()] = None,
        normal_gl: Annotated[UploadFile | None, File()] = None,
        world_normal: Annotated[UploadFile | None, File()] = None,
        curvature: Annotated[UploadFile | None, File()] = None,
        thickness: Annotated[UploadFile | None, File()] = None,
        position: Annotated[UploadFile | None, File()] = None,
        base_color: Annotated[UploadFile | None, File()] = None,
        roughness: Annotated[UploadFile | None, File()] = None,
        metallic: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        if job.job_type != "SUBSTANCE_BAKE_V1":
            raise HTTPException(409, detail={"code": "ASSET_JOB_TYPE_MISMATCH"})
        profile = str(job.options.get("profile", ""))
        contract = SUBSTANCE_BAKE_OUTPUTS.get(profile)
        if contract is None:
            raise HTTPException(409, detail={"code": "SUBSTANCE_PROFILE_INVALID"})
        supplied: dict[str, UploadFile | None] = {
            "ao": ao,
            "normal_dx": normal_dx,
            "normal_gl": normal_gl,
            "world_normal": world_normal,
            "curvature": curvature,
            "thickness": thickness,
            "position": position,
            "base_color": base_color,
            "roughness": roughness,
            "metallic": metallic,
            "result": result,
            "log": log,
        }
        if any(supplied[kind] is None for kind in contract):
            raise HTTPException(422, detail={"code": "SUBSTANCE_ARTIFACT_MISSING"})
        if any(upload is not None and kind not in contract for kind, upload in supplied.items()):
            raise HTTPException(422, detail={"code": "SUBSTANCE_ARTIFACT_UNEXPECTED"})

        staging = cfg.asset_root / job.id / f".outputs-{uuid.uuid4().hex}"
        final = cfg.asset_root / job.id / "output"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        actual_sha: dict[str, str] = {}
        try:
            for kind, (filename, content_type) in contract.items():
                upload = supplied[kind]
                assert upload is not None
                path = staging / filename
                digest = hashlib.sha256()
                size = 0
                with path.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > cfg.asset_max_upload_bytes:
                            raise HTTPException(413, detail={"code": "ASSET_TOO_LARGE"})
                        digest.update(chunk)
                        destination.write(chunk)
                if size == 0:
                    raise HTTPException(
                        422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind}
                    )
                actual_sha[kind] = digest.hexdigest()
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        kind=kind,
                        filename=filename,
                        path=str(final / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=actual_sha[kind],
                    )
                )

            expected_resolution = int(job.options.get("resolution", 0))
            for kind in (
                "ao", "normal_dx", "normal_gl", "world_normal", "curvature",
                "thickness", "position", "base_color", "roughness", "metallic",
            ):
                if kind not in contract:
                    continue
                try:
                    with Image.open(staging / contract[kind][0]) as image:
                        image.verify()
                    with Image.open(staging / contract[kind][0]) as image:
                        if image.format != "PNG" or image.size != (
                            expected_resolution,
                            expected_resolution,
                        ):
                            raise HTTPException(
                                422,
                                detail={"code": "SUBSTANCE_IMAGE_INVALID", "kind": kind},
                            )
                except (OSError, UnidentifiedImageError) as exc:
                    raise HTTPException(
                        422, detail={"code": "SUBSTANCE_IMAGE_INVALID", "kind": kind}
                    ) from exc

            try:
                result_payload = json.loads(
                    (staging / contract["result"][0]).read_text("utf-8")
                )
                log_text = (staging / contract["log"][0]).read_text(
                    "utf-8", errors="replace"
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    422, detail={"code": "SUBSTANCE_RESULT_INVALID"}
                ) from exc
            tool = result_payload.get("tool") or {}
            execution = result_payload.get("execution") or {}
            output_hashes = result_payload.get("output_sha256") or {}
            if (
                result_payload.get("schema_version") != 1
                or result_payload.get("job_id") != job.id
                or result_payload.get("status") != "SUCCEEDED"
                or result_payload.get("profile") != profile
                or tool.get("version") != "15.1.0"
                or tool.get("exe_sha256")
                != "7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D"
                or execution.get("exit_code") != 0
                or any(output_hashes.get(kind) != actual_sha[kind] for kind in contract if kind not in {"result", "log"})
                or "Bake finished successfully" not in log_text
            ):
                raise HTTPException(422, detail={"code": "SUBSTANCE_RESULT_INVALID"})

            if final.exists():
                raise HTTPException(409, detail={"code": "ASSET_OUTPUT_ALREADY_EXISTS"})
            staging.rename(final)
            db.add_all(created)
            job.status = "SUCCEEDED"
            job.progress = 100
            job.stage = "SUCCEEDED"
            job.stage_message = "Substance 3D Baker 原生 Windows 结果已校验并原子发布"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = datetime.now(UTC)
            job.lease_expires_at = None
            job.lease_token_hash = None
            worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
            if worker is not None:
                worker.current_jobs = max(0, worker.current_jobs - 1)
            await release_substance_gpu_fence(db, job)
            await append_asset_event(
                db,
                job,
                details={
                    "event": "asset.succeeded",
                    "profile": profile,
                    "runtime": "worker-3090-b-windows",
                },
            )
            await db.commit()
            return {"accepted": True, "status": job.status}
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.post("/internal/v1/assets/jobs/{job_id}/fail")
    async def worker_fail(
        job_id: str,
        body: WorkerFailure,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db)
        worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
        if worker is not None:
            worker.current_jobs = max(0, worker.current_jobs - 1)
        if job.cancel_requested:
            job.status = "CANCELLED"
            job.stage = "CANCELLED"
            job.stage_message = "任务已在 Worker 安全点取消"
            job.estimated_remaining_seconds = 0
        elif body.retryable and job.attempt_count < cfg.asset_job_max_attempts:
            job.status = "QUEUED"
            job.stage = "RETRY_QUEUED"
            job.stage_message = "执行失败，任务已按策略返回队列重试"
            job.estimated_remaining_seconds = None
            job.worker_id = None
        else:
            job.status = "FAILED"
            job.stage = "FAILED"
            job.stage_message = "任务执行失败且不会再次自动重试"
            job.estimated_remaining_seconds = 0
            job.finished_at = datetime.now(UTC)
        job.error_code = body.code
        job.error_message = body.message
        job.lease_token_hash = None
        job.lease_expires_at = None
        job.last_progress_at = datetime.now(UTC)
        await release_substance_gpu_fence(db, job)
        await append_asset_event(
            db,
            job,
            details={
                "event": "asset.failed" if job.status == "FAILED" else "asset.retry_queued",
                "error_code": body.code,
                "retryable": body.retryable,
            },
        )
        await db.commit()
        return {"accepted": True, "status": job.status}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gpu_control_asset_api.main:app", host="0.0.0.0", port=8010)
