import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.gpu_control_core.assets import (
    AssetCreateMetadata,
    asset_request_hash,
    lease_token_hash,
    validate_asset_filename,
)
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import (
    ApiClient,
    ApiKey,
    AssetArtifact,
    AssetIdempotencyKey,
    AssetJob,
    AssetWorker,
)
from packages.gpu_control_core.security import sign_agent_request, verify_api_key
from packages.gpu_control_core.settings import Settings, get_settings

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
TERMINAL_ASSET_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
REQUIRED_ARTIFACTS = {
    "blend": ("model_PBR_UV.blend", "application/octet-stream"),
    "fbx": ("model_PBR_UV.fbx", "application/octet-stream"),
    "report": ("model_report.json", "application/json"),
    "qa": ("model_QA.json", "application/json"),
}


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive test values and PostgreSQL values."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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


class WorkerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=64)
    load_1m: float = Field(ge=0, le=4096)
    available_memory_mb: int = Field(ge=0)


class WorkerProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: float = Field(ge=0, le=99.9)


class WorkerFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[A-Z0-9_]{3,64}$")
    message: str = Field(min_length=1, max_length=4000)
    retryable: bool = True


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
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

    app = FastAPI(title="Unified Scheduling Center - Asset API", version="1.0.0", lifespan=lifespan)

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

    async def api_principal(
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> Principal:
        client: ApiClient | None = None
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
            source_ip = str(
                ipaddress.ip_address(request.client.host if request.client else "127.0.0.1")
            )
            clients = list(
                (await db.scalars(select(ApiClient).where(ApiClient.role == "client"))).all()
            )
            matches = [candidate for candidate in clients if source_ip in (candidate.allowed_ips or [])]
            if len(matches) == 1:
                client = matches[0]
            elif len(matches) > 1:
                raise HTTPException(409, detail={"code": "CLIENT_IP_CONFLICT"})
        if client is None or not client.enabled or client.role != "client":
            raise HTTPException(401, detail={"code": "AUTH_FAILED"})
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

    async def job_payload(job: AssetJob, db: AsyncSession) -> dict[str, Any]:
        artifacts: list[dict[str, Any]] = []
        if job.status == "SUCCEEDED":
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
            "external_asset_id": job.external_asset_id,
            "job_type": job.job_type,
            "status": job.status,
            "progress": job.progress,
            "source_filename": job.source_filename,
            "input_sha256": job.input_sha256,
            "options": job.options,
            "worker_id": job.worker_id,
            "attempt_count": job.attempt_count,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": {"code": job.error_code, "message": job.error_message}
            if job.error_code
            else None,
            "artifacts": artifacts,
        }

    async def owned_job(job_id: str, principal: Principal, db: AsyncSession) -> AssetJob:
        job = await db.get(AssetJob, job_id)
        if job is None or job.client_id != principal.id:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        return job

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
        staging = cfg.asset_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        source = staging / filename
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("xb") as destination:
                while chunk := await asset.read(1024 * 1024):
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
            request_hash = asset_request_hash(parsed, input_sha)
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
                    AssetJob.external_asset_id == parsed.external_asset_id,
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
                external_asset_id=parsed.external_asset_id,
                status="QUEUED",
                source_filename=filename,
                input_path=str(job_root / filename),
                input_sha256=input_sha,
                input_size_bytes=size,
                options=parsed.options.model_dump(mode="json"),
                request_hash=request_hash,
                request_id=str(request.state.request_id),
            )
            db.add(job)
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
            payload = await job_payload(job, db)
            payload["status_url"] = f"/api/v1/assets/jobs/{job.id}"
            return JSONResponse(payload, 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @app.get("/api/v1/assets/jobs/{job_id}")
    async def get_job(
        job_id: str,
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        return await job_payload(await owned_job(job_id, principal, db), db)

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
            job.finished_at = datetime.now(UTC)
        else:
            job.status = "CANCELLING"
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
        if job.status != "SUCCEEDED":
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
        worker.max_concurrency = body.max_concurrency
        worker.current_jobs = body.current_jobs
        worker.last_heartbeat_at = datetime.now(UTC)
        resource_ok = (
            body.available_memory_mb >= cfg.asset_worker_min_available_memory_mb
            and body.load_1m / body.cpu_count <= cfg.asset_worker_max_load_per_cpu
        )
        worker.status = "ONLINE" if body.blender_version == "5.1.2" and resource_ok else "DRAINING"
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
                stale.finished_at = datetime.now(UTC)
            elif stale.attempt_count < cfg.asset_job_max_attempts:
                stale.status = "QUEUED"
                stale.worker_id = None
                stale.error_code = "ASSET_LEASE_EXPIRED"
                stale.error_message = "worker lease expired; job returned to the asset queue"
            else:
                stale.status = "FAILED"
                stale.error_code = "ASSET_LEASE_EXPIRED"
                stale.error_message = "worker lease expired after maximum attempts"
                stale.finished_at = datetime.now(UTC)
            stale.lease_token_hash = None
            stale.lease_expires_at = None
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
        job = await db.scalar(
            select(AssetJob)
            .where(AssetJob.status == "QUEUED", AssetJob.cancel_requested.is_(False))
            .order_by(AssetJob.created_at)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return JSONResponse({"job": None}, 200)
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        job.status = "CLAIMED"
        job.worker_id = worker.id
        job.lease_token_hash = lease_token_hash(token)
        job.lease_expires_at = now + timedelta(seconds=cfg.asset_worker_lease_seconds)
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.progress = max(job.progress, 1)
        worker.current_jobs += 1
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
        job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=cfg.asset_worker_lease_seconds)
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
                filename, content_type = REQUIRED_ARTIFACTS[kind]
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
            job.finished_at = datetime.now(UTC)
            job.lease_expires_at = None
            job.lease_token_hash = None
            worker = await db.get(AssetWorker, job.worker_id) if job.worker_id else None
            if worker is not None:
                worker.current_jobs = max(0, worker.current_jobs - 1)
            await db.commit()
            return {"accepted": True}
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
        elif body.retryable and job.attempt_count < cfg.asset_job_max_attempts:
            job.status = "QUEUED"
            job.worker_id = None
        else:
            job.status = "FAILED"
            job.finished_at = datetime.now(UTC)
        job.error_code = body.code
        job.error_message = body.message
        job.lease_token_hash = None
        job.lease_expires_at = None
        await db.commit()
        return {"accepted": True, "status": job.status}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gpu_control_asset_api.main:app", host="0.0.0.0", port=8010)
