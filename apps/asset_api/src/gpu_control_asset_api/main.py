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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from packages.gpu_control_core.admission import (
    TERMINAL_ASSET_WORK_STATUSES,
    active_production_work_exists,
    client_is_load_test,
)
from packages.gpu_control_core.assets import (
    AssetCreateMetadata,
    RETOPOLOGY_V6_POLICY_SHA256,
    RetopologyAuditMetadata,
    SubstanceBakeMetadata,
    adapt_retopology_v6_metadata_json,
    asset_request_hash,
    lease_token_hash,
    retopology_audit_request_hash,
    retopology_v6_process_request_hash,
    substance_bake_request_hash,
    uv_process_request_hash,
    validate_asset_filename,
    validate_baker_filename,
    validate_baker_texture_filename,
    validate_reference_image_filename,
)
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import (
    TERMINAL_BATCH_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from packages.gpu_control_core.retopology_v6 import (
    validate_contract_payload,
    verify_runtime_resources,
)
from packages.gpu_control_core.models import (
    ApiClient,
    ApiKey,
    AssetArtifact,
    AssetIdempotencyKey,
    AssetJob,
    AssetJobEvent,
    AssetWorker,
    Job,
    JobBatch,
    Node,
)
from packages.gpu_control_core.scheduling import (
    SUBSTANCE_DRAIN_OWNER,
    SUBSTANCE_DRAIN_OWNER_LABEL,
    SUBSTANCE_FENCE_LABEL,
    SUBSTANCE_GPU_NODE_ID,
    SUBSTANCE_LEGACY_FENCE_LABEL,
    SUBSTANCE_MAX_PARALLEL,
    SUBSTANCE_PENDING_RESERVATION_LABEL,
    SUBSTANCE_RECOVERY_REQUIRED_LABEL,
    SUBSTANCE_WORKER_ID,
    SUBSTANCE_WORKER_ID_PREFIX,
    linux_asset_claim_allowed,
    substance_fence_job_ids,
    substance_pending_reservation,
)
from packages.gpu_control_core.security import sign_agent_request, verify_api_key
from packages.gpu_control_core.settings import Settings, get_settings

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
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
SUBSTANCE_BAKE_COMMAND_COUNTS = {
    "ao-self-v1": 1,
    "normal-dx-v1": 1,
    "pbr-core-v1": 2,
    "li3d-pbr-full-v2": 10,
}
CODEX_REQUIRED_JOB_TYPES = frozenset(
    {"RETOPOLOGY_PROCESS_V1", "RETOPOLOGY_PROCESS_V2"}
)
RETOPOLOGY_V6_SKILL_VERSION = "asset-skills-retopology-v6.0.0"


@dataclass(frozen=True, slots=True)
class AssetCompletionSnapshot:
    """Immutable job contract used while completion files are validated off-lock."""

    id: str
    job_type: str
    worker_id: str | None
    source_filename: str
    input_sha256: str
    options: dict[str, Any]


async def decrement_asset_worker_jobs_atomic(
    db: AsyncSession,
    worker_id: str | None,
) -> None:
    """Release one durable Worker slot without overwriting a concurrent claim.

    Terminal/retry callers keep their assigned ``AssetJob`` row locked and
    non-QUEUED until this statement has executed.  Claims serialize on the
    Worker row and only try to lock QUEUED jobs, so that state partition cannot
    form a Worker -> Job / Job -> Worker deadlock cycle.  The in-database
    arithmetic also preserves a claim increment that commits before this
    decrement and clamps stale counters at zero.
    """

    if worker_id is None:
        return
    await db.execute(
        update(AssetWorker)
        .where(AssetWorker.id == worker_id)
        .values(
            current_jobs=case(
                (AssetWorker.current_jobs > 0, AssetWorker.current_jobs - 1),
                else_=0,
            )
        )
        .execution_options(synchronize_session=False)
    )


async def persist_completion_upload(
    upload: UploadFile | StarletteUploadFile,
    destination: Path,
    *,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """Write one private completion artifact without holding database locks."""

    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise HTTPException(413, detail={"code": "ASSET_TOO_LARGE"})
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    return digest.hexdigest(), size


def fsync_completion_staging(staging: Path) -> None:
    """Persist directory entries before DB rows make staged artifacts visible."""

    descriptor = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


SUBSTANCE_VERSION = "substance-15.1.0"
SUBSTANCE_SKILL_VERSION = "substance-baker-2026.08.03-v6"
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
RETOPOLOGY_V6_ROOT = Path("/opt/li3d/retopology-v6")
RETOPOLOGY_V6_ARTIFACTS = {
    "final_low_blend": ("final_low.blend", "application/octet-stream"),
    "final_low_exchange": ("final_low.fbx", "application/octet-stream"),
    "execution_plan": ("execution_plan.json", "application/json"),
    "qa_report": ("qa_report.json", "application/json"),
    "comparison_contact_sheet": ("comparison_contact_sheet.png", "image/png"),
    "wireframe_contact_sheet": ("wireframe_contact_sheet.png", "image/png"),
    "manifest": ("manifest.json", "application/json"),
    "result": ("result.json", "application/json"),
    "formal_agent_receipt": ("formal_agent_receipt.json", "application/json"),
    "formal_agent_events": ("formal_agent_events.jsonl", "application/x-ndjson"),
    "qa_agent_events": ("qa_agent_events.jsonl", "application/x-ndjson"),
}
RETOPOLOGY_V6_RESULT_ARTIFACT_ROLES = frozenset(
    {
        "final_low_blend",
        "final_low_exchange",
        "execution_plan",
        "qa_report",
        "comparison_contact_sheet",
        "wireframe_contact_sheet",
        "manifest",
    }
)
RETOPOLOGY_DIRECT_V2_ARTIFACTS = {
    "blend": ("final_low.blend", "application/octet-stream"),
    "fbx": ("final_low.fbx", "application/octet-stream"),
    "generation_report": ("generation_report.json", "application/json"),
    "delivery_manifest": ("delivery_manifest.json", "application/json"),
    "result": ("result.json", "application/json"),
    "agent_events": ("agent_events.jsonl", "application/x-ndjson"),
    "wrapper_events": ("wrapper_events.jsonl", "application/x-ndjson"),
}
RETOPOLOGY_DIAGNOSTIC_ERROR_CODES = frozenset(
    {"RETOPOLOGY_AUDIT_FAILED", "RETOPOLOGY_QUALITY_GATE_FAILED"}
)


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive test values and PostgreSQL values."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def is_postgres_lock_not_available(exc: DBAPIError) -> bool:
    original = exc.orig
    return (
        getattr(original, "sqlstate", None) == "55P03"
        or getattr(original, "pgcode", None) == "55P03"
    )


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_substance_input_bundle(
    staging: Path,
    input_root: Path,
    request_document: dict[str, Any],
    bundle_files: dict[str, str],
) -> tuple[Path, str, int]:
    """Build and hash the immutable Baker bundle outside the admission lock."""

    workspace = input_root.parent
    request_path = workspace / "request.json"
    request_path.write_text(
        json.dumps(request_document, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    publish_root = staging / "publish"
    publish_root.mkdir(parents=False, exist_ok=False)
    bundle = publish_root / "substance_bake_input.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(request_path, "request.json")
        for bundle_name in bundle_files.values():
            archive.write(input_root / bundle_name, f"input/{bundle_name}")
    bundle_sha = sha256_path(bundle)
    bundle_size = bundle.stat().st_size
    shutil.rmtree(workspace)
    return publish_root, bundle_sha, bundle_size


def build_retopology_input_bundle(
    staging: Path,
    bundle_root: Path,
    project_filename: str,
    input_manifest: dict[str, Any],
    reference_names: list[str],
) -> tuple[Path, str, int]:
    """Build and hash the immutable retopology bundle outside admission."""

    manifest_path = bundle_root / "input_manifest.json"
    manifest_path.write_text(
        json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    publish_root = staging / "publish"
    publish_root.mkdir(parents=False, exist_ok=False)
    bundle = publish_root / "retopology_input.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(bundle_root / project_filename, project_filename)
        archive.write(manifest_path, "input_manifest.json")
        for filename in reference_names:
            archive.write(bundle_root / "references" / filename, f"references/{filename}")
    bundle_sha = sha256_path(bundle)
    bundle_size = bundle.stat().st_size
    shutil.rmtree(bundle_root)
    return publish_root, bundle_sha, bundle_size


def is_substance_worker_id(worker_id: str | None) -> bool:
    return bool(
        worker_id
        and (worker_id == SUBSTANCE_WORKER_ID or worker_id.startswith(SUBSTANCE_WORKER_ID_PREFIX))
    )


def substance_process_counts_consistent(
    *,
    reported_worker_jobs: int,
    durable_worker_jobs: int,
    active_host_processes: int | None,
    durable_host_jobs: int,
) -> bool:
    """Reject stale local counters and unowned native Baker processes.

    ``substance_active_processes`` is deliberately host-wide, while
    ``current_jobs`` is scoped to one stable Worker ID.  Consequently an idle
    sibling may legitimately observe a process owned by another Worker.  The
    two unsafe contradictions are narrower: this Worker disagrees with its
    own durable lease count, or the host reports more native processes than
    all live durable Substance leases can account for.
    """

    return (
        reported_worker_jobs == durable_worker_jobs
        and active_host_processes is not None
        and active_host_processes <= durable_host_jobs
    )


async def production_gpu_work_active(db: AsyncSession) -> bool:
    """Return whether real GPU work must take precedence over load-test Baker work."""
    terminal_jobs = [status.value for status in TERMINAL_JOB_STATUSES]
    active_job = await db.scalar(
        select(Job.id)
        .outerjoin(ApiClient, ApiClient.id == Job.tenant_id)
        .where(
            or_(ApiClient.id.is_(None), ApiClient.client_kind != "test"),
            Job.status.not_in(terminal_jobs),
        )
        .limit(1)
    )
    if active_job is not None:
        return True
    terminal_batches = [status.value for status in TERMINAL_BATCH_STATUSES]
    active_batch = await db.scalar(
        select(JobBatch.id)
        .outerjoin(ApiClient, ApiClient.id == JobBatch.tenant_id)
        .where(
            or_(ApiClient.id.is_(None), ApiClient.client_kind != "test"),
            JobBatch.status.not_in(terminal_batches),
        )
        .limit(1)
    )
    return active_batch is not None


def expire_substance_pending_reservation(node: Node, now: datetime) -> None:
    """Remove an expired pending reservation without touching an active fence."""
    labels = dict(node.labels or {})
    pending_ids, _ = substance_pending_reservation(labels, now)
    if labels.get(SUBSTANCE_PENDING_RESERVATION_LABEL) is not None and not pending_ids:
        labels.pop(SUBSTANCE_PENDING_RESERVATION_LABEL, None)
    fenced_job_ids = substance_fence_job_ids(labels)
    recovery_required = bool(labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL))
    if not fenced_job_ids and not pending_ids and not recovery_required:
        owned = labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
        if owned:
            labels.pop(SUBSTANCE_DRAIN_OWNER_LABEL, None)
        labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
        labels.pop(SUBSTANCE_FENCE_LABEL, None)
        if owned and node.mode == "DRAINING" and not node.manual_reserved:
            node.mode = "ACTIVE"
    node.labels = labels


def ensure_substance_owned_drain(node: Node, labels: dict[str, Any]) -> bool:
    """Acquire/retain only an Asset API-owned drain without stealing mode ownership."""
    if node.mode == "ACTIVE" and not node.manual_reserved:
        node.mode = "DRAINING"
        labels[SUBSTANCE_DRAIN_OWNER_LABEL] = SUBSTANCE_DRAIN_OWNER
        return True
    return (
        node.mode == "DRAINING" and labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
    )


async def reconcile_substance_gpu_reservation(
    db: AsyncSession,
    node: Node,
    reservation_seconds: int,
    worker_heartbeat_timeout_seconds: int,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Reserve the next four production bakes while holding the GPU node lock."""
    current = now or datetime.now(UTC)
    expire_substance_pending_reservation(node, current)
    labels = dict(node.labels or {})
    if labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL):
        # A timed-out native Baker is an ambiguous execution, not idle
        # capacity. Keep the physical node fail-closed until the same worker
        # reports idle and the Scheduler has observed ComfyUI ONLINE again.
        labels.pop(SUBSTANCE_PENDING_RESERVATION_LABEL, None)
        ensure_substance_owned_drain(node, labels)
        node.labels = labels
        return []
    fenced_job_ids = substance_fence_job_ids(labels)
    available = max(0, SUBSTANCE_MAX_PARALLEL - len(fenced_job_ids))
    pending_job_ids: list[str] = []
    fresh_bakers = list(
        (
            await db.scalars(
                select(AssetWorker)
                .where(
                    AssetWorker.id.like(f"{SUBSTANCE_WORKER_ID}%"),
                    AssetWorker.status == "ONLINE",
                    AssetWorker.last_heartbeat_at
                    >= current - timedelta(seconds=worker_heartbeat_timeout_seconds),
                    AssetWorker.current_jobs < AssetWorker.max_concurrency,
                )
                .order_by(AssetWorker.id)
            )
        ).all()
    )
    fresh_baker_slots = sum(
        max(0, worker.max_concurrency - worker.current_jobs) for worker in fresh_bakers
    )
    reservation_capacity = min(available, fresh_baker_slots)
    if reservation_capacity:
        pending_job_ids = list(
            (
                await db.scalars(
                    select(AssetJob.id)
                    .join(ApiClient, ApiClient.id == AssetJob.client_id)
                    .where(
                        AssetJob.job_type == "SUBSTANCE_BAKE_V1",
                        AssetJob.status == "QUEUED",
                        AssetJob.cancel_requested.is_(False),
                        ApiClient.client_kind != "test",
                    )
                    .order_by(AssetJob.created_at, AssetJob.id)
                    .limit(reservation_capacity)
                    .with_for_update(skip_locked=True, of=AssetJob)
                )
            ).all()
        )
    if pending_job_ids and ensure_substance_owned_drain(node, labels):
        labels[SUBSTANCE_PENDING_RESERVATION_LABEL] = {
            "job_ids": pending_job_ids,
            "worker_ids": [worker.id for worker in fresh_bakers][: len(pending_job_ids)],
            "expires_at": (current + timedelta(seconds=reservation_seconds)).isoformat(),
            "max_parallel": SUBSTANCE_MAX_PARALLEL,
        }
    else:
        pending_job_ids = []
        labels.pop(SUBSTANCE_PENDING_RESERVATION_LABEL, None)

    if fenced_job_ids:
        # The fence is a hard scheduling interlock, but it is not proof that
        # Asset API owns an administrative DISABLED/RESERVED/non-owner drain.
        ensure_substance_owned_drain(node, labels)
    elif not pending_job_ids:
        owned = labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
        if owned:
            labels.pop(SUBSTANCE_DRAIN_OWNER_LABEL, None)
        labels.pop(SUBSTANCE_FENCE_LABEL, None)
        labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
        if owned and node.mode == "DRAINING" and not node.manual_reserved:
            node.mode = "ACTIVE"
    node.labels = labels
    return pending_job_ids


def substance_recovery_entries(labels: dict[str, Any]) -> list[dict[str, Any]]:
    raw = labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL, [])
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("job_id") or not item.get("worker_id"):
            continue
        entries.append(
            {
                "job_id": str(item["job_id"]),
                "worker_id": str(item["worker_id"]),
                "worker_instance_id": str(item.get("worker_instance_id", "")),
                "lease_expired_at": str(item.get("lease_expired_at", "")),
                "idle_observed_at": str(item.get("idle_observed_at", "")),
                "idle_observed_agent_instance_id": str(
                    item.get("idle_observed_agent_instance_id", "")
                ),
                "process_probe_checked_at": str(item.get("process_probe_checked_at", "")),
            }
        )
    return entries


async def mark_substance_gpu_recovery_required(
    db: AsyncSession,
    job: AssetJob,
    worker_id: str,
    now: datetime,
    *,
    locked_node: Node | None = None,
) -> None:
    """Fail closed after an ambiguous native Baker lease expiry."""
    node = locked_node
    if node is None:
        node = await db.scalar(
            select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
        )
    if node is None:
        return
    labels = dict(node.labels or {})
    fenced_job_ids = [
        fenced_job_id
        for fenced_job_id in substance_fence_job_ids(labels)
        if fenced_job_id != job.id
    ]
    if fenced_job_ids:
        labels[SUBSTANCE_FENCE_LABEL] = fenced_job_ids
    else:
        labels.pop(SUBSTANCE_FENCE_LABEL, None)
    labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
    labels.pop(SUBSTANCE_PENDING_RESERVATION_LABEL, None)
    entries = [entry for entry in substance_recovery_entries(labels) if entry["job_id"] != job.id]
    entries.append(
        {
            "job_id": job.id,
            "worker_id": worker_id,
            "worker_instance_id": str(job.worker_instance_id or ""),
            "lease_expired_at": now.isoformat(),
        }
    )
    labels[SUBSTANCE_RECOVERY_REQUIRED_LABEL] = entries
    ensure_substance_owned_drain(node, labels)
    node.labels = labels


async def confirm_substance_gpu_recovery(
    db: AsyncSession,
    worker: AssetWorker,
    reported_current_jobs: int,
    reported_agent_instance_id: str | None,
    process_probe_status: str,
    process_probe_checked_at: datetime | None,
    active_baker_processes: int | None,
    reservation_seconds: int,
    node_heartbeat_timeout_seconds: int,
    worker_heartbeat_timeout_seconds: int,
    *,
    locked_node: Node | None = None,
) -> bool:
    """Release an expiry drain after host-process and ComfyUI evidence.

    Recovery deliberately uses a two-phase observation.  The first healthy
    host-wide zero-process probe is persisted as the recovery barrier.  A
    later Scheduler heartbeat must then prove that ComfyUI is still healthy
    and idle *after* that barrier; a subsequent current zero-process probe
    triggers release.  This ordering prevents a pre-recovery ComfyUI heartbeat
    from unlocking the GPU after an orphan Baker exits.  A restarted Agent's
    in-memory ``current_jobs`` counter is never sufficient.
    """
    node = locked_node
    if node is None:
        node = await db.scalar(
            select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
        )
    if node is None:
        return False
    labels = dict(node.labels or {})
    entries = substance_recovery_entries(labels)
    matching = [entry for entry in entries if entry["worker_id"] == worker.id]
    if not matching:
        return False
    try:
        latest_lease_expiry = max(
            as_utc(datetime.fromisoformat(entry["lease_expired_at"].replace("Z", "+00:00")))
            for entry in matching
        )
    except (ValueError, TypeError):
        return False
    if (
        reported_current_jobs != 0
        or worker.status != "ONLINE"
        or not reported_agent_instance_id
        or worker.agent_instance_id != reported_agent_instance_id
        or process_probe_status != "HEALTHY"
        or process_probe_checked_at is None
        or active_baker_processes != 0
    ):
        return False
    observation_time = datetime.now(UTC)
    process_observation = as_utc(process_probe_checked_at)
    if (
        observation_time < latest_lease_expiry
        or process_observation < latest_lease_expiry
        or process_observation > observation_time + timedelta(seconds=30)
        or (observation_time - process_observation).total_seconds()
        > worker_heartbeat_timeout_seconds
    ):
        return False
    # Preserve the first valid zero-process observation instead of replacing
    # it on every Agent heartbeat.  Replacing it would continually move the
    # barrier ahead of Scheduler and make the two-phase handshake impossible.
    persisted_idle_observations: list[datetime] = []
    persisted_process_observations: list[datetime] = []
    for entry in matching:
        try:
            idle_observed_at = as_utc(
                datetime.fromisoformat(entry["idle_observed_at"].replace("Z", "+00:00"))
            )
            persisted_process_observation = as_utc(
                datetime.fromisoformat(entry["process_probe_checked_at"].replace("Z", "+00:00"))
            )
        except (ValueError, TypeError):
            continue
        if (
            idle_observed_at >= latest_lease_expiry
            and idle_observed_at <= observation_time
            and persisted_process_observation >= latest_lease_expiry
            and persisted_process_observation <= idle_observed_at + timedelta(seconds=30)
        ):
            persisted_idle_observations.append(idle_observed_at)
            persisted_process_observations.append(persisted_process_observation)
    if not persisted_idle_observations:
        for entry in entries:
            if entry["worker_id"] == worker.id:
                entry["idle_observed_at"] = observation_time.isoformat()
                entry["idle_observed_agent_instance_id"] = reported_agent_instance_id
                entry["process_probe_checked_at"] = process_observation.isoformat()
        recovery_observed_after = observation_time
    else:
        recovery_observed_after = max(
            *persisted_idle_observations,
            *persisted_process_observations,
        )
    labels[SUBSTANCE_RECOVERY_REQUIRED_LABEL] = entries
    ensure_substance_owned_drain(node, labels)
    node.labels = labels
    node_heartbeat = as_utc(node.last_heartbeat_at) if node.last_heartbeat_at is not None else None
    if (
        node.health != "ONLINE"
        or node_heartbeat is None
        or node_heartbeat < latest_lease_expiry
        or node_heartbeat < recovery_observed_after
        or (observation_time - node_heartbeat).total_seconds() > node_heartbeat_timeout_seconds
        or node.current_jobs != 0
        or node.external_busy
        or node.foreign_queue_detected
    ):
        return False
    remaining = [entry for entry in entries if entry["worker_id"] != worker.id]
    if remaining:
        labels[SUBSTANCE_RECOVERY_REQUIRED_LABEL] = remaining
        ensure_substance_owned_drain(node, labels)
        node.labels = labels
        return True
    labels.pop(SUBSTANCE_RECOVERY_REQUIRED_LABEL, None)
    node.labels = labels
    await reconcile_substance_gpu_reservation(
        db,
        node,
        reservation_seconds,
        worker_heartbeat_timeout_seconds,
    )
    return True


async def release_substance_gpu_fence(
    db: AsyncSession,
    job: AssetJob,
    reservation_seconds: int,
    worker_heartbeat_timeout_seconds: int,
) -> None:
    """Release one Baker fence and atomically reserve the next production work."""
    if job.job_type != "SUBSTANCE_BAKE_V1":
        return
    node = await db.scalar(select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update())
    if node is None:
        return
    labels = dict(node.labels or {})
    fenced_job_ids = substance_fence_job_ids(labels)
    fenced_job_ids = [job_id for job_id in fenced_job_ids if job_id != job.id]
    if fenced_job_ids:
        labels[SUBSTANCE_FENCE_LABEL] = fenced_job_ids
    else:
        labels.pop(SUBSTANCE_FENCE_LABEL, None)
    labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
    pending = labels.get(SUBSTANCE_PENDING_RESERVATION_LABEL)
    if isinstance(pending, dict) and isinstance(pending.get("job_ids"), list):
        pending["job_ids"] = [str(job_id) for job_id in pending["job_ids"] if str(job_id) != job.id]
        labels[SUBSTANCE_PENDING_RESERVATION_LABEL] = pending
    node.labels = labels
    await reconcile_substance_gpu_reservation(
        db,
        node,
        reservation_seconds,
        worker_heartbeat_timeout_seconds,
    )


class Principal(BaseModel):
    id: str
    client_kind: str


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
    agent_instance_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$", max_length=32)
    agent_started_at: datetime | None = None
    substance_process_probe_status: str = Field(default="NOT_RUN", max_length=24)
    substance_process_probe_checked_at: datetime | None = None
    substance_active_processes: int | None = Field(default=None, ge=0, le=64)
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
    node_id: str | None = Field(
        default=None,
        pattern=r"^(?:control|worker)-[a-z0-9-]+$",
        max_length=64,
    )
    agent_instance_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$", max_length=32)
    load_1m: float = Field(ge=0, le=4096)
    available_memory_mb: int = Field(ge=0)


class WorkerProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress: float = Field(ge=0, le=99.9)
    # Workers may temporarily report a newer descriptive stage name before
    # every node has rolled to the shortened DB-safe spelling. Known aliases
    # are canonicalized below; unknown values still fail closed.
    stage: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$", max_length=64)
    message: str = Field(min_length=1, max_length=500)
    estimated_remaining_seconds: int | None = Field(default=None, ge=0, le=604800)


WORKER_PROGRESS_STAGE_ALIASES = {
    "RETOPOLOGY_DIRECT_V2_INPUT_NORMALIZATION": "RETOPOLOGY_V2_INPUT_IMPORT",
}


def canonical_worker_progress_stage(stage: str) -> str:
    canonical = WORKER_PROGRESS_STAGE_ALIASES.get(stage, stage)
    if len(canonical) > 32:
        raise HTTPException(
            422,
            detail={
                "code": "ASSET_PROGRESS_STAGE_UNSUPPORTED",
                "stage": stage,
            },
        )
    return canonical


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
    # Worker requests use a short-lived HMAC timestamp, but the timestamp by
    # itself does not make a signed recovery heartbeat single-use.  Keep a
    # process-local replay window, protected by a lock so concurrent copies of
    # the same signed request cannot both pass the check.  Deployments run one
    # Asset API process; a future multi-process topology should move this
    # bounded cache to Redis without changing the wire contract.
    app.state.asset_worker_nonces = {}
    app.state.asset_worker_nonce_lock = asyncio.Lock()

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
            matches = [
                candidate for candidate in clients if source_ip in (candidate.allowed_ips or [])
            ]
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
        return Principal(id=client.id, client_kind=client.client_kind)

    async def verify_worker(request: Request, raw_body: bytes, worker_id: str) -> None:
        timestamp = request.headers.get("x-asset-timestamp", "")
        nonce = request.headers.get("x-asset-nonce", "")
        signature = request.headers.get("x-asset-signature", "")
        try:
            stamp = int(timestamp)
        except ValueError as exc:
            raise HTTPException(401, detail={"code": "WORKER_AUTH_FAILED"}) from exc
        now = int(time.time())
        if abs(now - stamp) > 30 or not nonce or len(nonce) > 128:
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
        nonces: dict[str, int] = request.app.state.asset_worker_nonces
        nonce_lock: asyncio.Lock = request.app.state.asset_worker_nonce_lock
        replay_key = f"{worker_id}:{nonce}"
        async with nonce_lock:
            for key, seen_at in list(nonces.items()):
                if now - seen_at > 60:
                    del nonces[key]
            if replay_key in nonces:
                raise HTTPException(409, detail={"code": "ASSET_WORKER_REQUEST_REPLAY"})
            nonces[replay_key] = now

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
        # A prior attempt's error remains in the immutable event history, but
        # must never be exposed as the current outcome after the job has been
        # re-queued or claimed again.  External clients otherwise render a
        # contradictory "RUNNING + failed" state.
        if job.status not in TERMINAL_ASSET_WORK_STATUSES or not job.error_code:
            return None
        messages = {
            "UV_QA_FAILED": (
                "自动展 UV 未通过严格交付 QA；系统已保留输入与诊断，"
                "不会发布不合格结果。请联系服务端管理员重试或修复。"
            ),
            "RETOPOLOGY_AUDIT_FAILED": (
                "自动重拓扑候选未通过严格交付 QA；诊断制品已保留，未发布为最终结果。"
            ),
            "RETOPOLOGY_QUALITY_GATE_FAILED": (
                "自动重拓扑候选未通过严格交付 QA；诊断制品已保留，未发布为最终结果。"
            ),
            "BLENDER_EXECUTION_FAILED": (
                "Blender 资产处理执行失败；系统已保留任务诊断，请联系服务端管理员处理后重试。"
            ),
            "SUBSTANCE_EXECUTION_FAILED": (
                "Substance 3D Baker 执行失败；系统已保留输入和原生 Windows 日志，未发布不完整贴图。"
            ),
            "SUBSTANCE_RESULT_INVALID": (
                "Substance 3D Baker 输出未通过完整性校验，未发布为最终贴图。"
            ),
            "SUBSTANCE_LEASE_EXPIRED_RECOVERY_REQUIRED": (
                "Substance 3D Baker 租约失效；为防止重复执行，3090-B 已保持恢复闭锁，"
                "等待宿主 Baker 进程为零与 ComfyUI 恢复证据。"
            ),
            "SUBSTANCE_COMFYUI_CONTINUITY_FAILED": (
                "烘焙期间 ComfyUI 进程身份或健康状态发生变化；3090-B 已保持恢复闭锁，"
                "未发布本次结果。"
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
                details=details or {},
            )
        )

    async def asset_slot_snapshot(
        db: AsyncSession,
        now: datetime,
        *,
        substance: bool,
        job_type: str | None = None,
        client_kind: str = "production",
    ) -> dict[str, int]:
        """Use the same resource gates for capacity, ETA, and actual claim."""

        heartbeat_cutoff = now - timedelta(
            seconds=cfg.asset_worker_heartbeat_timeout_seconds
        )
        worker_filter = (
            (AssetWorker.id == SUBSTANCE_WORKER_ID)
            | AssetWorker.id.startswith(SUBSTANCE_WORKER_ID_PREFIX)
            if substance
            else (
                (AssetWorker.id != SUBSTANCE_WORKER_ID)
                & ~AssetWorker.id.startswith(SUBSTANCE_WORKER_ID_PREFIX)
            )
        )
        online_workers = list(
            (
                await db.scalars(
                    select(AssetWorker).where(
                        AssetWorker.status == "ONLINE",
                        AssetWorker.last_heartbeat_at >= heartbeat_cutoff,
                        worker_filter,
                    )
                )
            ).all()
        )
        eligible_workers: list[AssetWorker] = []
        physical_total_limit: int | None = None
        physical_available_limit: int | None = None
        physical_used_floor = 0
        if substance:
            node = await db.get(Node, SUBSTANCE_GPU_NODE_ID)
            labels = dict(node.labels or {}) if node is not None else {}
            fenced_job_ids = substance_fence_job_ids(labels)
            pending_ids, _ = substance_pending_reservation(labels, now)
            owned_drain = bool(
                node is not None
                and node.mode == "DRAINING"
                and labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
            )
            pending_owned = bool(pending_ids) and owned_drain
            physical_available = bool(
                node is not None
                and (
                    node.mode == "ACTIVE"
                    or (owned_drain and (fenced_job_ids or pending_owned))
                )
                and node.health == "ONLINE"
                and node.current_jobs == 0
                and not labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL)
                and not node.manual_reserved
                and not node.external_busy
                and not node.foreign_queue_detected
            )
            if physical_available and not (
                client_kind == "test" and await production_gpu_work_active(db)
            ):
                eligible_workers = [
                    worker
                    for worker in online_workers
                    if worker.node_id == SUBSTANCE_GPU_NODE_ID
                    and worker.agent_instance_id is not None
                    and worker.agent_started_at is not None
                ]
                physical_total_limit = SUBSTANCE_MAX_PARALLEL
                physical_available_limit = max(
                    0, SUBSTANCE_MAX_PARALLEL - len(fenced_job_ids)
                )
                physical_used_floor = len(fenced_job_ids)
        else:
            node_ids = {worker.node_id for worker in online_workers if worker.node_id}
            nodes = (
                list((await db.scalars(select(Node).where(Node.id.in_(node_ids)))).all())
                if node_ids
                else []
            )
            nodes_by_id = {node.id: node for node in nodes}
            eligible_workers = [
                worker
                for worker in online_workers
                if worker.node_id in nodes_by_id
                and worker.agent_instance_id is not None
                and worker.agent_started_at is not None
                and linux_asset_claim_allowed(nodes_by_id[worker.node_id], now)
            ]
            if job_type in CODEX_REQUIRED_JOB_TYPES:
                codex_probe_cutoff = now - timedelta(
                    seconds=cfg.asset_codex_probe_max_age_seconds
                )
                eligible_workers = [
                    worker
                    for worker in eligible_workers
                    if worker.codex_auth_status == "AUTHENTICATED"
                    and worker.codex_probe_status == "HEALTHY"
                    and worker.codex_last_checked_at is not None
                    and as_utc(worker.codex_last_checked_at) >= codex_probe_cutoff
                ]
        total_slots = sum(worker.max_concurrency for worker in eligible_workers)
        used_slots = sum(worker.current_jobs for worker in eligible_workers)
        available_slots = sum(
            max(0, worker.max_concurrency - worker.current_jobs)
            for worker in eligible_workers
        )
        if physical_total_limit is not None and physical_available_limit is not None:
            total_slots = min(total_slots, physical_total_limit)
            used_slots = min(max(used_slots, physical_used_floor), total_slots)
            available_slots = min(
                available_slots,
                physical_available_limit,
                max(0, total_slots - used_slots),
            )
        return {
            "online_workers": len(online_workers),
            "schedulable_workers": len(eligible_workers),
            "total_slots": total_slots,
            "used_slots": used_slots,
            "available_slots": available_slots,
        }

    async def queue_timing(job: AssetJob, db: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)
        terminal_at = job.finished_at or job.last_progress_at
        timing_end = (
            as_utc(terminal_at)
            if job.status in TERMINAL_ASSET_WORK_STATUSES and terminal_at is not None
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
            if job.job_type == "SUBSTANCE_BAKE_V1":
                queue_query = queue_query.where(AssetJob.job_type == "SUBSTANCE_BAKE_V1")
            else:
                queue_query = queue_query.where(AssetJob.job_type != "SUBSTANCE_BAKE_V1")
            queue_position = int(await db.scalar(queue_query) or 1)
            slot_snapshot = await asset_slot_snapshot(
                db,
                now,
                substance=job.job_type == "SUBSTANCE_BAKE_V1",
                job_type=job.job_type,
                client_kind=str(
                    await db.scalar(
                        select(ApiClient.client_kind).where(ApiClient.id == job.client_id)
                    )
                    or "production"
                ),
            )
            slots = slot_snapshot["available_slots"]
            typical_seconds = {
                "UV_UNWRAP": 180,
                "UV_PROCESS_V2": 240,
                "RETOPOLOGY_AUDIT": 120,
                "RETOPOLOGY_PROCESS_V1": 900,
                "RETOPOLOGY_PROCESS_V2": 1800,
                "SUBSTANCE_BAKE_V1": 600,
            }.get(job.job_type, 300)
            if slots > 0:
                estimated_start_seconds = (
                    0
                    if queue_position <= slots
                    else ((queue_position - 1) // slots) * typical_seconds
                )
        return {
            "queue_position": queue_position,
            "estimated_start_seconds": estimated_start_seconds,
            "elapsed_seconds": elapsed,
            "estimated_remaining_seconds": job.estimated_remaining_seconds,
            "last_progress_at": job.last_progress_at.isoformat() if job.last_progress_at else None,
        }

    def artifacts_are_downloadable(job: AssetJob) -> bool:
        # V6 candidates that fail any formal gate are retained server-side for
        # operators, but they are never part of the public delivery contract.
        # V5 diagnostic downloads remain unchanged for rollback compatibility.
        if job.job_type == "RETOPOLOGY_PROCESS_V2" and job.status == "FAILED":
            return False
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
                "isolated_diagnostic"
                if job.job_type == "RETOPOLOGY_PROCESS_V2" and job.status == "FAILED"
                else
                "diagnostic"
                if job.status == "FAILED" and job.error_code in RETOPOLOGY_DIAGNOSTIC_ERROR_CODES
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

    async def lock_substance_node_for_job(job_id: str, db: AsyncSession) -> str | None:
        """Establish the global Node -> AssetJob lock order for Baker mutations."""
        job_type = await db.scalar(select(AssetJob.job_type).where(AssetJob.id == job_id))
        if job_type == "SUBSTANCE_BAKE_V1":
            await db.scalar(select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update())
        return job_type

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

    async def lock_asset_admission(
        request: Request,
        principal: Principal,
        db: AsyncSession,
    ) -> None:
        """Establish the shared global -> tenant lock order for new work."""

        await request.app.state.db.acquire_global_admission_transaction_lock(db)
        await request.app.state.db.acquire_tenant_transaction_lock(db, principal.id)

    async def enforce_new_asset_admission(
        principal: Principal,
        db: AsyncSession,
    ) -> None:
        """Pause only new test work while any production work is non-terminal."""

        if not await client_is_load_test(
            db, principal.id
        ) or not await active_production_work_exists(db):
            return
        raise HTTPException(
            503,
            detail={
                "code": "LOAD_TEST_PREEMPTED",
                "message": "真实生产任务已进入系统，新的压力测试任务已暂停接收",
                "retryable": True,
            },
            headers={"Retry-After": "5"},
        )

    async def replay_or_expire_asset_idempotency(
        principal: Principal,
        db: AsyncSession,
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> JSONResponse | None:
        """Replay a live key or remove an expired key under admission locks."""

        existing = await db.scalar(
            select(AssetIdempotencyKey)
            .where(
                AssetIdempotencyKey.client_id == principal.id,
                AssetIdempotencyKey.key == idempotency_key,
            )
            .with_for_update()
        )
        if existing is None:
            return None
        if as_utc(existing.expires_at) <= datetime.now(UTC):
            await db.delete(existing)
            await db.flush()
            return None
        if existing.request_hash != request_hash:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        old_job = await db.get(AssetJob, existing.job_id)
        if old_job is None:
            raise HTTPException(500, detail={"code": "ASSET_JOB_NOT_FOUND"})
        return JSONResponse(await job_payload(old_job, db), 200)

    async def cleanup_uncommitted_asset_root(
        request: Request,
        job_id: str | None,
        job_root: Path | None,
    ) -> None:
        """Remove an unpublished input tree only when the DB proves it is orphaned.

        A database commit may become durable even when its acknowledgement is
        lost to the API process.  In that case deleting ``job_root`` would leave
        an authoritative AssetJob pointing at missing input.  Fail closed on
        database uncertainty and leave the directory for the audited orphan
        sweeper instead.
        """

        if job_id is None or job_root is None or not job_root.exists():
            return
        try:
            async with request.app.state.db.session() as cleanup_db:
                persisted = await cleanup_db.get(AssetJob, job_id)
        except Exception:
            return
        if persisted is None and job_root.exists():
            await asyncio.to_thread(shutil.rmtree, job_root)

    async def cleanup_uncommitted_completion(
        request: Request,
        staging: Path,
        artifacts: list[AssetArtifact],
    ) -> None:
        """Delete staged outputs only when no durable artifact row references them."""

        if not staging.exists():
            return
        artifact_ids = [artifact.id for artifact in artifacts]
        persisted = False
        try:
            if artifact_ids:
                async with request.app.state.db.session() as cleanup_db:
                    persisted = (
                        await cleanup_db.scalar(
                            select(AssetArtifact.id)
                            .where(AssetArtifact.id.in_(artifact_ids))
                            .limit(1)
                        )
                    ) is not None
        except Exception:
            # A successful commit can lose its acknowledgement.  Database
            # uncertainty is therefore a preserve condition, never a delete
            # condition; a later orphan sweep can prove and reclaim the tree.
            return
        if not persisted and staging.exists():
            await asyncio.to_thread(shutil.rmtree, staging)

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
        job_id: str | None = None
        job_root: Path | None = None
        committed = False
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
            await lock_asset_admission(request, principal, db)
            replay = await replay_or_expire_asset_idempotency(
                principal,
                db,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            await enforce_new_asset_admission(principal, db)
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
            committed = True
            return JSONResponse(await job_payload(job, db), 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                await asyncio.to_thread(shutil.rmtree, staging)
            if not committed:
                await cleanup_uncommitted_asset_root(request, job_id, job_root)

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
            high_name = validate_baker_filename(high_mesh.filename or "") if high_mesh else None
            cage_name = validate_baker_filename(cage_mesh.filename or "") if cage_mesh else None
            if (
                parsed.options.profile in {"normal-dx-v1", "pbr-core-v1", "li3d-pbr-full-v2"}
                and not high_mesh
            ):
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
        input_root = staging / "workspace" / "input"
        input_root.mkdir(parents=True, exist_ok=False)
        job_id: str | None = None
        job_root: Path | None = None
        committed = False
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
            request_document = {
                "schema_version": 1,
                "job_type": "SUBSTANCE_BAKE_V1",
                "external_asset_id": parsed.external_asset_id,
                "options": parsed.options.model_dump(mode="json"),
                "files": bundle_files,
                "input_sha256": input_sha,
            }
            publish_root, bundle_sha, bundle_size = await asyncio.to_thread(
                build_substance_input_bundle,
                staging,
                input_root,
                request_document,
                bundle_files,
            )

            await lock_asset_admission(request, principal, db)
            replay = await replay_or_expire_asset_idempotency(
                principal,
                db,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            await enforce_new_asset_admission(principal, db)
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
            publish_root.rename(job_root)
            bundle_name = "substance_bake_input.zip"
            options = parsed.options.model_dump(mode="json")
            options["files"] = bundle_files
            options["input_sha256"] = input_sha
            job = AssetJob(
                id=job_id,
                client_id=principal.id,
                external_asset_id=parsed.external_asset_id,
                job_type="SUBSTANCE_BAKE_V1",
                status="QUEUED",
                source_filename=bundle_name,
                input_path=str(job_root / bundle_name),
                input_sha256=bundle_sha,
                input_size_bytes=bundle_size,
                options=options,
                request_hash=request_hash,
                request_id=str(request.state.request_id),
            )
            client = await db.get(ApiClient, principal.id)
            substance_node: Node | None = None
            if client is None or client.client_kind != "test":
                # Lock the physical node before publishing the queued job. The
                # Scheduler takes this same lock before a GPU assignment, so it
                # cannot slip another ComfyUI job between queueing the
                # production bake and installing its pending reservation.
                substance_node = await db.scalar(
                    select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
                )
            db.add(job)
            await db.flush()
            if substance_node is not None:
                await reconcile_substance_gpu_reservation(
                    db,
                    substance_node,
                    cfg.substance_pending_reservation_seconds,
                    cfg.asset_worker_heartbeat_timeout_seconds,
                )
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
            committed = True
            return JSONResponse(await job_payload(job, db), 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                await asyncio.to_thread(shutil.rmtree, staging)
            if not committed:
                await cleanup_uncommitted_asset_root(request, job_id, job_root)

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
            request_hash_builder=lambda input_sha: retopology_audit_request_hash(parsed, input_sha),
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
        """Create one V6 high-only automatic retopology job."""
        try:
            parsed, compatibility_warnings = adapt_retopology_v6_metadata_json(metadata)
            project_filename = validate_asset_filename(project.filename or "")
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
        job_id: str | None = None
        job_root: Path | None = None
        committed = False
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

            request_hash = retopology_v6_process_request_hash(
                parsed, project_sha, reference_sha
            )
            reference_by_name = {
                item.filename: item.model_dump(mode="json") for item in parsed.reference_views
            }
            input_manifest = {
                "schema_version": "retopology_input.v6",
                "engine_contract": "retopology-v6",
                "api_version": parsed.api_version,
                "policy_sha256": RETOPOLOGY_V6_POLICY_SHA256,
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
                "deprecated_fields_ignored": compatibility_warnings,
            }
            publish_root, bundle_sha, bundle_size = await asyncio.to_thread(
                build_retopology_input_bundle,
                staging,
                bundle_root,
                project_filename,
                input_manifest,
                sorted(reference_sha),
            )

            await lock_asset_admission(request, principal, db)
            replay = await replay_or_expire_asset_idempotency(
                principal,
                db,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            await enforce_new_asset_admission(principal, db)
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
            publish_root.rename(job_root)
            options = parsed.options.model_dump(mode="json")
            options.update(
                {
                    "engine_contract": "retopology-v6",
                    "policy_version": "6.0.0",
                    "policy_sha256": RETOPOLOGY_V6_POLICY_SHA256,
                    "deprecated_fields_ignored": compatibility_warnings,
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
                job_type="RETOPOLOGY_PROCESS_V2",
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
                db,
                job,
                details={
                    "event": "asset.queued",
                    "request_id": job.request_id,
                    "engine_contract": "retopology-v6",
                    "policy_sha256": RETOPOLOGY_V6_POLICY_SHA256,
                    "warnings": compatibility_warnings,
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
            committed = True
            return JSONResponse(await job_payload(job, db), 202)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, detail={"code": "ASSET_CONFLICT"}) from exc
        finally:
            if staging.exists():
                await asyncio.to_thread(shutil.rmtree, staging)
            if not committed:
                await cleanup_uncommitted_asset_root(request, job_id, job_root)

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
            request_hash_builder=lambda input_sha: uv_process_request_hash(parsed, input_sha),
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
                        terminal = item.status in TERMINAL_ASSET_WORK_STATUSES
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
        owner_and_type = (
            await db.execute(
                select(AssetJob.client_id, AssetJob.job_type).where(AssetJob.id == job_id)
            )
        ).one_or_none()
        if owner_and_type is None or owner_and_type.client_id != principal.id:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        if owner_and_type.job_type == "SUBSTANCE_BAKE_V1":
            # Every Baker mutation uses Node -> AssetJob. This serializes
            # queued cancellation against claim/fence installation and keeps
            # a claimed lease from losing its physical-GPU fence.
            await db.scalar(select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update())
        job = await db.get(AssetJob, job_id, with_for_update=True)
        if job is None or job.client_id != principal.id:
            raise HTTPException(404, detail={"code": "ASSET_JOB_NOT_FOUND"})
        if job.status in TERMINAL_ASSET_WORK_STATUSES:
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
        if job.status == "CANCELLED":
            await release_substance_gpu_fence(
                db,
                job,
                cfg.substance_pending_reservation_seconds,
                cfg.asset_worker_heartbeat_timeout_seconds,
            )
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
        principal: Annotated[Principal, Depends(api_principal)],
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        cpu = await asset_slot_snapshot(
            db,
            now,
            substance=False,
            client_kind=principal.client_kind,
        )
        substance = await asset_slot_snapshot(
            db,
            now,
            substance=True,
            client_kind=principal.client_kind,
        )
        return {
            "schema_version": "1.0",
            "advisory": True,
            "online_workers": cpu["online_workers"] + substance["online_workers"],
            "schedulable_workers": (
                cpu["schedulable_workers"] + substance["schedulable_workers"]
            ),
            "total_slots": cpu["total_slots"] + substance["total_slots"],
            "used_slots": cpu["used_slots"] + substance["used_slots"],
            "available_slots": cpu["available_slots"] + substance["available_slots"],
            "resources": {"cpu": cpu, "substance": substance},
            "client": {"id": principal.id, "kind": principal.client_kind},
            "as_of": now.isoformat(),
        }

    @app.post("/internal/v1/assets/workers/heartbeat")
    async def worker_heartbeat(
        body: WorkerHeartbeat,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
    ) -> dict[str, Any]:
        await verify_worker(request, await request.body(), body.worker_id)
        heartbeat_at = datetime.now(UTC)
        body_started_at = (
            as_utc(body.agent_started_at) if body.agent_started_at is not None else None
        )
        if body_started_at is not None and body_started_at > heartbeat_at + timedelta(seconds=30):
            raise HTTPException(
                409,
                detail={"code": "ASSET_WORKER_GENERATION_TIME_INVALID"},
            )
        if is_substance_worker_id(body.worker_id) and body.node_id != SUBSTANCE_GPU_NODE_ID:
            raise HTTPException(
                409,
                detail={"code": "SUBSTANCE_WORKER_NODE_MISMATCH"},
            )
        substance_heartbeat_node: Node | None = None
        if is_substance_worker_id(body.worker_id):
            substance_heartbeat_node = await db.scalar(
                select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
            )
        worker = await db.get(AssetWorker, body.worker_id, with_for_update=True)
        # Worker-first prevents a concurrent old-generation claim from
        # appearing after the active-lease snapshot. Job locks are NOWAIT:
        # progress/completion/reaping may already own Job then need Worker, so
        # waiting here would create Worker <-> Job deadlocks. A busy heartbeat
        # is safely retried by the Worker loop without changing generation.
        try:
            durable_jobs = list(
                (
                    await db.scalars(
                        select(AssetJob)
                        .where(
                            AssetJob.worker_id == body.worker_id,
                            AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                        )
                        .order_by(AssetJob.id)
                        .with_for_update(nowait=True)
                    )
                ).all()
            )
        except DBAPIError as exc:
            if not is_postgres_lock_not_available(exc):
                raise
            await db.rollback()
            raise HTTPException(
                409,
                detail={"code": "ASSET_WORKER_HEARTBEAT_BUSY_RETRY"},
            ) from exc
        # Only a still-live durable lease may pin a Worker generation. An
        # expired lease is reconciled at the beginning of the next claim.
        durable_current_jobs = sum(
            1
            for job in durable_jobs
            if job.lease_expires_at is None
            or as_utc(job.lease_expires_at) >= heartbeat_at
        )
        durable_substance_host_jobs = durable_current_jobs
        if is_substance_worker_id(body.worker_id):
            # ``substance_active_processes`` is a host-wide Win32 probe.  The
            # physical Node lock serializes this snapshot with every Baker
            # claim/completion/failure, so an excess native-process count is
            # evidence of an orphan rather than usable capacity.
            host_jobs = list(
                (
                    await db.scalars(
                        select(AssetJob).where(
                            AssetJob.job_type == "SUBSTANCE_BAKE_V1",
                            AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                        )
                    )
                ).all()
            )
            durable_substance_host_jobs = sum(
                1
                for job in host_jobs
                if job.lease_expires_at is None
                or as_utc(job.lease_expires_at) >= heartbeat_at
            )
        if worker is not None:
            stored_started_at = (
                as_utc(worker.agent_started_at) if worker.agent_started_at is not None else None
            )
            generation_changed = worker.agent_instance_id != body.agent_instance_id
            node_changed = worker.node_id != body.node_id
            stale_or_legacy_generation = generation_changed and (
                body.agent_instance_id is None
                or body_started_at is None
                or (
                    stored_started_at is not None
                    and body_started_at <= stored_started_at
                )
            )
            if stale_or_legacy_generation or (
                durable_current_jobs > 0 and (generation_changed or node_changed)
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "ASSET_WORKER_GENERATION_CONFLICT",
                        "active_jobs": durable_current_jobs,
                    },
                )
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
        # A restarted process has an empty in-memory counter.  Never let that
        # overwrite durable leases still assigned to the stable worker_id.
        worker.current_jobs = max(body.current_jobs, durable_current_jobs)
        worker.agent_instance_id = body.agent_instance_id
        worker.agent_started_at = body.agent_started_at
        worker.substance_process_probe_status = body.substance_process_probe_status
        worker.substance_process_probe_checked_at = body.substance_process_probe_checked_at
        worker.substance_active_processes = body.substance_active_processes
        worker.codex_cli_version = body.codex_cli_version
        worker.codex_auth_status = body.codex_auth_status
        worker.codex_probe_status = body.codex_probe_status
        worker.codex_probe_latency_ms = body.codex_probe_latency_ms
        worker.codex_last_checked_at = body.codex_last_checked_at
        # A failed probe after a Worker restart may have no in-memory success
        # timestamp.  Preserve the durable historical success rather than
        # erasing useful recovery evidence with a null heartbeat field.
        if body.codex_last_success_at is not None:
            worker.codex_last_success_at = body.codex_last_success_at
        worker.codex_error_code = body.codex_error_code
        worker.retopoflow_version = body.retopoflow_version
        worker.retopoflow_revision = body.retopoflow_revision
        worker.retopoflow_probe_status = body.retopoflow_probe_status
        worker.retopoflow_probe_latency_ms = body.retopoflow_probe_latency_ms
        worker.retopoflow_last_checked_at = body.retopoflow_last_checked_at
        worker.retopoflow_error_code = body.retopoflow_error_code
        worker.last_heartbeat_at = heartbeat_at
        resource_ok = (
            body.available_memory_mb >= cfg.asset_worker_min_available_memory_mb
            and body.load_1m / body.cpu_count <= cfg.asset_worker_max_load_per_cpu
        )
        runtime_version_ok = (
            body.blender_version == SUBSTANCE_VERSION
            and body.skill_version == SUBSTANCE_SKILL_VERSION
            if is_substance_worker_id(body.worker_id)
            else body.blender_version == "5.1.2"
        )
        process_checked_at = (
            as_utc(body.substance_process_probe_checked_at)
            if body.substance_process_probe_checked_at is not None
            else None
        )
        agent_started_at = (
            as_utc(body.agent_started_at) if body.agent_started_at is not None else None
        )
        substance_process_evidence_ok = not is_substance_worker_id(body.worker_id) or (
            body.agent_instance_id is not None
            and agent_started_at is not None
            and process_checked_at is not None
            and body.substance_process_probe_status == "HEALTHY"
            and body.substance_active_processes is not None
            and agent_started_at <= process_checked_at
            and process_checked_at
            >= heartbeat_at - timedelta(seconds=cfg.asset_worker_heartbeat_timeout_seconds)
            and process_checked_at <= heartbeat_at + timedelta(seconds=30)
            and substance_process_counts_consistent(
                reported_worker_jobs=body.current_jobs,
                durable_worker_jobs=durable_current_jobs,
                active_host_processes=body.substance_active_processes,
                durable_host_jobs=durable_substance_host_jobs,
            )
        )
        generation_evidence_ok = (
            body.agent_instance_id is not None
            and body_started_at is not None
            and body_started_at <= heartbeat_at + timedelta(seconds=30)
        )
        worker.status = (
            "ONLINE"
            if runtime_version_ok
            and resource_ok
            and substance_process_evidence_ok
            and generation_evidence_ok
            else "DRAINING"
        )
        if is_substance_worker_id(worker.id):
            await confirm_substance_gpu_recovery(
                db,
                worker,
                body.current_jobs,
                body.agent_instance_id,
                body.substance_process_probe_status,
                body.substance_process_probe_checked_at,
                body.substance_active_processes,
                cfg.substance_pending_reservation_seconds,
                cfg.node_heartbeat_timeout_seconds,
                cfg.asset_worker_heartbeat_timeout_seconds,
                locked_node=substance_heartbeat_node,
            )
        await db.commit()
        return {"accepted": True, "status": worker.status}

    @app.post("/internal/v1/assets/jobs/claim")
    async def claim_job(
        body: WorkerClaim,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
    ) -> JSONResponse:
        await verify_worker(request, await request.body(), body.worker_id)
        substance_claim = is_substance_worker_id(body.worker_id)
        if not substance_claim and (body.node_id is None or body.agent_instance_id is None):
            return JSONResponse({"job": None}, 200)
        if substance_claim:
            # Serialize a Baker claim with every new-work admission before
            # taking the physical 3090-B row.  This closes the window where a
            # test Baker could bypass a production create already holding the
            # global admission lock while that create waited for the Node.
            await request.app.state.db.acquire_global_admission_transaction_lock(db)
        expiry_cutoff = datetime.now(UTC)
        substance_expiry_hint = await db.scalar(
            select(AssetJob.id)
            .where(
                AssetJob.job_type == "SUBSTANCE_BAKE_V1",
                AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                AssetJob.lease_expires_at < expiry_cutoff,
            )
            .limit(1)
        )
        substance_expiry_node: Node | None = None
        expired_substance: list[AssetJob] = []
        if substance_expiry_hint is not None:
            # The hint is read-only. The authoritative recheck is performed
            # only after taking the physical Node lock, then each AssetJob row
            # is locked in the globally consistent Node -> Job order.
            substance_expiry_node = await db.scalar(
                select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
            )
            expired_substance = list(
                (
                    await db.scalars(
                        select(AssetJob)
                        .where(
                            AssetJob.job_type == "SUBSTANCE_BAKE_V1",
                            AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                            AssetJob.lease_expires_at < expiry_cutoff,
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
        expired_non_substance = list(
            (
                await db.scalars(
                    select(AssetJob)
                    .where(
                        AssetJob.job_type != "SUBSTANCE_BAKE_V1",
                        AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                        AssetJob.lease_expires_at < expiry_cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        expired = [*expired_substance, *expired_non_substance]
        for stale in expired:
            expired_at = datetime.now(UTC)
            assigned_worker_id = stale.worker_id
            previous_worker_id = str(stale.worker_id or "unknown")
            substance_expired = stale.job_type == "SUBSTANCE_BAKE_V1"
            # ``stale`` is still locked and not visible as QUEUED here.  A
            # concurrent claim may already have incremented this Worker, so
            # release the old slot with in-database arithmetic rather than a
            # stale ORM snapshot assignment.
            await decrement_asset_worker_jobs_atomic(db, assigned_worker_id)
            if stale.cancel_requested:
                stale.status = "CANCELLED"
                stale.stage = "CANCELLED"
                stale.stage_message = (
                    "Substance Worker 租约失效；任务取消但 GPU 保持恢复闭锁"
                    if substance_expired
                    else "Worker 租约失效后确认取消"
                )
                stale.estimated_remaining_seconds = 0
                stale.finished_at = expired_at
            elif substance_expired:
                # A native Baker timeout cannot be retried safely: the old
                # process may still be rendering. Keep this attempt terminal
                # and require explicit recovery evidence before any new Baker
                # or ComfyUI assignment can use the physical 3090-B.
                stale.status = "FAILED"
                stale.stage = "RECOVERY_REQUIRED"
                stale.stage_message = (
                    "Substance Worker 租约失效；等待宿主进程探针与 ComfyUI 恢复确认"
                )
                stale.estimated_remaining_seconds = 0
                stale.error_code = "SUBSTANCE_LEASE_EXPIRED_RECOVERY_REQUIRED"
                stale.error_message = (
                    "native Baker lease expired; automatic retry is blocked until "
                    "a healthy host process probe reports zero native Bakers and "
                    "ComfyUI-online evidence is observed"
                )
                stale.finished_at = expired_at
            elif stale.attempt_count < cfg.asset_job_max_attempts:
                stale.status = "QUEUED"
                stale.stage = "RETRY_QUEUED"
                stale.stage_message = "Worker 租约失效，任务已安全返回队列"
                stale.estimated_remaining_seconds = None
                stale.worker_id = None
                stale.worker_instance_id = None
                stale.error_code = "ASSET_LEASE_EXPIRED"
                stale.error_message = "worker lease expired; job returned to the asset queue"
            else:
                stale.status = "FAILED"
                stale.stage = "FAILED"
                stale.stage_message = "Worker 多次失联，任务已终止"
                stale.estimated_remaining_seconds = 0
                stale.error_code = "ASSET_LEASE_EXPIRED"
                stale.error_message = "worker lease expired after maximum attempts"
                stale.finished_at = expired_at
            stale.lease_token_hash = None
            stale.lease_expires_at = None
            stale.last_progress_at = expired_at
            if substance_expired:
                await mark_substance_gpu_recovery_required(
                    db,
                    stale,
                    previous_worker_id,
                    expired_at,
                    locked_node=substance_expiry_node,
                )
            await append_asset_event(
                db,
                stale,
                details={
                    "event": "asset.lease_expired",
                    "recovery_required": substance_expired,
                    "automatic_retry_blocked": substance_expired,
                    "worker_instance_id": stale.worker_instance_id,
                },
            )
        if expired:
            # Lease reconciliation is an independent durable safety action.
            # Every validation branch below may legitimately return no job;
            # committing here prevents those early returns from rolling back a
            # FAILED/recovery-required transition and its physical-GPU drain.
            await db.commit()
            if substance_claim:
                # The reconciliation commit releases transaction-scoped
                # advisory locks.  Reacquire the global lock before the next
                # Node lock so the ordering remains global -> Node -> Job.
                await request.app.state.db.acquire_global_admission_transaction_lock(db)
        substance_node: Node | None = None
        worker_node: Node | None = None
        worker_node_id_hint: str | None = None
        if substance_claim:
            substance_node = await db.scalar(
                select(Node).where(Node.id == SUBSTANCE_GPU_NODE_ID).with_for_update()
            )
            worker_node = substance_node
        else:
            # The Worker heartbeat is allowed to refresh independently of the
            # GPU node heartbeat. CPU Asset claims intentionally ignore the
            # ComfyUI/GPU health signal, while still requiring the bound node to
            # be operator-schedulable (mode/reservation gates). Read the binding
            # as a hint, then lock Node -> Worker -> AssetJob and
            # revalidate the binding below so a concurrent heartbeat cannot
            # move the Worker between nodes inside the claim transaction.
            worker_node_id_hint = body.node_id
            worker_node = await db.scalar(
                select(Node).where(Node.id == worker_node_id_hint).with_for_update()
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
        if not substance_claim and (
            worker_node is None
            or worker.node_id != worker_node_id_hint
            or worker_node.id != worker.node_id
            or worker.agent_instance_id != body.agent_instance_id
            or not linux_asset_claim_allowed(worker_node, datetime.now(UTC))
        ):
            # The node binding and process generation are part of the claim
            # identity.  GPU current_jobs/utilisation/health remain independent
            # from Blender CPU capacity.
            return JSONResponse({"job": None}, 200)
        if is_substance_worker_id(worker.id) and (
            worker.node_id != SUBSTANCE_GPU_NODE_ID
            or body.agent_instance_id is None
            or body.agent_instance_id != worker.agent_instance_id
        ):
            # A restarted scheduled task must heartbeat its new generation
            # before it can claim.  This prevents an older process with the
            # same stable worker_id from borrowing the newer instance's slot.
            return JSONResponse({"job": None}, 200)
        if is_substance_worker_id(worker.id):
            existing_assignment = await db.scalar(
                select(AssetJob.id)
                .where(
                    AssetJob.worker_id == worker.id,
                    AssetJob.status.in_(["CLAIMED", "RUNNING", "CANCELLING"]),
                )
                .limit(1)
            )
            if existing_assignment is not None:
                # The durable assignment is authoritative even if a restarted
                # Agent reported current_jobs=0 or that counter was otherwise
                # stale.  One stable Worker ID may never own two live leases.
                return JSONResponse({"job": None}, 200)
        pending_substance_job_ids: list[str] = []
        if is_substance_worker_id(worker.id):
            # Lock the physical GPU node before the asset job row.  The GPU
            # scheduler uses the same lock order, preventing a ComfyUI claim
            # from racing a native Windows Baker claim.
            labels = dict(substance_node.labels or {}) if substance_node else {}
            fenced_job_ids = substance_fence_job_ids(labels)
            if substance_node is not None:
                pending_substance_job_ids = await reconcile_substance_gpu_reservation(
                    db,
                    substance_node,
                    cfg.substance_pending_reservation_seconds,
                    cfg.asset_worker_heartbeat_timeout_seconds,
                )
                labels = dict(substance_node.labels or {})
                fenced_job_ids = substance_fence_job_ids(labels)
            recovery_required = bool(labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL))
            pending_owned = bool(pending_substance_job_ids) and (
                labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
            )
            owned_drain = (
                substance_node is not None
                and substance_node.mode == "DRAINING"
                and labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) == SUBSTANCE_DRAIN_OWNER
            )
            if (
                substance_node is None
                or (
                    substance_node.mode != "ACTIVE"
                    and not (owned_drain and (fenced_job_ids or pending_owned))
                )
                or substance_node.health != "ONLINE"
                or substance_node.current_jobs != 0
                or recovery_required
                or len(fenced_job_ids) >= SUBSTANCE_MAX_PARALLEL
                or substance_node.manual_reserved
                or substance_node.external_busy
                or substance_node.foreign_queue_detected
            ):
                # Reservation reconciliation is a durable scheduling action.
                # In particular, a production bake waiting for the currently
                # running ComfyUI frame must keep 3090-B drained past the
                # initial reservation TTL.  Returning without a commit here
                # rolls the renewed expiry back and lets the GPU scheduler
                # assign another frame, starving the native Baker queue.
                await db.commit()
                return JSONResponse({"job": None}, 200)

        claim_query = (
            select(AssetJob, ApiClient.client_kind)
            .join(ApiClient, ApiClient.id == AssetJob.client_id)
            .where(
                AssetJob.status == "QUEUED",
                AssetJob.cancel_requested.is_(False),
            )
        )
        if is_substance_worker_id(worker.id):
            claim_query = claim_query.where(AssetJob.job_type == "SUBSTANCE_BAKE_V1")
            if pending_substance_job_ids:
                claim_query = claim_query.where(AssetJob.id.in_(pending_substance_job_ids))
        else:
            claim_query = claim_query.where(AssetJob.job_type != "SUBSTANCE_BAKE_V1")
            if worker.skill_version == RETOPOLOGY_V6_SKILL_VERSION:
                # A Direct V2 worker consumes only the new high-only contract. V5
                # audit/process work stays on the rollback pool because the
                # two Skill contracts intentionally have incompatible inputs.
                claim_query = claim_query.where(
                    AssetJob.job_type.not_in(
                        {"RETOPOLOGY_AUDIT", "RETOPOLOGY_PROCESS_V1"}
                    )
                )
            else:
                # Old Workers must never claim a Direct V2 job: they still execute
                # bootstrap-low/target-face semantics and would corrupt the
                # new contract even if the external route is unchanged.
                claim_query = claim_query.where(
                    AssetJob.job_type != "RETOPOLOGY_PROCESS_V2"
                )
            codex_probe_cutoff = datetime.now(UTC) - timedelta(
                seconds=cfg.asset_codex_probe_max_age_seconds
            )
            codex_ready = (
                worker.codex_auth_status == "AUTHENTICATED"
                and worker.codex_probe_status == "HEALTHY"
                and worker.codex_last_checked_at is not None
                and as_utc(worker.codex_last_checked_at) >= codex_probe_cutoff
            )
            if not codex_ready:
                # Codex planning is required only by the full retopology
                # process. Keep UV and Blender-only retopology audit capacity
                # available while the credential or live probe is unhealthy.
                claim_query = claim_query.where(AssetJob.job_type.not_in(CODEX_REQUIRED_JOB_TYPES))
        claimed_row = (
            await db.execute(
                claim_query
                # Production always wins the next free slot.  This is deliberately
                # evaluated before queue age so an old load-test job cannot delay a
                # real caller.  FIFO remains stable inside each client-kind pool.
                .order_by(
                    case((ApiClient.client_kind == "test", 1), else_=0),
                    AssetJob.created_at,
                    AssetJob.id,
                ).with_for_update(skip_locked=True, of=AssetJob)
            )
        ).first()
        if claimed_row is None:
            return JSONResponse({"job": None}, 200)
        job, client_kind = claimed_row
        if (
            is_substance_worker_id(worker.id)
            and client_kind == "test"
            and await production_gpu_work_active(db)
        ):
            # Load-test Baker traffic may use only truly idle GPU capacity.
            # The physical Node row is already locked, so no GPU assignment
            # can race between this check and installation of the Baker fence.
            return JSONResponse({"job": None}, 200)
        if substance_node is not None:
            labels = dict(substance_node.labels or {})
            fenced_job_ids = substance_fence_job_ids(labels)
            if len(fenced_job_ids) >= SUBSTANCE_MAX_PARALLEL:
                return JSONResponse({"job": None}, 200)
            if job.id not in fenced_job_ids:
                fenced_job_ids.append(job.id)
            labels[SUBSTANCE_FENCE_LABEL] = fenced_job_ids
            labels.pop(SUBSTANCE_LEGACY_FENCE_LABEL, None)
            if not ensure_substance_owned_drain(substance_node, labels):
                return JSONResponse({"job": None}, 200)
            pending = labels.get(SUBSTANCE_PENDING_RESERVATION_LABEL)
            if isinstance(pending, dict) and isinstance(pending.get("job_ids"), list):
                remaining_pending = [
                    str(job_id) for job_id in pending["job_ids"] if str(job_id) != job.id
                ]
                if remaining_pending:
                    pending["job_ids"] = remaining_pending
                    labels[SUBSTANCE_PENDING_RESERVATION_LABEL] = pending
                else:
                    labels.pop(SUBSTANCE_PENDING_RESERVATION_LABEL, None)
            substance_node.labels = labels
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        job.status = "CLAIMED"
        # The previous attempt remains auditable through AssetJobEvent.  The
        # mutable job row represents the active attempt only.
        job.error_code = None
        job.error_message = None
        job.worker_id = worker.id
        job.worker_instance_id = body.agent_instance_id
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
            "RETOPOLOGY_PROCESS_V2": 1800,
            "SUBSTANCE_BAKE_V1": 600,
        }.get(job.job_type, 300)
        job.last_progress_at = now
        worker.current_jobs += 1
        await append_asset_event(
            db,
            job,
            details={
                "event": "asset.claimed",
                "worker_id": worker.id,
                "worker_instance_id": job.worker_instance_id,
            },
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

    async def leased_job(
        job_id: str,
        token: str,
        db: AsyncSession,
        *,
        lock_substance_node: bool = False,
    ) -> AssetJob:
        if lock_substance_node:
            await lock_substance_node_for_job(job_id, db)
        job = await db.scalar(
            select(AssetJob)
            .where(AssetJob.id == job_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if (
            job is None
            or job.lease_token_hash is None
            or not hmac.compare_digest(job.lease_token_hash, lease_token_hash(token))
            or job.lease_expires_at is None
            or as_utc(job.lease_expires_at) <= datetime.now(UTC)
        ):
            raise HTTPException(409, detail={"code": "ASSET_LEASE_INVALID"})
        return job

    async def prepare_asset_completion(
        job_id: str,
        token: str,
        db: AsyncSession,
        expected_job_type: str,
    ) -> AssetCompletionSnapshot:
        """Validate and renew a completion lease, then release every DB lock.

        Uploaded bytes are untrusted and may be large.  Completion endpoints
        must never keep an AssetJob (or the shared Substance GPU Node) locked
        while streaming or inspecting them.
        """

        job = await leased_job(
            job_id,
            token,
            db,
            lock_substance_node=expected_job_type == "SUBSTANCE_BAKE_V1",
        )
        if job.job_type != expected_job_type:
            raise HTTPException(409, detail={"code": "ASSET_JOB_TYPE_MISMATCH"})
        job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=cfg.asset_worker_lease_seconds)
        snapshot = AssetCompletionSnapshot(
            id=job.id,
            job_type=job.job_type,
            worker_id=job.worker_id,
            source_filename=job.source_filename,
            input_sha256=job.input_sha256,
            options=dict(job.options or {}),
        )
        await db.commit()
        return snapshot

    async def lock_asset_completion_for_publish(
        snapshot: AssetCompletionSnapshot,
        token: str,
        db: AsyncSession,
    ) -> AssetJob:
        """Revalidate completion ownership without taking a Worker snapshot."""

        # The terminal counter mutation is a single in-database arithmetic
        # UPDATE after this Job lock.  Loading AssetWorker here and later
        # assigning a Python snapshot can overwrite a concurrent claim.
        job = await leased_job(
            snapshot.id,
            token,
            db,
            lock_substance_node=snapshot.job_type == "SUBSTANCE_BAKE_V1",
        )
        if job.job_type != snapshot.job_type or job.worker_id != snapshot.worker_id:
            raise HTTPException(409, detail={"code": "ASSET_LEASE_INVALID"})
        return job

    async def cancel_at_completion_safe_point(
        db: AsyncSession,
        job: AssetJob,
    ) -> dict[str, Any] | None:
        """Finalize a requested cancellation before any artifact is published."""
        if not job.cancel_requested:
            return None
        now = datetime.now(UTC)
        job.status = "CANCELLED"
        job.stage = "CANCELLED"
        job.stage_message = "任务已在制品发布前的 Worker 安全点取消"
        job.estimated_remaining_seconds = 0
        job.last_progress_at = now
        job.finished_at = now
        job.lease_token_hash = None
        job.lease_expires_at = None
        await decrement_asset_worker_jobs_atomic(db, job.worker_id)
        await release_substance_gpu_fence(
            db,
            job,
            cfg.substance_pending_reservation_seconds,
            cfg.asset_worker_heartbeat_timeout_seconds,
        )
        await append_asset_event(
            db,
            job,
            details={"event": "asset.cancelled", "safe_point": "before_publish"},
        )
        await db.commit()
        return {
            "accepted": False,
            "status": "CANCELLED",
            "cancel_requested": True,
        }

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
        stage = canonical_worker_progress_stage(body.stage)
        job.status = "RUNNING"
        job.progress = max(job.progress, body.progress)
        job.stage = stage
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
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        blend: Annotated[UploadFile, File()],
        fbx: Annotated[UploadFile, File()],
        report: Annotated[UploadFile, File()],
        qa: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        snapshot = await prepare_asset_completion(job_id, lease, db, "UV_UNWRAP")
        uploads = {"blend": blend, "fbx": fbx, "report": report, "qa": qa}
        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        committed = False
        try:
            for kind, upload in uploads.items():
                filename, content_type = UV_REQUIRED_ARTIFACTS[kind]
                path = staging / filename
                digest, size = await persist_completion_upload(upload, path)
                if size == 0:
                    raise HTTPException(422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind})
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(staging / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
                    )
                )
            try:
                qa_payload = json.loads((staging / "model_QA.json").read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise HTTPException(422, detail={"code": "ASSET_QA_INVALID"}) from exc
            hard_failures = qa_payload.get("hard_failures")
            if not isinstance(hard_failures, list) or hard_failures:
                raise HTTPException(422, detail={"code": "ASSET_QA_FAILED"})
            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
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
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await append_asset_event(db, job, details={"event": "asset.succeeded"})
            await db.commit()
            committed = True
            return {"accepted": True}
        finally:
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/retopology-complete")
    async def worker_complete_retopology_audit(
        job_id: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        audit: Annotated[UploadFile, File()],
        manifest: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        snapshot = await prepare_asset_completion(job_id, lease, db, "RETOPOLOGY_AUDIT")
        uploads = {"audit": audit, "manifest": manifest}
        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        committed = False
        try:
            for kind, upload in uploads.items():
                filename, content_type = RETOPOLOGY_AUDIT_ARTIFACTS[kind]
                path = staging / filename
                digest, size = await persist_completion_upload(upload, path)
                if size == 0:
                    raise HTTPException(422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind})
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(staging / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
                    )
                )
            try:
                audit_payload = json.loads((staging / "retopology_audit.json").read_text("utf-8"))
                manifest_payload = json.loads(
                    (staging / "retopology_manifest.json").read_text("utf-8")
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AUDIT_INVALID"}) from exc
            if audit_payload.get("schema_version") != 2:
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AUDIT_SCHEMA_INVALID"})
            objects = audit_payload.get("objects")
            if not isinstance(objects, dict) or not {"high", "reference", "low"}.issubset(objects):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AUDIT_OBJECTS_MISSING"})
            visual_review = audit_payload.get("visual_review_required")
            if not isinstance(visual_review, list) or not {
                "front",
                "side",
                "top",
                "perspective",
            }.issubset(set(visual_review)):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_VISUAL_REVIEW_MISSING"})
            if (
                manifest_payload.get("job_id") != snapshot.id
                or manifest_payload.get("input_sha256") != snapshot.input_sha256
                or manifest_payload.get("job_type") != snapshot.job_type
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_MANIFEST_MISMATCH"})
            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
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
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await release_substance_gpu_fence(
                db,
                job,
                cfg.substance_pending_reservation_seconds,
                cfg.asset_worker_heartbeat_timeout_seconds,
            )
            await append_asset_event(
                db,
                job,
                details={
                    "event": "asset.succeeded" if audit_passed else "asset.qa_failed",
                    "audit_passed": audit_passed,
                },
            )
            await db.commit()
            committed = True
            return {
                "accepted": True,
                "status": job.status,
                "review_required": False,
                "audit_passed": audit_passed,
            }
        finally:
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/retopology-process-complete")
    async def worker_complete_retopology_process(
        job_id: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        snapshot = await prepare_asset_completion(job_id, lease, db, "RETOPOLOGY_PROCESS_V1")
        form = await request.form()
        expected = dict(RETOPOLOGY_PROCESS_REQUIRED_ARTIFACTS)
        if snapshot.options.get("reference_views"):
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

        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        committed = False
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
                digest, size = await persist_completion_upload(upload, path)
                if size == 0:
                    raise HTTPException(422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind})
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(staging / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
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
            if (
                baseline_payload.get("schema_version") != 2
                or audit_payload.get("schema_version") != 2
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AUDIT_SCHEMA_INVALID"})
            if report_payload.get("schema_version") != "retopology_process_report.v1":
                raise HTTPException(422, detail={"code": "RETOPOLOGY_PROCESS_REPORT_INVALID"})
            quality_gate = report_payload.get("quality_gate")
            if (
                not isinstance(quality_gate, dict)
                or quality_gate.get("schema_version") != "retopology_quality_gate.v2"
                or not isinstance(quality_gate.get("passed"), bool)
                or not isinstance(quality_gate.get("failures"), list)
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_QUALITY_GATE_INVALID"})
            if agent_plan_payload.get("recommended_algorithm") not in {
                "quadriflow",
                "cleanup_existing",
            } or not isinstance(agent_plan_payload.get("target_faces"), int):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AGENT_PLAN_INVALID"})
            if (
                manifest_payload.get("schema_version") != "retopology_process_manifest.v1"
                or manifest_payload.get("job_id") != snapshot.id
                or manifest_payload.get("job_type") != snapshot.job_type
                or manifest_payload.get("input_sha256") != snapshot.input_sha256
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_MANIFEST_MISMATCH"})
            expected_objects = {
                "high": snapshot.options["high_object"],
                "reference": snapshot.options["reference_object"],
                "current": snapshot.options["low_object"],
                "generated": snapshot.options["generated_low_object"],
            }
            if manifest_payload.get("objects") != expected_objects:
                raise HTTPException(422, detail={"code": "RETOPOLOGY_MANIFEST_OBJECTS_MISMATCH"})
            # The old key is accepted while workers roll. This is visual
            # evidence for deterministic QA, not a manual approval gate.
            visual_evidence = manifest_payload.get("visual_evidence") or manifest_payload.get(
                "visual_review"
            )
            if (
                not isinstance(visual_evidence, dict)
                or visual_evidence.get("required") is not True
                or set(visual_evidence.get("views", [])) != {"front", "side", "top", "perspective"}
                or set(visual_evidence.get("roles", [])) != {"high", "reference", "generated"}
                or visual_evidence.get("manual_review_required", False) is not False
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_VISUAL_EVIDENCE_MISSING"})
            agent_plan = manifest_payload.get("agent_plan")
            if (
                not isinstance(agent_plan, dict)
                or agent_plan.get("required") is not True
                or agent_plan.get("recommended_algorithm")
                != agent_plan_payload.get("recommended_algorithm")
                or agent_plan.get("recommended_target_faces")
                != agent_plan_payload.get("target_faces")
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AGENT_MANIFEST_MISMATCH"})
            if (
                manifest_payload.get("source_preserved") is not True
                or report_payload.get("source_preserved") is not True
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_SOURCE_PROTECTION_FAILED"})
            topology_goal_met = bool(report_payload.get("topology_goal_met"))
            if (
                topology_goal_met != bool(quality_gate.get("passed"))
                or manifest_payload.get("quality_gate") != quality_gate
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_QUALITY_GATE_MISMATCH"})
            if manifest_payload.get("automatic_final_promotion_allowed") != (
                bool(audit_payload.get("audit_passed")) and topology_goal_met
            ):
                raise HTTPException(422, detail={"code": "RETOPOLOGY_AUTOMATIC_DELIVERY_INVALID"})
            for filename, (_, content_type) in {
                **RETOPOLOGY_PROCESS_REQUIRED_FILENAMES,
                **(
                    RETOPOLOGY_PROCESS_OPTIONAL_FILENAMES
                    if snapshot.options.get("reference_views")
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
            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
            db.add_all(created)
            audit_passed = audit_payload.get("audit_passed") is True
            report_promotable = report_payload.get("topology_goal_met") is True
            manifest_promotable = manifest_payload.get("automatic_final_promotion_allowed") is True
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
            advisory_warning = cfg.retopology_qa_enforcement == "advisory" and not quality_passed
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
                job.stage_message = "候选已通过严格 QA 与三组四视图生成，自动发布交付"
                completion_event = "asset.succeeded"
            elif advisory_warning:
                job.stage_message = "候选已生成并交付；严格 QA 未通过，告警与完整报告已保留"
                completion_event = "asset.succeeded_with_warnings"
            else:
                job.stage_message = "候选未满足拓扑目标或硬性 QA；仅保留诊断制品，不可交付"
                completion_event = "asset.qa_failed"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = job.last_progress_at
            job.error_code = None if delivery_allowed else "RETOPOLOGY_QUALITY_GATE_FAILED"
            job.error_message = (
                None if job.error_code is None else json.dumps(quality_failures, ensure_ascii=False)
            )
            job.lease_expires_at = None
            job.lease_token_hash = None
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await release_substance_gpu_fence(
                db,
                job,
                cfg.substance_pending_reservation_seconds,
                cfg.asset_worker_heartbeat_timeout_seconds,
            )
            await append_asset_event(
                db,
                job,
                details={
                    "event": completion_event,
                    "warning_code": (
                        "RETOPOLOGY_QUALITY_GATE_WARNING" if advisory_warning else None
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
            committed = True
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
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/retopology-v6-formal-complete")
    async def worker_complete_retopology_v6_formal(
        job_id: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        """Validate V6 evidence and publish only an all-gates-passed formal low."""

        verify_runtime_resources(RETOPOLOGY_V6_ROOT)
        snapshot = await prepare_asset_completion(job_id, lease, db, "RETOPOLOGY_PROCESS_V2")
        form = await request.form()
        uploads: dict[str, StarletteUploadFile] = {}
        for kind in RETOPOLOGY_V6_ARTIFACTS:
            upload = form.get(kind)
            if not isinstance(upload, StarletteUploadFile):
                raise HTTPException(
                    422, detail={"code": "ASSET_ARTIFACT_MISSING", "kind": kind}
                )
            uploads[kind] = upload
        unknown = set(form.keys()) - set(RETOPOLOGY_V6_ARTIFACTS)
        if unknown:
            raise HTTPException(
                422,
                detail={"code": "ASSET_ARTIFACT_UNEXPECTED", "kinds": sorted(unknown)},
            )

        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        committed = False
        try:
            for kind, upload in uploads.items():
                filename, content_type = RETOPOLOGY_V6_ARTIFACTS[kind]
                if upload.filename != filename:
                    raise HTTPException(
                        422,
                        detail={"code": "ASSET_ARTIFACT_FILENAME_MISMATCH", "kind": kind},
                    )
                path = staging / filename
                digest, size = await persist_completion_upload(upload, path)
                if size <= 0:
                    raise HTTPException(
                        422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind}
                    )
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(path),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
                    )
                )

            try:
                result_payload = json.loads((staging / "result.json").read_text("utf-8"))
                plan_payload = json.loads(
                    (staging / "execution_plan.json").read_text("utf-8")
                )
                qa_payload = json.loads((staging / "qa_report.json").read_text("utf-8"))
                manifest_payload = json.loads((staging / "manifest.json").read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_JSON_INVALID"}
                ) from exc
            try:
                validate_contract_payload(
                    RETOPOLOGY_V6_ROOT,
                    "retopology-plan-v6.schema.json",
                    plan_payload,
                )
                validate_contract_payload(
                    RETOPOLOGY_V6_ROOT,
                    "retopology-result-v6.schema.json",
                    result_payload,
                )
            except Exception as exc:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_SCHEMA_INVALID"}
                ) from exc

            if (
                result_payload.get("job_id") != snapshot.id
                or result_payload.get("policy", {}).get("sha256")
                != snapshot.options.get("policy_sha256")
                or result_payload.get("source", {}).get("sha256_before")
                != snapshot.options.get("project_sha256")
                or result_payload.get("source", {}).get("sha256_after")
                != snapshot.options.get("project_sha256")
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_IDENTITY_MISMATCH"}
                )
            if not isinstance(qa_payload, dict) or qa_payload.get("gates") != result_payload.get(
                "gates"
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_QA_RESULT_MISMATCH"}
                )
            if (
                not isinstance(manifest_payload, dict)
                or manifest_payload.get("job_id") != snapshot.id
                or manifest_payload.get("engine_contract") != "retopology-v6"
                or manifest_payload.get("policy_sha256")
                != snapshot.options.get("policy_sha256")
                or manifest_payload.get("source_sha256")
                != snapshot.options.get("project_sha256")
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_MANIFEST_MISMATCH"}
                )

            staged_by_kind = {item.kind: item for item in created}
            result_artifacts = result_payload.get("artifacts")
            if not isinstance(result_artifacts, list):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_ARTIFACT_IDENTITIES_MISSING"}
                )
            result_by_role = {
                str(item.get("role")): item
                for item in result_artifacts
                if isinstance(item, dict)
            }
            if RETOPOLOGY_V6_RESULT_ARTIFACT_ROLES.difference(result_by_role):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_ARTIFACT_ROLES_MISSING"}
                )
            for role in RETOPOLOGY_V6_RESULT_ARTIFACT_ROLES:
                row = result_by_role[role]
                artifact = staged_by_kind[role]
                if (
                    Path(str(row.get("object_key"))).name != artifact.filename
                    or row.get("sha256") != artifact.sha256
                    or row.get("size_bytes") != artifact.size_bytes
                ):
                    raise HTTPException(
                        422,
                        detail={
                            "code": "RETOPOLOGY_V6_ARTIFACT_IDENTITY_MISMATCH",
                            "role": role,
                        },
                    )

            for kind in ("comparison_contact_sheet", "wireframe_contact_sheet"):
                try:
                    with Image.open(staging / RETOPOLOGY_V6_ARTIFACTS[kind][0]) as image:
                        if image.width * image.height > cfg.max_image_pixels:
                            raise HTTPException(
                                413,
                                detail={
                                    "code": "RETOPOLOGY_REVIEW_IMAGE_TOO_LARGE",
                                    "kind": kind,
                                },
                            )
                        image.verify()
                except (OSError, UnidentifiedImageError) as exc:
                    raise HTTPException(
                        422,
                        detail={"code": "RETOPOLOGY_REVIEW_IMAGE_INVALID", "kind": kind},
                    ) from exc

            gates = result_payload.get("gates")
            all_gates_passed = isinstance(gates, dict) and len(gates) == 8 and all(
                isinstance(gate, dict) and gate.get("passed") is True
                for gate in gates.values()
            )
            strict_publish_allowed = (
                result_payload.get("status") == "succeeded"
                and result_payload.get("publish_allowed") is True
                and result_payload.get("source", {}).get("unchanged") is True
                and all_gates_passed
            )
            if result_payload.get("publish_allowed") is True and not strict_publish_allowed:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_V6_PUBLISH_GATE_BYPASS"}
                )

            # In production advisory mode, QA controls the warning attached to
            # a delivery, not whether intact candidate bytes are returned.
            # Identity, source immutability, schema, hashes and artifact
            # completeness above remain hard gates.
            advisory_warning = (
                cfg.retopology_qa_enforcement == "advisory"
                and not strict_publish_allowed
            )
            delivery_allowed = strict_publish_allowed or advisory_warning

            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
            if delivery_allowed:
                stem = Path(snapshot.options["project_filename"]).stem
                staged_by_kind["final_low_blend"].kind = "blend"
                staged_by_kind["final_low_blend"].filename = f"{stem}_GAME_LOW.blend"
                staged_by_kind["final_low_exchange"].kind = "fbx"
                staged_by_kind["final_low_exchange"].filename = f"{stem}_GAME_LOW.fbx"
            db.add_all(created)
            job.status = "SUCCEEDED" if delivery_allowed else "FAILED"
            job.progress = 100
            job.stage = "SUCCEEDED" if delivery_allowed else "FAILED"
            if strict_publish_allowed:
                job.stage_message = "V6 低模已通过八项 QA 并原子发布"
            elif advisory_warning:
                job.stage_message = "V6 候选 BLEND/FBX 已交付；QA 未通过，请查看质量告警与完整报告"
            else:
                job.stage_message = "V6 候选未通过全部门禁；诊断证据已隔离保留"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = job.last_progress_at
            failure_codes = result_payload.get("failure_codes")
            job.error_code = None if delivery_allowed else "RETOPOLOGY_QUALITY_GATE_FAILED"
            job.error_message = (
                None
                if delivery_allowed
                else json.dumps(
                    failure_codes if isinstance(failure_codes, list) else [],
                    ensure_ascii=False,
                )
            )
            job.options = {
                **job.options,
                "v6_result": {
                    "status": result_payload.get("status"),
                    "publish_allowed": strict_publish_allowed,
                    "delivered_with_warnings": advisory_warning,
                    "failure_codes": failure_codes if isinstance(failure_codes, list) else [],
                },
                **(
                    {
                        "qa_warning": {
                            "code": "RETOPOLOGY_QUALITY_GATE_WARNING",
                            "enforcement": "advisory",
                            "failure_codes": (
                                failure_codes if isinstance(failure_codes, list) else []
                            ),
                        }
                    }
                    if advisory_warning
                    else {}
                ),
            }
            job.lease_expires_at = None
            job.lease_token_hash = None
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await append_asset_event(
                db,
                job,
                details={
                    "event": (
                        "asset.succeeded_with_warnings"
                        if advisory_warning
                        else "asset.succeeded"
                        if strict_publish_allowed
                        else "asset.qa_failed"
                    ),
                    "engine_contract": "retopology-v6",
                    "publish_allowed": strict_publish_allowed,
                    "delivered_with_warnings": advisory_warning,
                    "warning_code": (
                        "RETOPOLOGY_QUALITY_GATE_WARNING" if advisory_warning else None
                    ),
                    "failure_codes": failure_codes if isinstance(failure_codes, list) else [],
                },
            )
            await db.commit()
            committed = True
            return {
                "accepted": True,
                "status": job.status,
                "publish_allowed": strict_publish_allowed,
                "delivered_with_warnings": advisory_warning,
            }
        finally:
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/retopology-v6-complete")
    async def worker_complete_retopology_direct_v2(
        job_id: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        """Publish the Direct V2 generated FBX without running the retired V6 QA."""

        snapshot = await prepare_asset_completion(job_id, lease, db, "RETOPOLOGY_PROCESS_V2")
        form = await request.form()
        uploads: dict[str, StarletteUploadFile] = {}
        for kind in RETOPOLOGY_DIRECT_V2_ARTIFACTS:
            upload = form.get(kind)
            if not isinstance(upload, StarletteUploadFile):
                raise HTTPException(
                    422, detail={"code": "ASSET_ARTIFACT_MISSING", "kind": kind}
                )
            uploads[kind] = upload
        unknown = set(form.keys()) - set(RETOPOLOGY_DIRECT_V2_ARTIFACTS)
        if unknown:
            raise HTTPException(
                422,
                detail={"code": "ASSET_ARTIFACT_UNEXPECTED", "kinds": sorted(unknown)},
            )

        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        committed = False
        try:
            for kind, upload in uploads.items():
                filename, content_type = RETOPOLOGY_DIRECT_V2_ARTIFACTS[kind]
                if upload.filename != filename:
                    raise HTTPException(
                        422,
                        detail={"code": "ASSET_ARTIFACT_FILENAME_MISMATCH", "kind": kind},
                    )
                path = staging / filename
                digest, size = await persist_completion_upload(upload, path)
                if size <= 0:
                    raise HTTPException(
                        422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind}
                    )
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(path),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
                    )
                )

            try:
                generation = json.loads(
                    (staging / "generation_report.json").read_text("utf-8")
                )
                result = json.loads((staging / "result.json").read_text("utf-8"))
                manifest = json.loads(
                    (staging / "delivery_manifest.json").read_text("utf-8")
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_DIRECT_V2_JSON_INVALID"}
                ) from exc

            if (
                generation.get("status") != "generated_for_user_inspection"
                or not isinstance(generation.get("assets"), list)
                or not generation["assets"]
                or result.get("status") != "generated_for_user_inspection"
                or result.get("automatic_post_generation_review") is not False
                or result.get("automatic_retry") is not False
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_DIRECT_V2_RESULT_INVALID"}
                )
            staged_by_kind = {item.kind: item for item in created}
            if (
                manifest.get("schema_version") != "retopology_direct_delivery.v2"
                or manifest.get("job_id") != snapshot.id
                or manifest.get("engine_contract") != "retopology-direct-v2"
                or manifest.get("package_sha256")
                != snapshot.options.get("package_sha256")
                or manifest.get("source_sha256") != snapshot.options.get("project_sha256")
                or manifest.get("agent_blend_sha256") != result.get("output_sha256")
                or manifest.get("delivery_blend_sha256")
                != staged_by_kind["blend"].sha256
                or manifest.get("delivery_blend_size_bytes")
                != staged_by_kind["blend"].size_bytes
                or manifest.get("delivery_fbx_sha256")
                != staged_by_kind["fbx"].sha256
                or manifest.get("delivery_fbx_size_bytes")
                != staged_by_kind["fbx"].size_bytes
                or manifest.get("automatic_post_generation_review") is not False
                or manifest.get("automatic_retry") is not False
            ):
                raise HTTPException(
                    422, detail={"code": "RETOPOLOGY_DIRECT_V2_IDENTITY_MISMATCH"}
                )

            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
            stem = Path(snapshot.options["project_filename"]).stem
            staged_by_kind["blend"].kind = "blend"
            staged_by_kind["blend"].filename = f"{stem}_GAME_LOW.blend"
            staged_by_kind["fbx"].kind = "fbx"
            staged_by_kind["fbx"].filename = f"{stem}_GAME_LOW.fbx"
            db.add_all(created)
            job.status = "SUCCEEDED"
            job.progress = 100
            job.stage = "SUCCEEDED"
            job.stage_message = "Direct V2 低模 BLEND 与 FBX 已生成并交付；等待用户检查"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = job.last_progress_at
            job.error_code = None
            job.error_message = None
            job.options = {
                **job.options,
                "direct_v2_result": {
                    "status": "generated_for_user_inspection",
                    "delivery_format": "blend+fbx",
                    "delivery_formats": ["blend", "fbx"],
                    "automatic_post_generation_review": False,
                    "automatic_retry": False,
                    "assets": generation["assets"],
                },
            }
            job.lease_expires_at = None
            job.lease_token_hash = None
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await append_asset_event(
                db,
                job,
                details={
                    "event": "asset.succeeded",
                    "engine_contract": "retopology-direct-v2",
                    "delivery_format": "blend+fbx",
                    "automatic_post_generation_review": False,
                },
            )
            await db.commit()
            committed = True
            return {
                "accepted": True,
                "status": job.status,
                "delivery_format": "fbx",
                "generated_for_user_inspection": True,
            }
        finally:
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/uv-v2-complete")
    async def worker_complete_uv_v2(
        job_id: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
        blend: Annotated[UploadFile, File()],
        fbx: Annotated[UploadFile, File()],
        report: Annotated[UploadFile, File()],
        qa: Annotated[UploadFile, File()],
        fbx_qa: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        snapshot = await prepare_asset_completion(job_id, lease, db, "UV_PROCESS_V2")
        stem = Path(snapshot.source_filename).stem
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
        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        committed = False
        try:
            for kind, upload in uploads.items():
                filename, content_type = contract[kind]
                path = staging / filename
                digest, size = await persist_completion_upload(upload, path)
                if size == 0:
                    raise HTTPException(422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind})
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(staging / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest,
                    )
                )
            try:
                report_payload = json.loads((staging / contract["report"][0]).read_text("utf-8"))
                blend_qa_payload = json.loads((staging / contract["qa"][0]).read_text("utf-8"))
                fbx_qa_payload = json.loads((staging / contract["fbx_qa"][0]).read_text("utf-8"))
            except (OSError, ValueError) as exc:
                raise HTTPException(422, detail={"code": "ASSET_QA_INVALID"}) from exc
            if not all(
                isinstance(payload, dict)
                for payload in (report_payload, blend_qa_payload, fbx_qa_payload)
            ):
                raise HTTPException(422, detail={"code": "ASSET_QA_INVALID"})
            if (
                report_payload.get("input") not in {None, snapshot.source_filename}
                and Path(str(report_payload.get("input"))).name != snapshot.source_filename
            ):
                raise HTTPException(422, detail={"code": "ASSET_REPORT_INPUT_MISMATCH"})
            quality_failures: list[str] = []
            failed_qa: list[str] = []
            for label, payload in (
                ("blend", blend_qa_payload),
                ("fbx_readback", fbx_qa_payload),
            ):
                hard_failures = payload.get("hard_failures")
                if not isinstance(hard_failures, list):
                    raise HTTPException(422, detail={"code": "ASSET_QA_INVALID", "qa": label})
                passed = payload.get("passed")
                if not isinstance(passed, bool):
                    raise HTTPException(422, detail={"code": "ASSET_QA_INVALID", "qa": label})
                if hard_failures or not passed:
                    failed_qa.append(label)
                    if hard_failures:
                        for failure in hard_failures:
                            rendered = (
                                failure
                                if isinstance(failure, str)
                                else json.dumps(
                                    failure,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                            )
                            quality_failures.append(f"{label}: {rendered}")
                    else:
                        quality_failures.append(f"{label}: passed=false")
            quality_failures = list(dict.fromkeys(quality_failures))
            quality_passed = not failed_qa
            advisory_warning = cfg.uv_qa_enforcement == "advisory" and not quality_passed
            if not quality_passed and not advisory_warning:
                raise HTTPException(422, detail={"code": "ASSET_QA_FAILED", "qa": failed_qa[0]})
            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
            db.add_all(created)
            if advisory_warning:
                job.options = {
                    **job.options,
                    "qa_warning": {
                        "code": "UV_QUALITY_GATE_WARNING",
                        "enforcement": cfg.uv_qa_enforcement,
                        "failed_qa": failed_qa,
                        "failures": quality_failures,
                    },
                }
            job.status = "SUCCEEDED"
            job.progress = 100
            job.stage = "SUCCEEDED"
            if quality_passed:
                job.stage_message = "PBR UV、FBX 回读与双重 QA 已通过并发布"
                completion_event = "asset.succeeded"
            else:
                job.stage_message = "PBR UV 五件套已发布；几何 QA 未通过，告警与完整报告已保留"
                completion_event = "asset.succeeded_with_warnings"
            job.estimated_remaining_seconds = 0
            job.last_progress_at = datetime.now(UTC)
            job.finished_at = datetime.now(UTC)
            job.error_code = None
            job.error_message = None
            job.lease_expires_at = None
            job.lease_token_hash = None
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await append_asset_event(
                db,
                job,
                details={
                    "event": completion_event,
                    "warning_code": ("UV_QUALITY_GATE_WARNING" if advisory_warning else None),
                    "qa_enforcement": cfg.uv_qa_enforcement,
                    "quality_gate_passed": quality_passed,
                    "quality_failures": quality_failures,
                    "failed_qa": failed_qa,
                },
            )
            await db.commit()
            committed = True
            return {
                "accepted": True,
                "status": job.status,
                "quality_gate_passed": quality_passed,
                "qa_enforcement": cfg.uv_qa_enforcement,
                "delivered_with_warnings": advisory_warning,
            }
        finally:
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/substance-complete")
    async def worker_complete_substance(
        job_id: str,
        request: Request,
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
        snapshot = await prepare_asset_completion(job_id, lease, db, "SUBSTANCE_BAKE_V1")
        profile = str(snapshot.options.get("profile", ""))
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

        staging = cfg.asset_root / snapshot.id / f".outputs-{uuid.uuid4().hex}"
        staging.mkdir(parents=True, exist_ok=False)
        created: list[AssetArtifact] = []
        actual_sha: dict[str, str] = {}
        committed = False
        try:
            for kind, (filename, content_type) in contract.items():
                upload = supplied[kind]
                assert upload is not None
                path = staging / filename
                digest, size = await persist_completion_upload(
                    upload, path, max_bytes=cfg.asset_max_upload_bytes
                )
                if size == 0:
                    raise HTTPException(422, detail={"code": "ASSET_ARTIFACT_EMPTY", "kind": kind})
                actual_sha[kind] = digest
                created.append(
                    AssetArtifact(
                        id=str(uuid.uuid4()),
                        job_id=snapshot.id,
                        kind=kind,
                        filename=filename,
                        path=str(staging / filename),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=actual_sha[kind],
                    )
                )

            expected_resolution = int(snapshot.options.get("resolution", 0))
            for kind in (
                "ao",
                "normal_dx",
                "normal_gl",
                "world_normal",
                "curvature",
                "thickness",
                "position",
                "base_color",
                "roughness",
                "metallic",
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
                result_payload = json.loads((staging / contract["result"][0]).read_text("utf-8"))
                log_text = (staging / contract["log"][0]).read_text("utf-8", errors="replace")
            except (OSError, ValueError) as exc:
                raise HTTPException(422, detail={"code": "SUBSTANCE_RESULT_INVALID"}) from exc
            tool = result_payload.get("tool") or {}
            execution = result_payload.get("execution") or {}
            output_hashes = result_payload.get("output_sha256") or {}
            schema_version = result_payload.get("schema_version")
            legacy_exit_evidence_valid = schema_version == 1 and execution.get("exit_code") == 0
            commands = execution.get("commands")
            expected_command_count = SUBSTANCE_BAKE_COMMAND_COUNTS[profile]
            all_exit_codes_observed = isinstance(commands, list) and all(
                isinstance(command, dict) and command.get("exit_code_observed") is True
                for command in commands
            )
            command_evidence_valid = (
                schema_version == 2
                and isinstance(commands, list)
                and len(commands) == expected_command_count
                and execution.get("command_count") == expected_command_count
                and execution.get("success_marker_verified") is True
                and all(
                    isinstance(command, dict)
                    and command.get("success_marker_present") is True
                    and isinstance(command.get("exit_code_observed"), bool)
                    and (
                        command.get("exit_code") == 0
                        if command.get("exit_code_observed") is True
                        else command.get("exit_code") is None
                    )
                    for command in commands
                )
                and execution.get("exit_code_observed") is all_exit_codes_observed
                and execution.get("exit_code") == (0 if all_exit_codes_observed else None)
                and log_text.count("Bake finished successfully") >= expected_command_count
            )
            if (
                not (legacy_exit_evidence_valid or command_evidence_valid)
                or result_payload.get("job_id") != snapshot.id
                or result_payload.get("status") != "SUCCEEDED"
                or result_payload.get("profile") != profile
                or tool.get("version") != "15.1.0"
                or tool.get("exe_sha256")
                != "7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D"
                or execution.get("comfyui_cache_policy") != "no_explicit_eviction_process_preserved"
                or execution.get("comfyui_container_restarted") is not False
                or execution.get("comfyui_process_continuity_verified") is not True
                or any(
                    output_hashes.get(kind) != actual_sha[kind]
                    for kind in contract
                    if kind not in {"result", "log"}
                )
                or "Bake finished successfully" not in log_text
            ):
                raise HTTPException(422, detail={"code": "SUBSTANCE_RESULT_INVALID"})

            fsync_completion_staging(staging)
            job = await lock_asset_completion_for_publish(snapshot, lease, db)
            cancelled = await cancel_at_completion_safe_point(db, job)
            if cancelled is not None:
                return cancelled
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
            await decrement_asset_worker_jobs_atomic(db, job.worker_id)
            await release_substance_gpu_fence(
                db,
                job,
                cfg.substance_pending_reservation_seconds,
                cfg.asset_worker_heartbeat_timeout_seconds,
            )
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
            committed = True
            return {"accepted": True, "status": job.status}
        finally:
            if not committed:
                await cleanup_uncommitted_completion(request, staging, created)

    @app.post("/internal/v1/assets/jobs/{job_id}/fail")
    async def worker_fail(
        job_id: str,
        body: WorkerFailure,
        db: Annotated[AsyncSession, Depends(session)],
        lease: Annotated[str, Header(alias="X-Asset-Lease")],
    ) -> dict[str, Any]:
        job = await leased_job(job_id, lease, db, lock_substance_node=True)
        previous_worker_id = job.worker_id
        previous_worker_instance_id = job.worker_instance_id
        requires_runtime_recovery = (
            job.job_type == "SUBSTANCE_BAKE_V1"
            and body.code
            in {
                "SUBSTANCE_COMFYUI_CONTINUITY_FAILED",
                "SUBSTANCE_BAKER_TERMINATION_UNCONFIRMED",
            }
            and previous_worker_id is not None
        )
        v6_post_build_failure = (
            job.job_type == "RETOPOLOGY_PROCESS_V2"
            and job.progress >= 70
            and body.code == "BLENDER_EXECUTION_FAILED"
        )
        effective_retryable = (
            body.retryable
            and not requires_runtime_recovery
            and not v6_post_build_failure
        )
        await decrement_asset_worker_jobs_atomic(db, previous_worker_id)
        if requires_runtime_recovery:
            # An ambiguous native process may still own the physical GPU.
            # Never requeue this attempt, even if the Agent marks the failure
            # retryable; preserve its Worker identity for recovery evidence.
            job.status = "FAILED"
            job.stage = "RECOVERY_REQUIRED"
            job.stage_message = (
                "Substance Worker 终止或 ComfyUI 连续性无法确认；等待宿主恢复证据"
            )
            job.estimated_remaining_seconds = 0
            job.finished_at = datetime.now(UTC)
        elif job.cancel_requested:
            job.status = "CANCELLED"
            job.stage = "CANCELLED"
            job.stage_message = "任务已在 Worker 安全点取消"
            job.estimated_remaining_seconds = 0
            job.finished_at = datetime.now(UTC)
        elif effective_retryable and job.attempt_count < cfg.asset_job_max_attempts:
            job.status = "QUEUED"
            job.stage = "RETRY_QUEUED"
            job.stage_message = "执行失败，任务已按策略返回队列重试"
            job.estimated_remaining_seconds = None
            job.worker_id = None
            job.worker_instance_id = None
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
        if requires_runtime_recovery and previous_worker_id is not None:
            await mark_substance_gpu_recovery_required(
                db,
                job,
                previous_worker_id,
                job.last_progress_at,
            )
        else:
            await release_substance_gpu_fence(
                db,
                job,
                cfg.substance_pending_reservation_seconds,
                cfg.asset_worker_heartbeat_timeout_seconds,
            )
        await append_asset_event(
            db,
            job,
            details={
                "event": (
                    "asset.cancelled"
                    if job.status == "CANCELLED"
                    else "asset.failed"
                    if job.status == "FAILED"
                    else "asset.retry_queued"
                ),
                "error_code": body.code,
                "error_message": body.message,
                "retryable": effective_retryable,
                "reported_retryable": body.retryable,
                "v6_post_build_retry_suppressed": v6_post_build_failure,
                "recovery_required": requires_runtime_recovery,
                "worker_instance_id": previous_worker_instance_id,
            },
        )
        await db.commit()
        return {"accepted": True, "status": job.status}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("gpu_control_asset_api.main:app", host="0.0.0.0", port=8010)
