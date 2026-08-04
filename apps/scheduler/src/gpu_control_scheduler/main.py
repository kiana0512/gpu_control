import asyncio
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import uuid
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from threading import Event
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from prometheus_client import Counter, Gauge, Histogram, Info, start_http_server
from redis.asyncio import Redis
from sqlalchemy import and_, case, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from packages.comfy_client import ComfyClient, ComfyError
from packages.gpu_control_core.admission import active_production_work_exists
from packages.gpu_control_core.batches import (
    ArchiveFrame,
    BatchContractError,
    build_result_archive,
    cleanup_result_archive_staging,
    materialize_batch_item,
    result_archive_final_path,
    result_archive_staging_path,
    transition_batch,
    workflow_identity_from_row,
)
from packages.gpu_control_core.database import (
    Database,
    SchedulerLockHandle,
    SchedulerLockLost,
)
from packages.gpu_control_core.enums import (
    TERMINAL_JOB_STATUSES,
    BatchItemStatus,
    BatchStatus,
    JobStatus,
    NodeHealth,
    NodeMode,
)
from packages.gpu_control_core.logging import bind_context, configure_logging, logger, reset_context
from packages.gpu_control_core.models import (
    ApiClient,
    BatchArtifact,
    BatchCancelOperation,
    CallbackAttempt,
    Job,
    JobArtifact,
    JobAttempt,
    JobBatch,
    JobBatchItem,
    JobCallback,
    Node,
    SystemSetting,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import (
    claim_next_job,
    prompt_client_id,
    release_lease,
    transition_job,
)
from packages.gpu_control_core.scheduling import OverflowGuard, QueueSnapshot, rank_nodes
from packages.gpu_control_core.security import (
    derive_callback_secret,
    sign_agent_request,
    sign_callback_payload,
)
from packages.gpu_control_core.settings import Settings, get_settings
from packages.gpu_control_core.storage import LocalJobStorage
from packages.gpu_control_core.workflow import node_compatibility_reasons

DECISION = Histogram(
    "gpu_control_scheduler_decision_duration_seconds", "Scheduler decision latency"
)
LOOP_LAG = Gauge(
    "gpu_control_scheduler_loop_lag_seconds",
    "Current scheduler event-loop wake delay beyond the latest probe deadline",
)
QUEUED = Gauge("gpu_control_jobs_queued", "Queued jobs")
RUNNING = Gauge("gpu_control_jobs_running", "Running jobs")
OLDEST = Gauge("gpu_control_oldest_queued_job_seconds", "Oldest queued job age")
NODE_HEALTH = Gauge("gpu_control_node_health", "Node health", ["node_id"])
NODE_JOBS = Gauge("gpu_control_node_current_jobs", "Current node jobs", ["node_id"])
COMPLETED = Counter("gpu_control_jobs_completed_total", "Completed jobs", ["workflow_key"])
FAILED = Counter("gpu_control_jobs_failed_total", "Failed jobs", ["error_code"])
OVERFLOW = Counter("gpu_control_4090_overflow_assignments_total", "4090 overflow assignments")
CALLBACK_ATTEMPTS = Counter(
    "gpu_control_callback_attempts_total", "Callback delivery attempts", ["result"]
)
CALLBACK_FAILURES = Counter(
    "gpu_control_callback_failures_total", "Callback deliveries that exhausted retries"
)
BUILD_INFO = Info(
    "gpu_control_scheduler_build",
    "Scheduler package, immutable build version and source revision",
)
BUILD_ALIGNED = Gauge(
    "gpu_control_scheduler_build_aligned",
    "1 when the installed package version matches the immutable build version",
)

FAIL_CLOSED_SUBMISSION_ERRORS = frozenset(
    {
        "COMFY_SUBMISSION_UNKNOWN",
        "COMFY_SUBMISSION_DUPLICATE",
        "COMFY_SUBMISSION_RECONCILE_FAILED",
    }
)
MAX_BATCH_MATERIALIZATIONS_PER_TICK = 32
SCHEDULER_LOCK_CHECK_INTERVAL_SECONDS = 2.0
SCHEDULER_LOCK_QUERY_TIMEOUT_SECONDS = 2.0
SCHEDULER_LOCK_FAILURE_GRACE_SECONDS = 3.0
SCHEDULER_LOOP_LAG_SAMPLE_INTERVAL_SECONDS = 0.5
ARCHIVE_BUILD_CANCEL_GRACE_SECONDS = 2.5
CALLBACK_DELIVERY_LEASE_SECONDS = 30
CALLBACK_DNS_TIMEOUT_SECONDS = 5
PROMPT_SUBMISSION_SETTLE_SECONDS = 35.0


def runtime_version_metadata() -> dict[str, Any]:
    try:
        installed = package_version("gpu-control")
    except PackageNotFoundError:
        installed = None
    build_version = os.environ.get("GPU_CONTROL_BUILD_VERSION")
    build_revision = os.environ.get("GPU_CONTROL_BUILD_REVISION")
    return {
        "component": "gpu-control-scheduler",
        "package_version": installed,
        "build_version": build_version,
        "source_revision": build_revision,
        "version_aligned": bool(installed and build_version and installed == build_version),
        "provenance_complete": bool(
            installed
            and build_version
            and installed == build_version
            and build_revision
            and re.fullmatch(r"[0-9a-f]{40}", build_revision)
        ),
    }


async def reconcile_prompt_submission(
    client: ComfyClient,
    client_id: str,
    *,
    settle_seconds: float = 0.0,
    poll_interval_seconds: float = 0.25,
) -> str:
    """Resolve one durable submit intent without issuing another POST /prompt."""
    deadline = asyncio.get_running_loop().time() + max(0.0, settle_seconds)
    while True:
        try:
            prompt_ids = await client.prompt_ids_for_client(client_id)
        except ComfyError as exc:
            if asyncio.get_running_loop().time() >= deadline:
                raise ComfyError(
                    "COMFY_SUBMISSION_RECONCILE_FAILED",
                    "cannot reconcile the persisted prompt submission intent",
                    {"client_id": client_id, "cause": exc.code},
                ) from exc
            prompt_ids = []
        if len(prompt_ids) == 1:
            return prompt_ids[0]
        if len(prompt_ids) > 1:
            raise ComfyError(
                "COMFY_SUBMISSION_DUPLICATE",
                "multiple Comfy prompts share one durable submission id",
                {"client_id": client_id, "prompt_ids": prompt_ids},
            )
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise ComfyError(
                "COMFY_SUBMISSION_UNKNOWN",
                "persisted prompt submission intent was not found in Comfy queue or history",
                {"client_id": client_id, "settle_seconds": settle_seconds},
            )
        await asyncio.sleep(min(poll_interval_seconds, remaining))


async def current_job_attempt(
    session: AsyncSession, job: Job, *, lock: bool = False
) -> JobAttempt:
    query = select(JobAttempt).where(
        JobAttempt.job_id == job.id,
        JobAttempt.attempt == job.attempt_count,
    )
    if lock:
        query = query.with_for_update()
    attempt = await session.scalar(query)
    if attempt is None:
        raise RuntimeError("current durable job attempt is missing")
    return attempt


async def prepare_prompt_submission(session: AsyncSession, job: Job) -> str:
    """Persist one outbound submit intent before POST /prompt is allowed."""
    attempt = await current_job_attempt(session, job, lock=True)
    expected = prompt_client_id(job.id, job.attempt_count)
    if job.submission_client_id not in {None, expected}:
        raise RuntimeError("job submission client id does not match its attempt")
    if attempt.prompt_client_id not in {None, expected}:
        raise RuntimeError("attempt prompt client id does not match its identity")
    job.submission_client_id = expected
    attempt.prompt_client_id = expected
    if job.submission_intent_at is None:
        job.submission_intent_at = datetime.now(UTC)
        attempt.prompt_attempts += 1
    return expected


async def persist_prompt_id(session: AsyncSession, job: Job, prompt_id: str) -> None:
    attempt = await current_job_attempt(session, job, lock=True)
    if job.prompt_id not in {None, prompt_id} or attempt.prompt_id not in {None, prompt_id}:
        raise ComfyError(
            "COMFY_SUBMISSION_DUPLICATE",
            "recovered prompt id conflicts with persisted attempt identity",
        )
    job.prompt_id = prompt_id
    attempt.prompt_id = prompt_id


async def mark_gpu_started(session: AsyncSession, job: Job) -> None:
    attempt = await current_job_attempt(session, job, lock=True)
    if attempt.gpu_started_at is None:
        attempt.gpu_started_at = datetime.now(UTC)
    if job.status == JobStatus.SUBMITTED.value:
        await transition_job(session, job, JobStatus.RUNNING, "executor.running")


async def mark_gpu_finished(session: AsyncSession, job: Job) -> None:
    attempt = await current_job_attempt(session, job, lock=True)
    now = datetime.now(UTC)
    if job.status == JobStatus.SUBMITTED.value:
        await transition_job(session, job, JobStatus.RUNNING, "executor.running_recovered")
    if attempt.gpu_finished_at is None:
        # A completion recovered without a start event is useful evidence, but
        # it is not a zero-duration GPU sample.  Preserve the missing start so
        # parent/node performance stays explicitly incomplete instead of
        # fabricating service time.
        attempt.gpu_finished_at = now


async def fail_closed_prompt_submission(
    session: AsyncSession, job: Job, error: ComfyError
) -> None:
    job.error_code = error.code
    job.error_message = str(error)[:1000]
    if JobStatus(job.status) not in TERMINAL_JOB_STATUSES:
        await transition_job(
            session,
            job,
            JobStatus.FAILED,
            "executor.submission_fail_closed",
            {"error_code": error.code, **error.details},
        )
        await release_lease(
            session,
            job,
            attempt_status=JobStatus.FAILED,
            attempt_error={"code": error.code, "message": job.error_message},
        )


def monotonic_job_progress(current: float, value: float, maximum: float) -> float:
    """Convert a ComfyUI node progress event without moving a job backwards."""
    denominator = max(maximum, 1.0)
    reported = min(99.0, max(0.0, value / denominator * 100.0))
    return max(current, reported)


def monotonic_batch_progress(
    current: float,
    total_items: int,
    terminal_items: int,
    active_progress: float,
) -> float:
    """Aggregate child progress while preserving the public monotonic contract."""
    computed = min(
        100.0,
        (terminal_items + max(0.0, active_progress)) / max(total_items, 1) * 100.0,
    )
    return max(current, computed)


def event_loop_wake_delay(
    wait_started_at: float,
    timeout_seconds: float,
    woke_at: float,
) -> float:
    """Measure only the latest wait's deadline overshoot, never historical drift."""

    wake_deadline = wait_started_at + max(0.0, timeout_seconds)
    return max(0.0, woke_at - wake_deadline)


async def latest_output_artifacts(
    session: AsyncSession,
    job_ids: list[str],
) -> dict[str, JobArtifact]:
    """Load the newest output artifact for every requested job in one query."""

    unique_job_ids = list(dict.fromkeys(job_ids))
    if not unique_job_ids:
        return {}
    ranked = (
        select(
            JobArtifact.id.label("artifact_id"),
            func.row_number()
            .over(
                partition_by=JobArtifact.job_id,
                order_by=(JobArtifact.created_at.desc(), JobArtifact.id.desc()),
            )
            .label("artifact_rank"),
        )
        .where(
            JobArtifact.job_id.in_(unique_job_ids),
            JobArtifact.kind == "output",
        )
        .subquery()
    )
    artifacts = list(
        (
            await session.scalars(
                select(JobArtifact)
                .join(ranked, ranked.c.artifact_id == JobArtifact.id)
                .where(ranked.c.artifact_rank == 1)
            )
        ).all()
    )
    return {artifact.job_id: artifact for artifact in artifacts}


async def queued_job_counts(session: AsyncSession) -> tuple[int, dict[str, int]]:
    rows = (
        await session.execute(
            select(Job.tenant_id, func.count(Job.id))
            .where(Job.status == JobStatus.QUEUED.value)
            .group_by(Job.tenant_id)
        )
    ).all()
    by_tenant = {str(tenant_id): int(count) for tenant_id, count in rows}
    return sum(by_tenant.values()), by_tenant


async def batch_in_window_counts(
    session: AsyncSession,
    batch_ids: list[str],
) -> dict[str, int]:
    if not batch_ids:
        return {}
    rows = (
        await session.execute(
            select(JobBatchItem.batch_id, func.count(JobBatchItem.id))
            .where(
                JobBatchItem.batch_id.in_(batch_ids),
                JobBatchItem.status.in_(
                    [
                        BatchItemStatus.QUEUED.value,
                        BatchItemStatus.RUNNING.value,
                    ]
                ),
            )
            .group_by(JobBatchItem.batch_id)
        )
    ).all()
    return {str(batch_id): int(count) for batch_id, count in rows}


def materialization_tick_budget(
    system_remaining: int,
    batch_feed_window: int,
    active_node_count: int,
) -> int:
    """Bound synchronous file preparation while keeping one feed window per active node."""

    topology_budget = batch_feed_window * max(1, active_node_count)
    return max(
        0,
        min(system_remaining, topology_budget, MAX_BATCH_MATERIALIZATIONS_PER_TICK),
    )


def round_robin_feed_allocations(
    batch_order: list[tuple[str, str]],
    batch_capacities: dict[str, int],
    tenant_remaining: dict[str, int],
    total_budget: int,
) -> dict[str, int]:
    """Allocate an exact bounded feed budget without letting one batch monopolize it."""

    remaining_by_tenant = dict(tenant_remaining)
    allocations = {batch_id: 0 for batch_id, _tenant_id in batch_order}
    remaining_total = max(0, total_budget)
    while remaining_total > 0:
        allocated_in_pass = False
        for batch_id, tenant_id in batch_order:
            if remaining_total == 0:
                break
            if allocations[batch_id] >= max(0, batch_capacities.get(batch_id, 0)):
                continue
            if remaining_by_tenant.get(tenant_id, 0) <= 0:
                continue
            allocations[batch_id] += 1
            remaining_by_tenant[tenant_id] -= 1
            remaining_total -= 1
            allocated_in_pass = True
        if not allocated_in_pass:
            break
    return {batch_id: count for batch_id, count in allocations.items() if count > 0}


def bounded_pending_items_statement(
    allocations: dict[str, int],
) -> Select[tuple[JobBatchItem]]:
    """Lock no more pending rows per batch than the exact round-robin allocation."""

    if not allocations:
        raise ValueError("pending allocations must not be empty")
    ranked_pending = (
        select(
            JobBatchItem.id.label("item_id"),
            JobBatchItem.batch_id.label("batch_id"),
            func.row_number()
            .over(
                partition_by=JobBatchItem.batch_id,
                order_by=JobBatchItem.ordinal,
            )
            .label("item_rank"),
        )
        .where(
            JobBatchItem.batch_id.in_(allocations),
            JobBatchItem.status == BatchItemStatus.PENDING.value,
        )
        .subquery()
    )
    per_batch_limit = case(
        allocations,
        value=ranked_pending.c.batch_id,
        else_=0,
    )
    return (
        select(JobBatchItem)
        .join(ranked_pending, ranked_pending.c.item_id == JobBatchItem.id)
        .where(ranked_pending.c.item_rank <= per_batch_limit)
        .order_by(JobBatchItem.batch_id, JobBatchItem.ordinal)
        .with_for_update(skip_locked=True, of=JobBatchItem)
    )


class Scheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.storage = LocalJobStorage(settings.job_root)
        self.stop_event = asyncio.Event()
        self.wakeup = asyncio.Event()
        self.executions: dict[str, asyncio.Task[None]] = {}
        self.batch_assemblies: dict[str, asyncio.Task[None]] = {}
        self.health_task: asyncio.Task[None] | None = None
        self.redis_task: asyncio.Task[None] | None = None
        self.callback_task: asyncio.Task[None] | None = None
        self.event_loop_lag_task: asyncio.Task[None] | None = None
        self.scheduler_lock_task: asyncio.Task[None] | None = None
        self.scheduler_lock_failure: SchedulerLockLost | None = None
        self.scheduler_lock_handle: SchedulerLockHandle | None = None
        self.scheduler_epoch: int | None = None
        self.object_info_checked_at: dict[str, float] = {}
        # GPU metrics are optional scheduling telemetry.  A WSL/Windows host can
        # temporarily serialize ``nvidia-smi`` while Substance or ComfyUI owns
        # the adapter.  Do not turn that into a 3-second request on every
        # five-second health pass: ComfyUI's system_stats remains the fallback
        # source for VRAM while this bounded retry window is active.
        self.gpu_metrics_retry_at: dict[str, float] = {}

    async def guard(self, session: AsyncSession) -> OverflowGuard:
        keys = {
            "overflow_queue_threshold",
            "overflow_wait_threshold_seconds",
            "overflow_4090_max_gpu_util_percent",
            "overflow_4090_min_free_vram_mb",
            "overflow_4090_auto_enabled",
            "overflow_4090_allowed_windows",
        }
        stored = {
            item.key: item.value.get("value")
            for item in (
                await session.scalars(select(SystemSetting).where(SystemSetting.key.in_(keys)))
            ).all()
        }
        effective = self.settings.model_copy(update=stored)
        return OverflowGuard(
            queue_threshold=effective.overflow_queue_threshold,
            wait_threshold_seconds=effective.overflow_wait_threshold_seconds,
            max_gpu_util_percent=effective.overflow_4090_max_gpu_util_percent,
            min_free_vram_mb=effective.overflow_4090_min_free_vram_mb,
            sentinel=effective.overflow_4090_sentinel,
            auto_enabled=effective.overflow_4090_auto_enabled,
            allowed_windows=effective.overflow_windows,
        )

    async def publish(self, payload: dict[str, Any]) -> None:
        try:
            await self.redis.publish("gpu-control:events", json.dumps(payload))
        except Exception as exc:
            logger().warning(
                "redis.publish_failed",
                error_code="REDIS_UNAVAILABLE",
                error_type=type(exc).__name__,
            )

    async def redis_listener(self) -> None:
        while not self.stop_event.is_set():
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe("gpu-control:wakeup")
                    while not self.stop_event.is_set():
                        message = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1
                        )
                        if message:
                            self.wakeup.set()
            except Exception as exc:
                logger().warning(
                    "redis.listen_failed",
                    error_code="REDIS_UNAVAILABLE",
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(2)

    async def callback_target_is_public(self, url: str) -> bool:
        host = urlparse(url).hostname
        if not host:
            return False
        try:
            records = await asyncio.wait_for(
                asyncio.to_thread(
                    socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM
                ),
                timeout=CALLBACK_DNS_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError):
            return False
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global:
                return False
        return bool(records)

    async def finalize_callback_delivery(
        self,
        *,
        callback_id: str,
        lease_deadline: datetime,
        attempt_number: int,
        status_code: int | None,
        error_code: str | None,
        duration_ms: int,
    ) -> bool:
        """Persist a callback result only for the exact live delivery lease."""

        metric_result: str
        async with self.db.session() as session:
            # Epoch -> callback row is the global lock order. A late result
            # from an old leader cannot mutate the callback after takeover.
            await self.assert_scheduler_epoch(session)
            current = await session.get(JobCallback, callback_id, with_for_update=True)
            if current is None:
                return False
            current_deadline = current.next_attempt_at
            if current_deadline is not None and current_deadline.tzinfo is None:
                current_deadline = current_deadline.replace(tzinfo=UTC)
            if current.status != "DELIVERING" or current_deadline != lease_deadline:
                logger().warning(
                    "callback.stale_result_ignored",
                    callback_id=callback_id,
                    status=current.status,
                    attempt=attempt_number,
                )
                await session.rollback()
                return False
            session.add(
                CallbackAttempt(
                    callback_id=current.id,
                    attempt=attempt_number,
                    response_status=status_code,
                    error_code=error_code,
                    duration_ms=duration_ms,
                )
            )
            if error_code is None:
                current.status = "SUCCEEDED"
                current.next_attempt_at = None
                metric_result = "success"
            elif attempt_number >= 6:
                current.status = "FAILED"
                current.next_attempt_at = None
                metric_result = "failed"
            else:
                delays = (10, 60, 300, 1800, 7200)
                current.status = "RETRY"
                current.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=delays[min(attempt_number - 1, len(delays) - 1)]
                )
                metric_result = "retry"
            await self.commit_as_leader(session)

        CALLBACK_ATTEMPTS.labels(metric_result).inc()
        if metric_result == "failed":
            CALLBACK_FAILURES.inc()
        return True

    async def dispatch_one_callback(self) -> bool:
        now = datetime.now(UTC)
        lease_deadline = now + timedelta(seconds=CALLBACK_DELIVERY_LEASE_SECONDS)
        async with self.db.session() as session:
            await self.assert_scheduler_epoch(session)
            callback = await session.scalar(
                select(JobCallback)
                .join(Job, Job.id == JobCallback.job_id)
                .where(
                    or_(
                        and_(
                            JobCallback.status.in_(["PENDING", "RETRY"]),
                            JobCallback.next_attempt_at <= now,
                        ),
                        and_(
                            JobCallback.status == "DELIVERING",
                            or_(
                                JobCallback.next_attempt_at.is_(None),
                                JobCallback.next_attempt_at <= now,
                            ),
                        ),
                    ),
                    Job.status.in_([status.value for status in TERMINAL_JOB_STATUSES]),
                )
                .order_by(JobCallback.next_attempt_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if callback is None:
                return False
            callback.status = "DELIVERING"
            # While DELIVERING, next_attempt_at is the durable lease deadline
            # and exact ownership token. It returns to retry scheduling only
            # after finalization or lease expiry.
            callback.next_attempt_at = lease_deadline
            job = await session.get(Job, callback.job_id)
            attempts = int(
                await session.scalar(
                    select(func.count(CallbackAttempt.id)).where(
                        CallbackAttempt.callback_id == callback.id
                    )
                )
                or 0
            )
            attempt_number = attempts + 1
            callback_id = callback.id
            callback_url = callback.url
            await self.commit_as_leader(session)

        if job is None:
            return False
        payload = {
            "event": f"job.{job.status.lower()}",
            "job_id": job.id,
            "status": job.status,
            "request_id": job.request_id,
            "trace_id": job.trace_id,
            "node_id": job.node_id,
            "prompt_id": job.prompt_id,
            "error": {"code": job.error_code, "message": job.error_message}
            if job.error_code
            else None,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        timestamp = str(int(now.timestamp()))
        secret = derive_callback_secret(callback_id, self.settings.api_key_pepper)
        headers = {
            "Content-Type": "application/json",
            "X-GPU-Control-Timestamp": timestamp,
            "X-GPU-Control-Signature": sign_callback_payload(body, timestamp, secret),
            "X-Request-ID": job.request_id,
            # Delivery is explicitly at-least-once. Receivers must persist this
            # attempt-scoped key and return the prior result for an ambiguous
            # replay of that same attempt.
            "Idempotency-Key": (
                f"gpu-control-callback:{callback_id}:{attempt_number}"
            ),
            "X-GPU-Control-Callback-ID": callback_id,
            "X-GPU-Control-Attempt": str(attempt_number),
        }
        status_code: int | None = None
        error_code: str | None = None
        delivery_unknown = False
        started = asyncio.get_running_loop().time()
        try:
            # Hold the epoch key-share lock across DNS + HTTP. A new leader's
            # epoch advance cannot start a concurrent replay while this POST
            # has a known live owner.
            async with self.db.session() as fence_session:
                await self.assert_scheduler_epoch(fence_session)
                if not await self.callback_target_is_public(callback_url):
                    raise RuntimeError(
                        "callback target did not resolve exclusively to public addresses"
                    )
                async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                    response = await client.post(
                        callback_url,
                        content=body,
                        headers=headers,
                    )
                    status_code = response.status_code
                    response.raise_for_status()
        except SchedulerLockLost:
            # This owner is fenced. Leave the durable lease untouched so a
            # new leader retries the same attempt and Idempotency-Key only
            # after the lease deadline.
            raise
        except httpx.TransportError as exc:
            # A timeout/transport break may happen after the receiver applied
            # the request. Do not consume an attempt whose outcome is unknown.
            delivery_unknown = True
            logger().warning(
                "callback.delivery_unknown",
                job_id=job.id,
                error_code="CALLBACK_DELIVERY_UNKNOWN",
                error_type=type(exc).__name__,
                attempt=attempt_number,
                callback_id=callback_id,
            )
        except Exception as exc:
            error_code = "CALLBACK_DELIVERY_FAILED"
            logger().warning(
                "callback.failed",
                job_id=job.id,
                error_code=error_code,
                error_type=type(exc).__name__,
                attempt=attempt_number,
            )
        duration_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        if delivery_unknown:
            return True
        await self.finalize_callback_delivery(
            callback_id=callback_id,
            lease_deadline=lease_deadline,
            attempt_number=attempt_number,
            status_code=status_code,
            error_code=error_code,
            duration_ms=duration_ms,
        )
        return True

    async def callback_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                dispatched = await self.dispatch_one_callback()
            except Exception:
                logger().exception(
                    "callback.dispatcher_failed",
                    error_code="CALLBACK_INTERNAL_ERROR",
                )
                dispatched = False
            if not dispatched:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=1)
                except TimeoutError:
                    continue

    async def node_agent_identity(self, node: Node) -> dict[str, Any] | None:
        if not node.id.startswith("worker-") or not node.agent_url:
            return None
        path = "/v1/identity"
        timestamp = str(int(datetime.now(UTC).timestamp()))
        nonce = uuid.uuid4().hex
        signature = sign_agent_request(
            "GET",
            path,
            b"",
            timestamp,
            nonce,
            self.settings.node_agent_secret(node.id),
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2)) as client:
            response = await client.get(
                f"{node.agent_url.rstrip('/')}{path}",
                headers={
                    "X-GPU-Timestamp": timestamp,
                    "X-GPU-Nonce": nonce,
                    "X-GPU-Signature": signature,
                },
            )
            response.raise_for_status()
            raw_identity = response.json()
            if not isinstance(raw_identity, dict):
                raise RuntimeError("invalid node agent identity response")
            identity = cast(dict[str, Any], raw_identity)
        if identity.get("node_id") != node.id:
            raise RuntimeError("node agent identity mismatch")
        labels = node.labels or {}
        for key in ("mac", "gpu_uuid"):
            expected = str(labels.get(key, ""))
            reported = str(identity.get(key, ""))
            if expected and expected.lower() != reported.lower():
                raise RuntimeError(f"node agent {key} mismatch")
        return identity

    async def node_agent_gpu_metrics(self, node: Node) -> dict[str, Any] | None:
        if not node.agent_url:
            return None
        now_monotonic = asyncio.get_running_loop().time()
        if now_monotonic < self.gpu_metrics_retry_at.get(node.id, 0):
            return None
        path = "/v1/gpu-metrics"
        timestamp = str(int(datetime.now(UTC).timestamp()))
        nonce = uuid.uuid4().hex
        signature = sign_agent_request(
            "GET",
            path,
            b"",
            timestamp,
            nonce,
            self.settings.node_agent_secret(node.id),
        )
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2)) as client:
                response = await client.get(
                    f"{node.agent_url.rstrip('/')}{path}",
                    headers={
                        "X-GPU-Timestamp": timestamp,
                        "X-GPU-Nonce": nonce,
                        "X-GPU-Signature": signature,
                    },
                )
                response.raise_for_status()
            payload = response.json()
            self.gpu_metrics_retry_at.pop(node.id, None)
            return {
                "gpu_util_percent": float(payload["gpu_util_percent"]),
                "free_vram_mb": int(payload["free_vram_mb"]),
                "total_vram_mb": int(payload["total_vram_mb"]),
                "gpu_temperature_c": (
                    float(payload["gpu_temperature_c"])
                    if payload.get("gpu_temperature_c") is not None
                    else None
                ),
                "gpu_power_w": (
                    float(payload["gpu_power_w"])
                    if payload.get("gpu_power_w") is not None
                    else None
                ),
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            self.gpu_metrics_retry_at[node.id] = (
                asyncio.get_running_loop().time() + 30.0
            )
            logger().warning(
                "node.gpu_metrics_failed",
                node_id=node.id,
                error_type=type(exc).__name__,
            )
            return None

    async def update_node_health(self) -> None:
        while not self.stop_event.is_set():
            # Keep probe ORM objects read-only. Health probes perform external
            # I/O, so committing this long-lived session would write stale
            # JSON labels over a concurrent Asset API fence/reservation.
            async with self.db.session() as probe_session:
                nodes = list((await probe_session.scalars(select(Node))).all())
                workflow_versions = list(
                    (await probe_session.scalars(select(WorkflowVersion))).all()
                )
                required_classes = {
                    class_type
                    for version in workflow_versions
                    for class_type in version.allowed_class_types
                }
                # Detach the read snapshots and end the transaction before
                # network I/O. Writers can now commit a reservation while a
                # slow health probe is in flight; the later locked write will
                # observe and merge that committed state.
                probe_session.expunge_all()
                await probe_session.rollback()
                for node in nodes:
                    try:
                        now_monotonic = asyncio.get_running_loop().time()
                        refresh_inventory = (
                            now_monotonic
                            - self.object_info_checked_at.get(node.id, 0)
                            >= 60
                            or not isinstance(
                                (node.labels or {}).get("comfy_class_types"), list
                            )
                        )
                        async with ComfyClient(
                            node.base_url, connect_timeout=2, read_timeout=5
                        ) as client:
                            if refresh_inventory:
                                stats, queue, _, gpu_metrics, inventory = await asyncio.gather(
                                    client.system_stats(),
                                    client.queue(),
                                    self.node_agent_identity(node),
                                    self.node_agent_gpu_metrics(node),
                                    client.object_info(),
                                )
                            else:
                                stats, queue, _, gpu_metrics = await asyncio.gather(
                                    client.system_stats(),
                                    client.queue(),
                                    self.node_agent_identity(node),
                                    self.node_agent_gpu_metrics(node),
                                )
                                inventory = None
                        # Timestamp the evidence when the network probe
                        # actually completed, before any wait on the Node row.
                        # A stale probe must never appear newer merely because
                        # a concurrent reservation held the write lock.
                        probe_completed_at = datetime.now(UTC)
                        foreign = False
                        for section in ("queue_running", "queue_pending"):
                            for item in queue.get(section, []):
                                metadata = (
                                    item[3]
                                    if isinstance(item, list)
                                    and len(item) > 3
                                    and isinstance(item[3], dict)
                                    else {}
                                )
                                client_id = str(metadata.get("client_id", ""))
                                if client_id and not client_id.startswith("gpu-control-"):
                                    foreign = True
                        devices = stats.get("devices", [])
                        # Re-read and lock the row only after all external I/O.
                        # The new session has no stale identity-map instance;
                        # labels are merged from the latest committed value.
                        async with self.db.session() as write_session:
                            async with write_session.begin():
                                await self.assert_scheduler_epoch(write_session)
                                current = await write_session.scalar(
                                    select(Node).where(Node.id == node.id).with_for_update()
                                )
                                if current is None:
                                    continue
                                current.foreign_queue_detected = foreign
                                current.health = (
                                    NodeHealth.DEGRADED.value
                                    if foreign
                                    else NodeHealth.ONLINE.value
                                )
                                current.last_heartbeat_at = probe_completed_at
                                labels = dict(current.labels or {})
                                if gpu_metrics is not None:
                                    current.gpu_util_percent = gpu_metrics[
                                        "gpu_util_percent"
                                    ]
                                    current.free_vram_mb = gpu_metrics["free_vram_mb"]
                                    current.total_vram_mb = gpu_metrics["total_vram_mb"]
                                    for label_key in ("gpu_temperature_c", "gpu_power_w"):
                                        value = gpu_metrics.get(label_key)
                                        if value is None:
                                            labels.pop(label_key, None)
                                        else:
                                            labels[label_key] = value
                                    labels["gpu_metrics_observed_at"] = (
                                        probe_completed_at.isoformat()
                                    )
                                else:
                                    labels.pop("gpu_temperature_c", None)
                                    labels.pop("gpu_power_w", None)
                                    labels.pop("gpu_metrics_observed_at", None)
                                    if devices:
                                        device = devices[0]
                                        current.free_vram_mb = int(
                                            device.get("vram_free", 0)
                                        ) // (1024 * 1024)
                                        current.total_vram_mb = int(
                                            device.get("vram_total", 0)
                                        ) // (1024 * 1024)
                                current.labels = labels
                                if isinstance(inventory, dict):
                                    labels["comfy_class_types"] = sorted(
                                        required_classes.intersection(inventory)
                                    )
                                    labels[
                                        "comfy_class_inventory_checked_at"
                                    ] = probe_completed_at.isoformat()
                                    current.labels = labels
                                    for version in workflow_versions:
                                        reasons = node_compatibility_reasons(
                                            min_vram_mb=version.min_vram_mb,
                                            required_labels=version.node_labels,
                                            allowed_class_types=version.allowed_class_types,
                                            total_vram_mb=current.total_vram_mb,
                                            reported_labels=labels,
                                        )
                                        compatibility = await write_session.scalar(
                                            select(WorkflowNodeCompatibility).where(
                                                WorkflowNodeCompatibility.workflow_version_id
                                                == version.id,
                                                WorkflowNodeCompatibility.node_id == current.id,
                                            )
                                        )
                                        if compatibility is None:
                                            write_session.add(
                                                WorkflowNodeCompatibility(
                                                    workflow_version_id=version.id,
                                                    node_id=current.id,
                                                    compatible=not reasons,
                                                    reasons=reasons,
                                                )
                                            )
                                        else:
                                            compatibility.compatible = not reasons
                                            compatibility.reasons = reasons
                                            compatibility.checked_at = probe_completed_at
                                metric_health = current.health
                                metric_jobs = current.current_jobs
                        if isinstance(inventory, dict):
                            self.object_info_checked_at[node.id] = now_monotonic
                        NODE_HEALTH.labels(node.id).set(
                            1 if metric_health == NodeHealth.ONLINE.value else 0
                        )
                        NODE_JOBS.labels(node.id).set(metric_jobs)
                    except Exception as exc:
                        # Failure writes use the same short locked transaction
                        # and deliberately touch health only, never labels or
                        # administrative mode.
                        async with self.db.session() as write_session:
                            async with write_session.begin():
                                await self.assert_scheduler_epoch(write_session)
                                current = await write_session.scalar(
                                    select(Node).where(Node.id == node.id).with_for_update()
                                )
                                if current is not None:
                                    current.health = NodeHealth.OFFLINE.value
                        NODE_HEALTH.labels(node.id).set(0)
                        logger().warning(
                            "node.health_failed",
                            node_id=node.id,
                            error_code="COMFY_HEALTH_FAILED",
                            error_type=type(exc).__name__,
                        )
            self.wakeup.set()
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=5)
            except TimeoutError:
                continue

    async def queue_snapshot(self, session: AsyncSession) -> QueueSnapshot:
        count, oldest = (
            await session.execute(
                select(func.count(Job.id), func.min(Job.created_at)).where(
                    Job.status == JobStatus.QUEUED.value
                )
            )
        ).one()
        wait = 0.0
        if oldest:
            stamped = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
            wait = max(0, (datetime.now(UTC) - stamped).total_seconds())
        QUEUED.set(int(count or 0))
        OLDEST.set(wait)
        return QueueSnapshot(depth=int(count or 0), oldest_wait_seconds=wait)

    def mark_scheduler_lock_lost(self, failure: SchedulerLockLost) -> None:
        """Fence this process and stop every source of scheduler-side writes."""

        if self.scheduler_lock_failure is None:
            self.scheduler_lock_failure = failure
        self.stop_event.set()
        self.wakeup.set()
        current = asyncio.current_task()
        tasks = [
            getattr(self, "health_task", None),
            getattr(self, "redis_task", None),
            getattr(self, "callback_task", None),
            getattr(self, "event_loop_lag_task", None),
            *getattr(self, "executions", {}).values(),
            *getattr(self, "batch_assemblies", {}).values(),
        ]
        for task in tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()

    async def establish_scheduler_epoch(self, handle: SchedulerLockHandle) -> int:
        """Advance the durable leader epoch before startup reconciliation."""

        self.scheduler_lock_handle = handle
        async with self.db.session() as session:
            async with session.begin():
                epoch = await self.db.advance_scheduler_epoch(
                    session,
                    backend_pid=handle.backend_pid,
                )
        self.scheduler_epoch = epoch
        return epoch

    async def assert_scheduler_epoch(self, session: AsyncSession) -> None:
        """Lock and validate the leader epoch for the caller's transaction.

        Direct unit/integration calls that do not enter ``run`` intentionally
        remain supported. In production, setting the lock handle without an
        epoch is always a fail-closed startup error.
        """

        if self.scheduler_lock_handle is None:
            return
        if self.scheduler_epoch is None:
            failure = SchedulerLockLost("scheduler leader epoch was not established")
            self.mark_scheduler_lock_lost(failure)
            raise failure
        try:
            await self.db.assert_scheduler_epoch(session, self.scheduler_epoch)
        except SchedulerLockLost as failure:
            self.mark_scheduler_lock_lost(failure)
            raise

    async def commit_as_leader(self, session: AsyncSession) -> None:
        """Fence the caller's current short transaction through commit.

        This deliberately does not reacquire after commit: GPU execution and
        artifact transfer can run for minutes, and an idle key-share
        transaction would block takeover and retain PostgreSQL vacuum state.
        Every later durable mutation must enter another fenced transaction.
        """

        await self.assert_scheduler_epoch(session)
        await session.commit()

    async def lock_job_as_leader(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> Job | None:
        """Acquire the global epoch before refreshing and locking one Job."""

        await self.assert_scheduler_epoch(session)
        return cast(
            Job | None,
            await session.scalar(
                select(Job)
                .where(Job.id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ),
        )

    async def cancel_locked_job(
        self,
        session: AsyncSession,
        job: Job,
        *,
        event: str,
    ) -> None:
        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
            return
        if job.status != JobStatus.CANCELLING.value:
            await transition_job(session, job, JobStatus.CANCELLING, f"{event}.requested")
        await transition_job(session, job, JobStatus.CANCELLED, event)
        await release_lease(session, job, attempt_status=JobStatus.CANCELLED)

    async def reconcile(self) -> None:
        async with self.db.session() as session:
            # The key-share lock is held through commit. A takeover must wait
            # for this reconciliation transaction before advancing the epoch.
            await self.assert_scheduler_epoch(session)
            delivering_callbacks = list(
                (
                    await session.scalars(
                        select(JobCallback).where(
                            JobCallback.status == "DELIVERING",
                            or_(
                                JobCallback.next_attempt_at.is_(None),
                                JobCallback.next_attempt_at <= datetime.now(UTC),
                            ),
                        )
                    )
                ).all()
            )
            for callback in delivering_callbacks:
                callback.status = "RETRY"
                callback.next_attempt_at = datetime.now(UTC)
            jobs = list(
                (
                    await session.scalars(
                        select(Job).where(
                            Job.status.in_(
                                [
                                    JobStatus.CLAIMED.value,
                                    JobStatus.UPLOADING.value,
                                    JobStatus.SUBMITTED.value,
                                    JobStatus.RUNNING.value,
                                    JobStatus.DOWNLOADING.value,
                                    JobStatus.CANCELLING.value,
                                ]
                            )
                        )
                    )
                ).all()
            )
            for job in jobs:
                if job.node_id and (job.prompt_id or job.submission_intent_at):
                    logger().info(
                        "scheduler.recover_submitted",
                        job_id=job.id,
                        prompt_id=job.prompt_id,
                        submission_client_id=job.submission_client_id,
                        node_id=job.node_id,
                    )
                    self.executions[job.id] = asyncio.create_task(
                        self.execute(job.id, recovering=True)
                    )
                elif job.status in {JobStatus.CLAIMED.value, JobStatus.UPLOADING.value}:
                    await release_lease(
                        session,
                        job,
                        attempt_status=JobStatus.FAILED,
                        attempt_error={"code": "SCHEDULER_RECOVERY_PRE_SUBMIT"},
                    )
                    await transition_job(
                        session, job, JobStatus.RETRY_WAIT, "scheduler.recover_pre_submit"
                    )
                    await transition_job(session, job, JobStatus.QUEUED, "scheduler.requeued")
                    job.node_id = None
                    job.claimed_at = None
                    job.submission_client_id = None
                    job.submission_intent_at = None
                else:
                    # SUBMITTED/RUNNING without either a prompt id or a durable
                    # intent cannot be retried safely after a restart.
                    job.error_code = "COMFY_SUBMISSION_UNKNOWN"
                    job.error_message = "active job has no recoverable prompt identity"
                    await transition_job(
                        session,
                        job,
                        JobStatus.FAILED,
                        "scheduler.recovery_identity_missing",
                    )
                    await release_lease(
                        session,
                        job,
                        attempt_status=JobStatus.FAILED,
                        attempt_error={
                            "code": job.error_code,
                            "message": job.error_message,
                        },
                    )
            await self.commit_as_leader(session)

    async def reconcile_batches(self) -> None:
        active_statuses = {
            BatchStatus.QUEUED.value,
            BatchStatus.RUNNING.value,
            BatchStatus.CANCELLING.value,
            BatchStatus.ASSEMBLING.value,
        }
        async with self.db.session() as session:
            batch_ids = list(
                (
                    await session.scalars(
                        select(JobBatch.id)
                        .where(JobBatch.status.in_(active_statuses))
                        .order_by(JobBatch.created_at)
                    )
                ).all()
            )
        for batch_id in batch_ids:
            should_assemble = await self.sync_batch_state(batch_id)
            if should_assemble and batch_id not in self.batch_assemblies:
                self.batch_assemblies[batch_id] = asyncio.create_task(
                    self.run_batch_assembly(batch_id)
                )
        await self.feed_batch_items()

    async def run_batch_assembly(self, batch_id: str) -> None:
        try:
            await self.assemble_batch(batch_id)
        except asyncio.CancelledError:
            raise
        except SchedulerLockLost:
            raise
        except Exception:
            logger().exception(
                "batch.assembly_internal_error",
                batch_id=batch_id,
                error_code="BATCH_ASSEMBLY_INTERNAL_ERROR",
            )
            async with self.db.session() as session:
                async with session.begin():
                    await self.assert_scheduler_epoch(session)
                    batch = await session.get(JobBatch, batch_id, with_for_update=True)
                    if batch is not None and batch.status == BatchStatus.ASSEMBLING.value:
                        batch.error_code = "BATCH_ASSEMBLY_INTERNAL_ERROR"
                        batch.error_message = "结果归档发生内部错误"
                        await transition_batch(
                            session,
                            batch,
                            BatchStatus.FAILED,
                            "batch.assembly_internal_error",
                        )
        finally:
            self.batch_assemblies.pop(batch_id, None)
            self.wakeup.set()

    async def sync_batch_state(self, batch_id: str) -> bool:
        async with self.db.session() as session:
            await self.assert_scheduler_epoch(session)
            batch = await session.scalar(
                select(JobBatch).where(JobBatch.id == batch_id).with_for_update()
            )
            if batch is None:
                return False
            if batch.status == BatchStatus.ASSEMBLING.value:
                return True
            previous_progress = batch.progress
            previous_progress_counts = (
                batch.running_items,
                batch.succeeded_items,
                batch.failed_items,
                batch.cancelled_items,
            )
            items = list(
                (
                    await session.scalars(
                        select(JobBatchItem)
                        .where(JobBatchItem.batch_id == batch.id)
                        .order_by(JobBatchItem.ordinal)
                        .with_for_update()
                    )
                ).all()
            )
            job_ids = [item.job_id for item in items if item.job_id]
            jobs = list(
                (
                    await session.scalars(select(Job).where(Job.id.in_(job_ids)))
                ).all()
            ) if job_ids else []
            jobs_by_id = {job.id: job for job in jobs}
            artifacts_by_job = await latest_output_artifacts(
                session,
                [job.id for job in jobs if job.status == JobStatus.SUCCEEDED.value],
            )
            failure_item: JobBatchItem | None = None
            active_progress = 0.0
            for item in items:
                if not item.job_id:
                    continue
                job = jobs_by_id.get(item.job_id)
                if job is None:
                    item.status = BatchItemStatus.FAILED.value
                    item.error_code = "CHILD_JOB_MISSING"
                    item.error_message = "批次帧对应的内部任务不存在"
                    failure_item = failure_item or item
                    continue
                item.node_id = job.node_id
                item.attempts = job.attempt_count
                item.updated_at = datetime.now(UTC)
                if job.status == JobStatus.SUCCEEDED.value:
                    artifact = artifacts_by_job.get(job.id)
                    if artifact is None:
                        item.status = BatchItemStatus.FAILED.value
                        item.error_code = "OUTPUT_MISSING"
                        item.error_message = "内部任务成功但没有输出图片"
                        failure_item = failure_item or item
                    else:
                        item.status = BatchItemStatus.SUCCEEDED.value
                        item.output_size_bytes = artifact.size_bytes
                        item.output_sha256 = artifact.sha256
                        item.error_code = None
                        item.error_message = None
                elif job.status in {JobStatus.FAILED.value, JobStatus.TIMED_OUT.value}:
                    if (
                        not batch.cancel_requested
                        and job.attempt_count < job.max_attempts
                        and job.error_code not in FAIL_CLOSED_SUBMISSION_ERRORS
                        and (
                            job.prompt_id is None
                            or job.error_code == "COMFY_EXECUTION_ERROR"
                        )
                        and batch.status
                        not in {BatchStatus.CANCELLING.value, BatchStatus.ASSEMBLING.value}
                    ):
                        previous_error = {
                            "code": job.error_code,
                            "message": job.error_message,
                        }
                        await transition_job(
                            session,
                            job,
                            JobStatus.RETRY_WAIT,
                            "batch.item_retry_wait",
                            {"previous_error": previous_error},
                        )
                        await transition_job(
                            session, job, JobStatus.QUEUED, "batch.item_retry_queued"
                        )
                        job.node_id = None
                        job.prompt_id = None
                        job.submission_client_id = None
                        job.submission_intent_at = None
                        job.progress = 0
                        job.cancel_requested = False
                        job.claimed_at = None
                        job.started_at = None
                        job.finished_at = None
                        job.not_before = None
                        job.error_code = None
                        job.error_message = None
                        item.status = BatchItemStatus.QUEUED.value
                    else:
                        item.status = BatchItemStatus.FAILED.value
                        item.error_code = job.error_code or "CHILD_JOB_FAILED"
                        item.error_message = job.error_message or "内部任务执行失败"
                        failure_item = failure_item or item
                elif job.status == JobStatus.CANCELLED.value:
                    item.status = BatchItemStatus.CANCELLED.value
                elif job.status == JobStatus.QUEUED.value:
                    item.status = BatchItemStatus.QUEUED.value
                else:
                    item.status = BatchItemStatus.RUNNING.value
                    active_progress += max(0.0, min(job.progress, 99.0)) / 100

            # A child failure is not a user cancellation.  Keep feeding and
            # processing the other frames so the failure remains isolated and
            # observable.  The all-or-nothing contract is enforced only at the
            # final parent transition: any exhausted child makes the parent
            # FAILED and no result archive is published.
            if failure_item is not None and not batch.cancel_requested:
                if not batch.error_code:
                    batch.error_code = failure_item.error_code
                    batch.error_message = (
                        f"帧 {failure_item.ordinal} {failure_item.input_relative_path}: "
                        f"{failure_item.error_message}"
                    )[:1000]
                    await transition_batch(
                        session,
                        batch,
                        BatchStatus(batch.status),
                        "batch.item_failed_continuing",
                        {
                            "ordinal": failure_item.ordinal,
                            "error_code": failure_item.error_code,
                        },
                    )

            if batch.cancel_requested:
                if batch.status != BatchStatus.CANCELLING.value:
                    await transition_batch(
                        session,
                        batch,
                        BatchStatus.CANCELLING,
                        "batch.cancelling",
                    )
                for item in items:
                    if not item.job_id and item.status == BatchItemStatus.PENDING.value:
                        item.status = BatchItemStatus.CANCELLED.value
                        continue
                    job = jobs_by_id.get(item.job_id or "")
                    if job is None or JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                        continue
                    if job.status == JobStatus.QUEUED.value:
                        await transition_job(
                            session, job, JobStatus.CANCELLED, "batch.cancelled_before_claim"
                        )
                        item.status = BatchItemStatus.CANCELLED.value
                    else:
                        job.cancel_requested = True
                        if job.status != JobStatus.CANCELLING.value:
                            await transition_job(
                                session, job, JobStatus.CANCELLING, "batch.cancel_requested"
                            )

            status_counts = {
                status.value: sum(item.status == status.value for item in items)
                for status in BatchItemStatus
            }
            batch.pending_items = status_counts[BatchItemStatus.PENDING.value]
            batch.queued_items = status_counts[BatchItemStatus.QUEUED.value]
            batch.running_items = status_counts[BatchItemStatus.RUNNING.value]
            batch.succeeded_items = status_counts[BatchItemStatus.SUCCEEDED.value]
            batch.failed_items = status_counts[BatchItemStatus.FAILED.value]
            batch.cancelled_items = status_counts[BatchItemStatus.CANCELLED.value]
            batch.progress = monotonic_batch_progress(
                batch.progress,
                batch.total_items,
                batch.succeeded_items + batch.failed_items + batch.cancelled_items,
                active_progress,
            )
            first_gpu_started = None
            last_gpu_finished = None
            gpu_started_attempts = 0
            gpu_finished_attempts = 0
            if job_ids:
                timing = (
                    await session.execute(
                        select(
                            func.min(JobAttempt.gpu_started_at),
                            func.max(JobAttempt.gpu_finished_at),
                            func.count(JobAttempt.gpu_started_at),
                            func.count(JobAttempt.gpu_finished_at),
                        ).where(JobAttempt.job_id.in_(job_ids))
                    )
                ).one()
                (
                    first_gpu_started,
                    last_gpu_finished,
                    gpu_started_attempts,
                    gpu_finished_attempts,
                ) = timing
            if batch.started_at is None and first_gpu_started is not None:
                batch.started_at = first_gpu_started
            progress_counts = (
                batch.running_items,
                batch.succeeded_items,
                batch.failed_items,
                batch.cancelled_items,
            )
            if (
                batch.progress > previous_progress
                or progress_counts != previous_progress_counts
            ):
                batch.last_progress_at = datetime.now(UTC)
            terminal_count = (
                batch.succeeded_items + batch.failed_items + batch.cancelled_items
            )
            if (
                terminal_count == batch.total_items
                and batch.execution_finished_at is None
                and last_gpu_finished is not None
                and gpu_started_attempts > 0
                and gpu_started_attempts == gpu_finished_attempts
            ):
                batch.execution_finished_at = last_gpu_finished
            if (
                terminal_count == batch.total_items
                and batch.failed_items
                and not batch.cancel_requested
            ):
                await transition_batch(
                    session,
                    batch,
                    BatchStatus.FAILED,
                    "batch.failed_after_all_items",
                    {"failed_items": batch.failed_items},
                )
                await self.commit_as_leader(session)
                return False
            if batch.status == BatchStatus.CANCELLING.value and terminal_count == batch.total_items:
                operation = await session.scalar(
                    select(BatchCancelOperation)
                    .where(BatchCancelOperation.batch_id == batch.id)
                    .with_for_update()
                )
                valid_cancel_audit = bool(
                    operation is not None
                    and operation.tenant_id == batch.tenant_id
                    and operation.status == "REQUESTED"
                    and operation.request_id.strip()
                    and operation.requested_by.strip()
                    and operation.source in {"public_api", "admin_api"}
                    and operation.reason.strip()
                    and operation.idempotency_key.strip()
                )
                if not batch.cancel_requested or not valid_cancel_audit:
                    batch.error_code = "CANCEL_AUDIT_MISSING"
                    batch.error_message = (
                        "取消终态缺少完整且有效的 BatchCancelOperation 审计记录"
                    )
                    await transition_batch(
                        session,
                        batch,
                        BatchStatus.FAILED,
                        "batch.cancel_audit_missing",
                        {"cancel_operation_id": operation.id if operation else None},
                    )
                else:
                    await transition_batch(
                        session, batch, BatchStatus.CANCELLED, "batch.cancelled"
                    )
                    assert operation is not None
                    operation.status = "COMPLETED"
                    if operation.finished_at is None:
                        operation.finished_at = datetime.now(UTC)
                    operation.completed_items = batch.succeeded_items + batch.failed_items
                    operation.cancelled_items = batch.cancelled_items
                await self.commit_as_leader(session)
                return False
            if batch.succeeded_items == batch.total_items:
                if batch.status == BatchStatus.QUEUED.value:
                    await transition_batch(
                        session, batch, BatchStatus.RUNNING, "batch.running"
                    )
                await transition_batch(
                    session, batch, BatchStatus.ASSEMBLING, "batch.assembling"
                )
                await self.commit_as_leader(session)
                return True
            if (
                batch.status == BatchStatus.QUEUED.value
                and batch.started_at is not None
            ):
                await transition_batch(session, batch, BatchStatus.RUNNING, "batch.running")
            await self.commit_as_leader(session)
            return False

    async def feed_batch_items(self) -> None:
        materialized_jobs: list[tuple[str, Path]] = []
        try:
            await self._feed_batch_items_transaction(materialized_jobs)
        except BaseException:
            try:
                await self._reconcile_materialized_job_roots(materialized_jobs)
            except BaseException:
                logger().warning(
                    "batch.materialization_cleanup_deferred",
                    reason="persistence_check_interrupted",
                    job_ids=[job_id for job_id, _root in materialized_jobs],
                )
            raise

    async def _reconcile_materialized_job_roots(
        self,
        materialized_jobs: list[tuple[str, Path]],
    ) -> None:
        if not materialized_jobs:
            return
        job_ids = [job_id for job_id, _root in materialized_jobs]
        try:
            async with self.db.session() as session:
                persisted_ids = set(
                    (
                        await session.scalars(select(Job.id).where(Job.id.in_(job_ids)))
                    ).all()
                )
        except Exception:
            logger().warning(
                "batch.materialization_cleanup_deferred",
                reason="persistence_check_failed",
                job_ids=job_ids,
            )
            return
        if persisted_ids:
            logger().warning(
                "batch.materialization_cleanup_deferred",
                reason="jobs_may_have_committed",
                job_ids=job_ids,
                persisted_job_ids=sorted(persisted_ids),
            )
            return
        for _job_id, job_root in materialized_jobs:
            self.storage.remove_tree(job_root)
        logger().warning(
            "batch.materialization_rollback_cleaned",
            job_ids=job_ids,
        )

    async def _feed_batch_items_transaction(
        self,
        materialized_jobs: list[tuple[str, Path]],
    ) -> None:
        async with self.db.session() as session:
            await self.assert_scheduler_epoch(session)
            # Discovery intentionally does not lock every active batch.  Exact
            # allocations are calculated first, then only those batch rows are
            # locked and revalidated below.
            batches = list(
                (
                    await session.scalars(
                        select(JobBatch)
                        .where(
                            JobBatch.status.in_(
                                [BatchStatus.QUEUED.value, BatchStatus.RUNNING.value]
                            ),
                            JobBatch.cancel_requested.is_(False),
                            JobBatch.pending_items > 0,
                        )
                        .order_by(
                            JobBatch.last_materialized_at.asc().nullsfirst(),
                            JobBatch.created_at,
                            JobBatch.id,
                        )
                    )
                ).all()
            )
            if not batches:
                return

            await self.db.acquire_global_admission_transaction_lock(session)
            tenant_ids = sorted({batch.tenant_id for batch in batches})
            clients = list(
                (await session.scalars(select(ApiClient).where(ApiClient.id.in_(tenant_ids)))).all()
            )
            clients_by_id = {client.id: client for client in clients}

            # Re-evaluate cross-plane production state only after taking the
            # same global lock used by API admission. A production request that
            # committed before this lock must stop all test-batch materializing
            # in this transaction. Missing client rows fail closed as
            # production, matching API admission and claim selection.
            production_active = await active_production_work_exists(session)
            if production_active:
                batches = [
                    batch
                    for batch in batches
                    if (clients_by_id.get(batch.tenant_id) is None)
                    or clients_by_id[batch.tenant_id].client_kind != "test"
                ]
                if not batches:
                    return

            queued_count, tenant_queued = await queued_job_counts(session)
            system_queue_limit = (
                self.settings.system_max_queued
                if production_active
                else self.settings.test_system_max_queued
            )
            system_remaining = max(0, system_queue_limit - queued_count)
            if system_remaining == 0:
                return

            tenant_ids = sorted({batch.tenant_id for batch in batches})
            clients_by_id = {
                tenant_id: clients_by_id[tenant_id]
                for tenant_id in tenant_ids
                if tenant_id in clients_by_id
            }
            batch_ids = [batch.id for batch in batches]
            in_window_by_batch = await batch_in_window_counts(session, batch_ids)
            active_node_count = int(
                await session.scalar(
                    select(func.count(Node.id)).where(
                        Node.health == NodeHealth.ONLINE.value,
                        Node.mode == NodeMode.ACTIVE.value,
                    )
                )
                or 0
            )

            tenant_remaining: dict[str, int] = {}
            for tenant_id in tenant_ids:
                client = clients_by_id.get(tenant_id)
                queue_limit = (
                    client.max_queued
                    if client is not None
                    else self.settings.default_tenant_max_queued
                )
                tenant_remaining[tenant_id] = max(
                    0,
                    queue_limit - tenant_queued.get(tenant_id, 0),
                )

            preliminary_capacities: dict[str, int] = {}
            for batch in batches:
                preliminary_capacities[batch.id] = min(
                    max(0, batch.pending_items),
                    max(
                        0,
                        self.settings.batch_feed_window
                        - in_window_by_batch.get(batch.id, 0),
                    ),
                )
            tick_budget = materialization_tick_budget(
                system_remaining,
                self.settings.batch_feed_window,
                active_node_count,
            )
            preliminary_allocations = round_robin_feed_allocations(
                [(batch.id, batch.tenant_id) for batch in batches],
                preliminary_capacities,
                tenant_remaining,
                tick_budget,
            )
            if not preliminary_allocations:
                return

            batch_by_id = {batch.id: batch for batch in batches}
            locked_tenant_ids = sorted(
                {
                    batch_by_id[batch_id].tenant_id
                    for batch_id in preliminary_allocations
                }
            )
            for tenant_id in locked_tenant_ids:
                await self.db.acquire_tenant_transaction_lock(session, tenant_id)

            # Re-count only after the global and sorted tenant locks are held;
            # the preliminary read is never used as an admission decision.
            queued_count, tenant_queued = await queued_job_counts(session)
            # The global lock remains held, so production cannot appear between
            # the state decision above and this final capacity check.
            system_remaining = max(0, system_queue_limit - queued_count)
            if system_remaining == 0:
                return
            clients = list(
                (
                    await session.scalars(
                        select(ApiClient)
                        .where(ApiClient.id.in_(locked_tenant_ids))
                        .execution_options(populate_existing=True)
                    )
                ).all()
            )
            clients_by_id = {client.id: client for client in clients}
            tenant_remaining = {}
            for tenant_id in locked_tenant_ids:
                client = clients_by_id.get(tenant_id)
                queue_limit = (
                    client.max_queued
                    if client is not None
                    else self.settings.default_tenant_max_queued
                )
                tenant_remaining[tenant_id] = max(
                    0,
                    queue_limit - tenant_queued.get(tenant_id, 0),
                )

            candidate_ids = list(preliminary_allocations)
            in_window_by_batch = await batch_in_window_counts(session, candidate_ids)
            candidate_order = [
                (batch.id, batch.tenant_id)
                for batch in batches
                if batch.id in preliminary_allocations
            ]
            candidate_capacities = {
                batch_id: min(
                    max(0, batch_by_id[batch_id].pending_items),
                    max(
                        0,
                        self.settings.batch_feed_window
                        - in_window_by_batch.get(batch_id, 0),
                    ),
                )
                for batch_id in candidate_ids
            }
            candidate_allocations = round_robin_feed_allocations(
                candidate_order,
                candidate_capacities,
                tenant_remaining,
                materialization_tick_budget(
                    system_remaining,
                    self.settings.batch_feed_window,
                    active_node_count,
                ),
            )
            if not candidate_allocations:
                return

            locked_batches = list(
                (
                    await session.scalars(
                        select(JobBatch)
                        .where(
                            JobBatch.id.in_(candidate_allocations),
                            JobBatch.status.in_(
                                [BatchStatus.QUEUED.value, BatchStatus.RUNNING.value]
                            ),
                            JobBatch.cancel_requested.is_(False),
                            JobBatch.pending_items > 0,
                        )
                        .order_by(
                            JobBatch.last_materialized_at.asc().nullsfirst(),
                            JobBatch.created_at,
                            JobBatch.id,
                        )
                        .with_for_update(skip_locked=True)
                        .execution_options(populate_existing=True)
                    )
                ).all()
            )
            if not locked_batches:
                return

            locked_batch_ids = [batch.id for batch in locked_batches]
            in_window_by_batch = await batch_in_window_counts(session, locked_batch_ids)
            workflow_keys = list(
                {
                    (batch.workflow_key, batch.workflow_version)
                    for batch in locked_batches
                }
            )
            workflows = list(
                (
                    await session.scalars(
                        select(WorkflowVersion).where(
                            tuple_(
                                WorkflowVersion.workflow_key,
                                WorkflowVersion.version,
                            ).in_(workflow_keys),
                            WorkflowVersion.enabled.is_(True),
                        )
                    )
                ).all()
            )
            workflows_by_key = {
                (workflow.workflow_key, workflow.version): workflow for workflow in workflows
            }

            eligible_batches: list[JobBatch] = []
            workflows_by_batch: dict[str, WorkflowVersion] = {}
            for batch in locked_batches:
                workflow = workflows_by_key.get((batch.workflow_key, batch.workflow_version))
                if workflow is None:
                    batch.error_code = "WORKFLOW_NOT_FOUND"
                    batch.error_message = "批次固定的工作流版本已停用或删除"
                    await transition_batch(
                        session, batch, BatchStatus.FAILED, "batch.workflow_missing"
                    )
                    continue
                try:
                    current_identity = workflow_identity_from_row(workflow)
                except BatchContractError as exc:
                    batch.error_code = exc.code
                    batch.error_message = str(exc)[:1000]
                    await transition_batch(
                        session,
                        batch,
                        BatchStatus.FAILED,
                        "batch.workflow_identity_invalid",
                    )
                    continue
                expected_identity = {
                    "workflow_key": batch.workflow_key,
                    "workflow_version": batch.workflow_version,
                    "pipeline_commit": batch.pipeline_commit,
                    "pipeline_sha256": batch.pipeline_sha256,
                    "output_node": batch.output_node,
                }
                if current_identity != expected_identity:
                    batch.error_code = "WORKFLOW_IDENTITY_DRIFT"
                    batch.error_message = (
                        "批次固定的工作流身份与当前工作流版本不一致，已闭锁且不创建子任务"
                    )
                    await transition_batch(
                        session,
                        batch,
                        BatchStatus.FAILED,
                        "batch.workflow_identity_drift",
                        {
                            "expected": expected_identity,
                            "actual": current_identity,
                        },
                    )
                    continue
                eligible_batches.append(batch)
                workflows_by_batch[batch.id] = workflow

            final_capacities = {
                batch.id: min(
                    max(0, batch.pending_items),
                    max(
                        0,
                        self.settings.batch_feed_window
                        - in_window_by_batch.get(batch.id, 0),
                    ),
                )
                for batch in eligible_batches
            }
            final_allocations = round_robin_feed_allocations(
                [(batch.id, batch.tenant_id) for batch in eligible_batches],
                final_capacities,
                tenant_remaining,
                materialization_tick_budget(
                    system_remaining,
                    self.settings.batch_feed_window,
                    active_node_count,
                ),
            )
            if not final_allocations:
                # Persist any fail-closed workflow transitions made above.
                await self.commit_as_leader(session)
                return

            pending_by_batch: dict[str, list[JobBatchItem]] = {
                batch.id: [] for batch in eligible_batches if batch.id in final_allocations
            }
            pending_items = list(
                (
                    await session.scalars(
                        bounded_pending_items_statement(final_allocations)
                    )
                ).all()
            )
            for item in pending_items:
                pending_by_batch[item.batch_id].append(item)

            batch_remaining = {
                batch_id: len(items) for batch_id, items in pending_by_batch.items()
            }

            # The persisted ordering rotates batches across scheduler loops.
            # Within this loop, repeated passes grant each eligible batch at
            # most one frame before any batch receives its next frame.
            item_offsets = {batch.id: 0 for batch in eligible_batches}
            while system_remaining > 0:
                materialized_in_pass = False
                for batch in eligible_batches:
                    if system_remaining == 0:
                        break
                    if batch.id not in final_allocations:
                        continue
                    if batch_remaining[batch.id] <= 0:
                        continue
                    if tenant_remaining.get(batch.tenant_id, 0) <= 0:
                        continue
                    offset = item_offsets[batch.id]
                    item = pending_by_batch[batch.id][offset]
                    job = await materialize_batch_item(
                        session,
                        self.storage,
                        self.settings,
                        batch,
                        item,
                        workflows_by_batch[batch.id],
                    )
                    materialized_jobs.append((job.id, Path(job.job_dir)))
                    item_offsets[batch.id] = offset + 1
                    batch_remaining[batch.id] -= 1
                    tenant_remaining[batch.tenant_id] -= 1
                    system_remaining -= 1
                    batch.pending_items = max(0, batch.pending_items - 1)
                    batch.queued_items += 1
                    materialized_in_pass = True
                if not materialized_in_pass:
                    break
            await self.commit_as_leader(session)
            materialized_jobs.clear()

    async def assemble_batch(self, batch_id: str) -> None:
        async with self.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            if batch is None or batch.status != BatchStatus.ASSEMBLING.value:
                return
            items = list(
                (
                    await session.scalars(
                        select(JobBatchItem)
                        .where(
                            JobBatchItem.batch_id == batch.id,
                            JobBatchItem.status == BatchItemStatus.SUCCEEDED.value,
                        )
                        .order_by(JobBatchItem.ordinal)
                    )
                ).all()
            )
            if len(items) != batch.total_items:
                return
            if any(not item.job_id or not item.output_sha256 for item in items):
                return
            job_ids = [str(item.job_id) for item in items]
            jobs = list((await session.scalars(select(Job).where(Job.id.in_(job_ids)))).all())
            jobs_by_id = {job.id: job for job in jobs}
            artifacts_by_job = await latest_output_artifacts(session, job_ids)
            frames: list[ArchiveFrame] = []
            for item in items:
                assert item.job_id is not None
                assert item.output_sha256 is not None
                job = jobs_by_id.get(item.job_id)
                artifact = artifacts_by_job.get(item.job_id)
                if job is None or artifact is None:
                    return
                frames.append(
                    ArchiveFrame(
                        ordinal=item.ordinal,
                        input_relative_path=item.input_relative_path,
                        output_relative_path=item.output_relative_path,
                        input_sha256=item.input_sha256,
                        output_path=Path(job.job_dir) / artifact.relative_path,
                        expected_output_sha256=item.output_sha256,
                        job_id=job.id,
                        node_id=item.node_id,
                        attempts=item.attempts,
                    )
                )
            external_batch_id = batch.external_batch_id
            batch_dir = Path(batch.batch_dir)
            workflow_identity = {
                "workflow_key": batch.workflow_key,
                "workflow_version": batch.workflow_version,
                "pipeline_commit": batch.pipeline_commit,
                "pipeline_sha256": batch.pipeline_sha256,
                "output_node": batch.output_node,
            }
        cleanup_result_archive_staging(batch_dir)
        staging_path = result_archive_staging_path(batch_dir, batch_id)
        cancel_event = Event()
        build_task = asyncio.create_task(
            asyncio.to_thread(
                build_result_archive,
                batch_id,
                external_batch_id,
                batch_dir,
                frames,
                workflow_identity,
                staging_path,
                cancel_event,
            )
        )
        try:
            # Shield the executor future so task cancellation can signal the
            # cooperative builder and wait briefly for it to leave staging.
            built = await asyncio.shield(build_task)
        except asyncio.CancelledError:
            cancel_event.set()
            try:
                await asyncio.wait_for(
                    asyncio.shield(build_task),
                    timeout=ARCHIVE_BUILD_CANCEL_GRACE_SECONDS,
                )
            except Exception as exc:
                logger().warning(
                    "batch.archive_cancel_grace_exceeded",
                    batch_id=batch_id,
                    error_type=type(exc).__name__,
                )
            finally:
                if build_task.done() and not staging_path.is_dir():
                    staging_path.unlink(missing_ok=True)
            raise
        except BatchContractError as exc:
            async with self.db.session() as session:
                async with session.begin():
                    await self.assert_scheduler_epoch(session)
                    batch = await session.get(JobBatch, batch_id, with_for_update=True)
                    if batch is not None and batch.status == BatchStatus.ASSEMBLING.value:
                        batch.error_code = exc.code
                        batch.error_message = str(exc)[:1000]
                        await transition_batch(
                            session,
                            batch,
                            BatchStatus.FAILED,
                            "batch.assembly_failed",
                            {
                                "ordinal": exc.ordinal,
                                "relative_path": exc.relative_path,
                                "error_code": exc.code,
                            },
                        )
            return
        try:
            async with self.db.session() as session:
                async with session.begin():
                    # The epoch key-share lock and Batch row lock are held
                    # across publish and commit. A takeover cannot advance and
                    # reconcile until this exact filesystem/DB publish ends.
                    await self.assert_scheduler_epoch(session)
                    batch = await session.scalar(
                        select(JobBatch)
                        .where(JobBatch.id == batch_id)
                        .with_for_update()
                    )
                    if batch is None or batch.status != BatchStatus.ASSEMBLING.value:
                        return
                    existing = await session.scalar(
                        select(BatchArtifact).where(
                            BatchArtifact.batch_id == batch.id,
                            BatchArtifact.kind == "result_archive",
                        )
                    )
                    destination = result_archive_final_path(Path(batch.batch_dir), batch.id)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(built.path, destination)
                    directory_fd = os.open(destination.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    relative_path = str(
                        destination.relative_to(Path(batch.batch_dir))
                    ).replace("\\", "/")
                    if existing is None:
                        session.add(
                            BatchArtifact(
                                id=str(uuid.uuid4()),
                                batch_id=batch.id,
                                kind="result_archive",
                                relative_path=relative_path,
                                filename=f"{batch.id}-rgba.zip",
                                content_type="application/zip",
                                size_bytes=built.size_bytes,
                                sha256=built.sha256,
                            )
                        )
                    else:
                        existing.relative_path = relative_path
                        existing.filename = f"{batch.id}-rgba.zip"
                        existing.content_type = "application/zip"
                        existing.size_bytes = built.size_bytes
                        existing.sha256 = built.sha256
                    batch.progress = 100
                    await transition_batch(
                        session,
                        batch,
                        BatchStatus.SUCCEEDED,
                        "batch.succeeded",
                        {"result_sha256": built.sha256, "total": batch.total_items},
                    )
        finally:
            # This is safe only after the builder returned. If cancellation
            # timed out earlier, its unique file is deliberately left for the
            # age-based orphan cleanup instead of racing the live thread.
            staging_path.unlink(missing_ok=True)
        await self.publish({"event": "batch.succeeded", "batch_id": batch_id})

    async def schedule_available(self) -> None:
        started = asyncio.get_running_loop().time()
        while not self.stop_event.is_set():
            async with self.db.session() as session:
                snapshot = await self.queue_snapshot(session)
                if snapshot.depth == 0:
                    break
                nodes = list((await session.scalars(select(Node))).all())
                guard = await self.guard(session)
                target_workflow = await session.scalar(
                    select(Job.workflow_key)
                    .outerjoin(ApiClient, ApiClient.id == Job.tenant_id)
                    .where(Job.status == JobStatus.QUEUED.value)
                    .order_by(
                        case(
                            (
                                or_(
                                    ApiClient.id.is_(None),
                                    ApiClient.client_kind != "test",
                                ),
                                0,
                            ),
                            else_=1,
                        ),
                        Job.pinned.desc(),
                        Job.created_at.asc(),
                        Job.id.asc(),
                    )
                    .limit(1)
                )
                warm_nodes = {
                    candidate.id
                    for candidate in nodes
                    if target_workflow
                    and str((candidate.labels or {}).get("warm_workflow", ""))
                    == target_workflow
                }
                candidates, exclusions = rank_nodes(
                    nodes,
                    snapshot,
                    guard,
                    self.settings.node_heartbeat_timeout_seconds,
                    preferred_node_ids=warm_nodes,
                )
                if not candidates:
                    logger().debug(
                        "scheduler.no_node", exclusions=exclusions
                    )
                    break
                candidate_nodes = [(candidate.id, candidate.pool) for candidate in candidates]
                # rollback() expires ORM instances. Keep scalar values before
                # ending the read transaction so async SQLAlchemy never tries
                # to lazy-load an expired Node outside greenlet_spawn.
                await session.rollback()
                assignment = None
                node_id = ""
                node_pool = ""
                compatibility_misses: list[str] = []
                for candidate_id, candidate_pool in candidate_nodes:
                    if self.stop_event.is_set():
                        break
                    async with session.begin():
                        # This lock is the commit barrier: a new leader's
                        # FOR UPDATE epoch advance waits for old claims, while
                        # post-takeover claims see the new epoch and rollback.
                        await self.assert_scheduler_epoch(session)
                        candidate_assignment = await claim_next_job(
                            session,
                            candidate_id,
                            self.settings.priority_aging_seconds,
                            queue_snapshot=snapshot,
                            overflow_guard=guard,
                            heartbeat_timeout_seconds=self.settings.node_heartbeat_timeout_seconds,
                            batch_max_running=self.settings.batch_max_running_per_tenant,
                        )
                    if candidate_assignment is None:
                        compatibility_misses.append(candidate_id)
                        continue
                    assignment = candidate_assignment
                    node_id = candidate_id
                    node_pool = candidate_pool
                    break
                if assignment is None:
                    logger().debug(
                        "scheduler.no_compatible_assignment",
                        candidates=[candidate_id for candidate_id, _ in candidate_nodes],
                        compatibility_misses=compatibility_misses,
                    )
                    break
                job, _ = assignment
                if self.stop_event.is_set():
                    logger().warning(
                        "scheduler.assignment_deferred_after_stop",
                        job_id=job.id,
                        node_id=node_id,
                        lock_lost=self.scheduler_lock_failure is not None,
                    )
                    break
                if node_pool == "OVERFLOW":
                    OVERFLOW.inc()
                logger().info(
                    "scheduler.assigned",
                    job_id=job.id,
                    node_id=node_id,
                    candidates=len(nodes),
                    exclusions=exclusions,
                    cache_affinity=node_id in warm_nodes,
                    target_workflow=target_workflow,
                )
                self.executions[job.id] = asyncio.create_task(self.execute(job.id))
        DECISION.observe(asyncio.get_running_loop().time() - started)

    async def execute(self, job_id: str, recovering: bool = False) -> None:
        token = None
        timeout_event = asyncio.Event()
        timeout_task: asyncio.Task[None] | None = None
        try:
            async with self.db.session() as session:
                await self.assert_scheduler_epoch(session)
                job = await session.get(Job, job_id)
                if job is None or not job.node_id:
                    return
                node = await session.get(Node, job.node_id)
                workflow = await session.scalar(
                    select(WorkflowVersion).where(
                        WorkflowVersion.workflow_key == job.workflow_key,
                        WorkflowVersion.version == job.workflow_version,
                    )
                )
                if node is None or workflow is None:
                    return
                # Do not retain the epoch row or a database snapshot across a
                # multi-minute GPU execution. This read fence proves startup
                # ownership, then every later mutation is fenced separately.
                await self.commit_as_leader(session)
                token = bind_context(
                    job_id=job.id,
                    trace_id=job.trace_id,
                    tenant_id=job.tenant_id,
                    workflow_key=job.workflow_key,
                    workflow_version=job.workflow_version,
                    node_id=node.id,
                    attempt=job.attempt_count,
                )
                client = ComfyClient(node.base_url)
                parent_task = asyncio.current_task()
                if parent_task is None:
                    raise RuntimeError("executor task is unavailable")
                timeout_task = asyncio.create_task(
                    self.timeout_watchdog(
                        job.id,
                        workflow.timeout_seconds,
                        client,
                        parent_task,
                        timeout_event,
                    )
                )
                try:
                    if recovering and not job.prompt_id and job.submission_intent_at:
                        recovery_attempt = job.attempt_count
                        client_id = job.submission_client_id or prompt_client_id(
                            job.id, recovery_attempt
                        )
                        try:
                            recovered_prompt_id = await reconcile_prompt_submission(
                                client,
                                client_id,
                                settle_seconds=PROMPT_SUBMISSION_SETTLE_SECONDS,
                            )
                        except ComfyError as exc:
                            current_job = await self.lock_job_as_leader(session, job.id)
                            if current_job is None:
                                return
                            job = current_job
                            current_client_id = job.submission_client_id or prompt_client_id(
                                job.id,
                                job.attempt_count,
                            )
                            if (
                                job.attempt_count != recovery_attempt
                                or current_client_id != client_id
                                or job.prompt_id is not None
                            ):
                                await self.commit_as_leader(session)
                                return
                            if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                                await self.commit_as_leader(session)
                                return
                            if (
                                job.cancel_requested
                                or job.status == JobStatus.CANCELLING.value
                            ):
                                await self.cancel_locked_job(
                                    session,
                                    job,
                                    event="scheduler.cancelled_during_submission_recovery",
                                )
                                await self.commit_as_leader(session)
                                await self.publish(
                                    {"event": "job.cancelled", "job_id": job.id}
                                )
                                return
                            await fail_closed_prompt_submission(session, job, exc)
                            await self.commit_as_leader(session)
                            FAILED.labels(exc.code).inc()
                            await self.publish(
                                {
                                    "event": "job.failed",
                                    "job_id": job.id,
                                    "error_code": exc.code,
                                }
                            )
                            return
                        current_job = await self.lock_job_as_leader(session, job.id)
                        if current_job is None:
                            return
                        job = current_job
                        current_client_id = job.submission_client_id or prompt_client_id(
                            job.id,
                            job.attempt_count,
                        )
                        if (
                            job.attempt_count != recovery_attempt
                            or current_client_id != client_id
                            or job.prompt_id is not None
                        ):
                            await self.commit_as_leader(session)
                            return
                        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                            await self.commit_as_leader(session)
                            return
                        await persist_prompt_id(session, job, recovered_prompt_id)
                        if (
                            job.cancel_requested
                            or job.status == JobStatus.CANCELLING.value
                        ):
                            try:
                                await asyncio.wait_for(client.interrupt(), timeout=5)
                            except Exception as interrupt_error:
                                logger().warning(
                                    "executor.cancel_interrupt_failed",
                                    job_id=job.id,
                                    error_code="COMFY_INTERRUPT_FAILED",
                                    error_type=type(interrupt_error).__name__,
                                )
                            await self.cancel_locked_job(
                                session,
                                job,
                                event="scheduler.cancelled_after_submission_recovery",
                            )
                            await self.commit_as_leader(session)
                            await self.publish(
                                {"event": "job.cancelled", "job_id": job.id}
                            )
                            return
                        if job.status == JobStatus.CLAIMED.value:
                            await transition_job(
                                session, job, JobStatus.UPLOADING, "scheduler.recover_uploading"
                            )
                        if job.status == JobStatus.UPLOADING.value:
                            await transition_job(
                                session,
                                job,
                                JobStatus.SUBMITTED,
                                "scheduler.recovered_submission",
                                {"prompt_id": recovered_prompt_id, "client_id": client_id},
                            )
                        self.storage.atomic_json(
                            Path(job.job_dir) / "comfy" / "submit.response.json",
                            {
                                "prompt_id": recovered_prompt_id,
                                "client_id": client_id,
                                "recovered": True,
                            },
                        )
                        await self.commit_as_leader(session)
                    if recovering and job.prompt_id:
                        recovery_prompt_id = job.prompt_id
                        recovery_attempt = job.attempt_count
                        history = await client.history(recovery_prompt_id)
                        if recovery_prompt_id in history:
                            await self.finish_from_history(session, job, workflow, client, history)
                            return
                        queue = await client.queue()
                        prompt_ids = {
                            str(item[1])
                            for key in ("queue_running", "queue_pending")
                            for item in queue.get(key, [])
                            if isinstance(item, list) and len(item) > 1
                        }
                        if recovery_prompt_id not in prompt_ids:
                            error = ComfyError(
                                "COMFY_SUBMISSION_UNKNOWN",
                                "prompt_id 不在 ComfyUI 队列或历史中，已闭锁且不会重复提交",
                                {"prompt_id": recovery_prompt_id},
                            )
                            current_job = await self.lock_job_as_leader(session, job.id)
                            if current_job is None:
                                return
                            job = current_job
                            if (
                                job.prompt_id != recovery_prompt_id
                                or job.attempt_count != recovery_attempt
                            ):
                                await self.commit_as_leader(session)
                                return
                            if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                                await self.commit_as_leader(session)
                                return
                            if (
                                job.cancel_requested
                                or job.status == JobStatus.CANCELLING.value
                            ):
                                await self.cancel_locked_job(
                                    session,
                                    job,
                                    event="scheduler.cancelled_during_prompt_recovery",
                                )
                                await self.commit_as_leader(session)
                                await self.publish(
                                    {"event": "job.cancelled", "job_id": job.id}
                                )
                                return
                            await fail_closed_prompt_submission(session, job, error)
                            await self.commit_as_leader(session)
                            FAILED.labels(error.code).inc()
                            return
                    if not job.prompt_id:
                        previous_workflow = await session.scalar(
                            select(Job.workflow_key)
                            .where(
                                Job.node_id == node.id,
                                Job.id != job.id,
                                Job.status.in_(
                                    [status.value for status in TERMINAL_JOB_STATUSES]
                                ),
                                Job.finished_at.is_not(None),
                            )
                            .order_by(Job.finished_at.desc())
                            .limit(1)
                        )
                        # Publish UPLOADING under a short fence before any
                        # cache or remote-input side effect begins.
                        await self.assert_scheduler_epoch(session)
                        await transition_job(
                            session, job, JobStatus.UPLOADING, "executor.uploading"
                        )
                        await self.commit_as_leader(session)

                        # Bound the complete pre-submit phase with one epoch
                        # key-share transaction. Takeover waits until free,
                        # overwrite+SHA-verified uploads and durable intent are
                        # all complete, but never waits for GPU execution.
                        await self.assert_scheduler_epoch(session)
                        if previous_workflow != job.workflow_key:
                            # Large model families cannot coexist on a 24 GiB 3090.
                            # Release only on a family switch (or cold start); same-
                            # workflow jobs keep their hot cache for lower latency.
                            free_result = await client.free()
                            logger().info(
                                "executor.memory_released",
                                job_id=job.id,
                                previous_workflow=previous_workflow,
                                current_workflow=job.workflow_key,
                            )
                        else:
                            free_result = {
                                "skipped": True,
                                "reason": "same_workflow_cache",
                                "workflow_key": job.workflow_key,
                            }
                            logger().info(
                                "executor.memory_cache_reused",
                                job_id=job.id,
                                workflow_key=job.workflow_key,
                            )
                        root = Path(job.job_dir)
                        self.storage.atomic_json(
                            root / "comfy" / "free.response.json", free_result
                        )
                        uploads: list[dict[str, Any]] = []
                        for path in sorted((root / "input").glob("*")):
                            if path.is_file() and not path.name.endswith(".json"):
                                uploads.append(
                                    await client.upload(
                                        path, mask=path.name.startswith("mask-"), subfolder=job.id
                                    )
                                )
                        self.storage.atomic_json(root / "comfy" / "upload.responses.json", uploads)
                        refreshed_job = await self.lock_job_as_leader(session, job.id)
                        if refreshed_job is None:
                            return
                        job = refreshed_job
                        if job.cancel_requested or job.status == JobStatus.CANCELLING.value:
                            await self.cancel_locked_job(
                                session,
                                job,
                                event="executor.cancelled_before_submit",
                            )
                            await self.commit_as_leader(session)
                            await self.publish(
                                {"event": "job.cancelled", "job_id": job.id}
                            )
                            return
                        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                            await self.commit_as_leader(session)
                            return
                        attempt = await current_job_attempt(session, job, lock=True)
                        attempt.upload_attempts += sum(
                            max(1, int(upload.get("attempt", 1))) for upload in uploads
                        )
                        rendered = json.loads(
                            (root / "workflow" / "rendered.api.json").read_text(encoding="utf-8")
                        )
                        submission_attempt = job.attempt_count
                        client_id = await prepare_prompt_submission(session, job)
                        # The intent and prompt-attempt count must be durable
                        # before the non-transactional Comfy request begins.
                        await self.commit_as_leader(session)
                        recovered_after_submit_error = False
                        try:
                            submitted_prompt_id = await client.submit(rendered, client_id)
                        except ComfyError as submit_error:
                            try:
                                submitted_prompt_id = await reconcile_prompt_submission(
                                    client,
                                    client_id,
                                    settle_seconds=PROMPT_SUBMISSION_SETTLE_SECONDS,
                                )
                                recovered_after_submit_error = True
                            except ComfyError as reconcile_error:
                                error = ComfyError(
                                    reconcile_error.code,
                                    str(reconcile_error),
                                    {
                                        **reconcile_error.details,
                                        "submit_error_code": submit_error.code,
                                    },
                                )
                                refreshed_job = await self.lock_job_as_leader(
                                    session,
                                    job.id,
                                )
                                if refreshed_job is None:
                                    return
                                job = refreshed_job
                                current_client_id = (
                                    job.submission_client_id
                                    or prompt_client_id(job.id, job.attempt_count)
                                )
                                if (
                                    job.attempt_count != submission_attempt
                                    or current_client_id != client_id
                                    or job.prompt_id is not None
                                ):
                                    await self.commit_as_leader(session)
                                    return
                                if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                                    await self.commit_as_leader(session)
                                    return
                                if (
                                    job.cancel_requested
                                    or job.status == JobStatus.CANCELLING.value
                                ):
                                    await self.cancel_locked_job(
                                        session,
                                        job,
                                        event="executor.cancelled_during_submit_reconcile",
                                    )
                                    await self.commit_as_leader(session)
                                    await self.publish(
                                        {"event": "job.cancelled", "job_id": job.id}
                                    )
                                    return
                                await fail_closed_prompt_submission(session, job, error)
                                await self.commit_as_leader(session)
                                FAILED.labels(error.code).inc()
                                await self.publish(
                                    {
                                        "event": "job.failed",
                                        "job_id": job.id,
                                        "error_code": error.code,
                                    }
                                )
                                return
                        refreshed_job = await self.lock_job_as_leader(session, job.id)
                        if refreshed_job is None:
                            return
                        job = refreshed_job
                        current_client_id = job.submission_client_id or prompt_client_id(
                            job.id,
                            job.attempt_count,
                        )
                        if (
                            job.attempt_count != submission_attempt
                            or current_client_id != client_id
                            or (
                                job.prompt_id is not None
                                and job.prompt_id != submitted_prompt_id
                            )
                        ):
                            await self.commit_as_leader(session)
                            return
                        if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                            await self.commit_as_leader(session)
                            return
                        await persist_prompt_id(session, job, submitted_prompt_id)
                        self.storage.atomic_json(
                            root / "comfy" / "submit.response.json",
                            {
                                "prompt_id": submitted_prompt_id,
                                "client_id": client_id,
                                "recovered_after_submit_error": recovered_after_submit_error,
                            },
                        )
                        cancelled_after_submit = bool(
                            job.cancel_requested
                            or job.status == JobStatus.CANCELLING.value
                        )
                        if cancelled_after_submit:
                            try:
                                await asyncio.wait_for(client.interrupt(), timeout=5)
                            except Exception as exc:
                                logger().warning(
                                    "executor.cancel_interrupt_failed",
                                    job_id=job.id,
                                    error_code="COMFY_INTERRUPT_FAILED",
                                    error_type=type(exc).__name__,
                                )
                            await self.cancel_locked_job(
                                session,
                                job,
                                event="executor.cancelled_after_submit",
                            )
                            await self.commit_as_leader(session)
                            await self.publish(
                                {"event": "job.cancelled", "job_id": job.id}
                            )
                            return
                        await transition_job(
                            session,
                            job,
                            JobStatus.SUBMITTED,
                            "executor.submitted",
                            {
                                "prompt_id": submitted_prompt_id,
                                "client_id": client_id,
                                "recovered_after_submit_error": recovered_after_submit_error,
                            },
                        )
                        await self.commit_as_leader(session)
                    current_job = await self.lock_job_as_leader(session, job.id)
                    if current_job is None:
                        return
                    job = current_job
                    if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                        await self.commit_as_leader(session)
                        return
                    if job.cancel_requested or job.status == JobStatus.CANCELLING.value:
                        # Interrupt is an external side effect; epoch -> Job
                        # locks prove this executor still owns the attempt.
                        try:
                            await asyncio.wait_for(client.interrupt(), timeout=5)
                        except Exception as exc:
                            logger().warning(
                                "executor.cancel_interrupt_failed",
                                job_id=job.id,
                                error_code="COMFY_INTERRUPT_FAILED",
                                error_type=type(exc).__name__,
                            )
                        await self.cancel_locked_job(
                            session,
                            job,
                            event="executor.cancelled",
                        )
                        await self.commit_as_leader(session)
                        await self.publish({"event": "job.cancelled", "job_id": job.id})
                        return
                    execution_prompt_id = job.prompt_id
                    execution_attempt = job.attempt_count
                    # Do not retain the validation locks while waiting on the
                    # websocket. Every meaningful event reacquires them.
                    await self.commit_as_leader(session)
                    cancellation_task = asyncio.create_task(
                        self.watch_cancellation(job.id, client)
                    )
                    try:
                        try:
                            async for event in client.events(
                                job.prompt_id or "",
                                job.submission_client_id
                                or prompt_client_id(job.id, job.attempt_count),
                            ):
                                event_type = str(event.get("type", ""))
                                if event_type in {
                                    "execution_start",
                                    "executing",
                                    "progress",
                                    "execution_success",
                                    "execution_error",
                                    "execution_interrupted",
                                    "history_recovered",
                                }:
                                    current_job = await self.lock_job_as_leader(
                                        session,
                                        job.id,
                                    )
                                    if current_job is None:
                                        return
                                    job = current_job
                                    if (
                                        job.prompt_id != execution_prompt_id
                                        or job.attempt_count != execution_attempt
                                        or JobStatus(job.status) in TERMINAL_JOB_STATUSES
                                    ):
                                        await self.commit_as_leader(session)
                                        return
                                cancelling = bool(
                                    job.cancel_requested
                                    or job.status == JobStatus.CANCELLING.value
                                )
                                if event_type == "progress" and not cancelling:
                                    await mark_gpu_started(session, job)
                                    data = event.get("data", {})
                                    job.progress = monotonic_job_progress(
                                        job.progress,
                                        float(data.get("value", 0)),
                                        float(data.get("max", 1)),
                                    )
                                    await self.commit_as_leader(session)
                                    await self.publish(
                                        {
                                            "event": "job.progress",
                                            "job_id": job.id,
                                            "progress": job.progress,
                                        }
                                    )
                                elif event_type in {
                                    "execution_success",
                                    "execution_error",
                                    "execution_interrupted",
                                    "history_recovered",
                                }:
                                    await mark_gpu_finished(session, job)
                                    await self.commit_as_leader(session)
                                elif (
                                    event_type in {"execution_start", "executing"}
                                    and not cancelling
                                ):
                                    await mark_gpu_started(session, job)
                                    await self.commit_as_leader(session)
                                elif event_type in {"progress", "execution_start", "executing"}:
                                    # Preserve a concurrently committed cancel;
                                    # this read-only commit only releases locks.
                                    await self.commit_as_leader(session)
                        except ComfyError:
                            current_job = await self.lock_job_as_leader(session, job.id)
                            if current_job is None:
                                return
                            job = current_job
                            if not job.cancel_requested:
                                raise
                    finally:
                        cancellation_task.cancel()
                        await asyncio.gather(cancellation_task, return_exceptions=True)
                    current_job = await self.lock_job_as_leader(session, job.id)
                    if current_job is None:
                        return
                    job = current_job
                    if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                        await self.commit_as_leader(session)
                        return
                    if job.cancel_requested:
                        await self.cancel_locked_job(
                            session,
                            job,
                            event="executor.cancelled",
                        )
                        await self.commit_as_leader(session)
                        await self.publish({"event": "job.cancelled", "job_id": job.id})
                        return
                    # End the refresh transaction before the external history
                    # request so no idle snapshot/epoch lock spans network I/O.
                    await self.commit_as_leader(session)
                    history = await client.history(job.prompt_id or "")
                    await self.finish_from_history(session, job, workflow, client, history)
                finally:
                    await client.close()
        except ComfyError as exc:
            await self.fail_job(job_id, exc.code, str(exc))
        except asyncio.CancelledError:
            if not timeout_event.is_set():
                raise
        except Exception as exc:
            logger().exception(
                "executor.failed",
                job_id=job_id,
                error_code="INTERNAL_ERROR",
            )
            await self.fail_job(job_id, "INTERNAL_ERROR", type(exc).__name__)
        finally:
            if timeout_task is not None:
                timeout_task.cancel()
                await asyncio.gather(timeout_task, return_exceptions=True)
            if token is not None:
                reset_context(token)
            self.executions.pop(job_id, None)
            self.wakeup.set()

    async def watch_cancellation(self, job_id: str, client: ComfyClient) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(0.5)
            async with self.db.session() as session:
                await self.assert_scheduler_epoch(session)
                cancel_requested = await session.scalar(
                    select(Job.cancel_requested).where(Job.id == job_id)
                )
                if cancel_requested:
                    try:
                        await asyncio.wait_for(client.interrupt(), timeout=5)
                    except Exception as exc:
                        logger().warning(
                            "executor.cancel_interrupt_failed",
                            job_id=job_id,
                            error_code="COMFY_INTERRUPT_FAILED",
                            error_type=type(exc).__name__,
                        )
                    return

    async def timeout_watchdog(
        self,
        job_id: str,
        timeout_seconds: int,
        client: ComfyClient,
        parent_task: asyncio.Task[Any],
        timeout_event: asyncio.Event,
    ) -> None:
        await asyncio.sleep(timeout_seconds)
        timed_out = False
        cancelled = False
        async with self.db.session() as session:
            # Lock order is always epoch -> Job. This also proves an old
            # watchdog cannot interrupt a prompt after takeover.
            await self.assert_scheduler_epoch(session)
            job = await session.get(Job, job_id, with_for_update=True)
            if job is not None and JobStatus(job.status) not in TERMINAL_JOB_STATUSES:
                try:
                    await asyncio.wait_for(client.interrupt(), timeout=5)
                except Exception as exc:
                    logger().warning(
                        "executor.timeout_interrupt_failed",
                        job_id=job_id,
                        error_code="COMFY_INTERRUPT_FAILED",
                        error_type=type(exc).__name__,
                    )
                if job.cancel_requested or job.status == JobStatus.CANCELLING.value:
                    await self.cancel_locked_job(
                        session,
                        job,
                        event="executor.cancelled_at_timeout_boundary",
                    )
                    await self.commit_as_leader(session)
                    cancelled = True
                else:
                    timeout_event.set()
                    attempt = await current_job_attempt(session, job, lock=True)
                    if attempt.gpu_started_at is not None and attempt.gpu_finished_at is None:
                        attempt.gpu_finished_at = datetime.now(UTC)
                    job.error_code = "JOB_TIMEOUT"
                    job.error_message = f"任务超过工作流截止时间 {timeout_seconds} 秒"
                    await transition_job(
                        session,
                        job,
                        JobStatus.TIMED_OUT,
                        "executor.timed_out",
                        {"timeout_seconds": timeout_seconds},
                    )
                    await release_lease(
                        session,
                        job,
                        attempt_status=JobStatus.TIMED_OUT,
                        attempt_error={
                            "code": "JOB_TIMEOUT",
                            "message": job.error_message,
                        },
                    )
                    await self.commit_as_leader(session)
                    timed_out = True
        if cancelled:
            await self.publish({"event": "job.cancelled", "job_id": job_id})
            parent_task.cancel()
        elif timed_out:
            FAILED.labels("JOB_TIMEOUT").inc()
            await self.publish({"event": "job.timed_out", "job_id": job_id})
            parent_task.cancel()

    async def finish_from_history(
        self,
        session: AsyncSession,
        job: Job,
        workflow: WorkflowVersion,
        client: ComfyClient,
        history: dict[str, Any],
    ) -> None:
        expected_prompt_id = job.prompt_id
        expected_attempt = job.attempt_count
        entry = history.get(expected_prompt_id or "")
        if not entry:
            raise ComfyError("COMFY_OUTPUT_MISSING", "history does not contain prompt")

        # Refresh under epoch -> Job locks before accepting the history result.
        # A cancellation or replacement attempt committed while history was in
        # flight must win over this late executor result.
        current_job = await self.lock_job_as_leader(session, job.id)
        if current_job is None:
            return
        job = current_job
        if (
            job.prompt_id != expected_prompt_id
            or job.attempt_count != expected_attempt
            or JobStatus(job.status) in TERMINAL_JOB_STATUSES
        ):
            await self.commit_as_leader(session)
            return
        if job.cancel_requested or job.status == JobStatus.CANCELLING.value:
            await self.cancel_locked_job(
                session,
                job,
                event="executor.cancelled_before_download",
            )
            await self.commit_as_leader(session)
            await self.publish({"event": "job.cancelled", "job_id": job.id})
            return
        await mark_gpu_finished(session, job)
        await self.commit_as_leader(session)
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            error_message = "ComfyUI execution error"
            for message in reversed(status.get("messages", [])):
                if (
                    isinstance(message, list)
                    and len(message) == 2
                    and message[0] == "execution_error"
                    and isinstance(message[1], dict)
                ):
                    details = message[1]
                    node = details.get("node_id", "?")
                    node_type = details.get("node_type", "unknown")
                    exception = details.get("exception_message") or details.get("exception_type")
                    if exception:
                        error_message = f"node {node} {node_type}: {exception}"
                    break
            raise ComfyError(
                "COMFY_EXECUTION_ERROR", error_message[:1000], {"status": status}
            )
        outputs = client.outputs(
            history,
            job.prompt_id or "",
            {str(node_id) for node_id in workflow.output_nodes},
        )
        if not outputs:
            raise ComfyError("COMFY_OUTPUT_MISSING", "ComfyUI completed without outputs")
        if job.status != JobStatus.DOWNLOADING.value:
            current_job = await self.lock_job_as_leader(session, job.id)
            if current_job is None:
                return
            job = current_job
            if (
                job.prompt_id != expected_prompt_id
                or job.attempt_count != expected_attempt
                or JobStatus(job.status) in TERMINAL_JOB_STATUSES
            ):
                await self.commit_as_leader(session)
                return
            if job.cancel_requested or job.status == JobStatus.CANCELLING.value:
                await self.cancel_locked_job(
                    session,
                    job,
                    event="executor.cancelled_before_download",
                )
                await self.commit_as_leader(session)
                await self.publish({"event": "job.cancelled", "job_id": job.id})
                return
            await transition_job(session, job, JobStatus.DOWNLOADING, "executor.downloading")
            await self.commit_as_leader(session)
        root = Path(job.job_dir)
        self.storage.atomic_json(root / "comfy" / "history.json", history)
        staging_dir = root / "output" / ".download" / str(uuid.uuid4())
        downloaded: list[tuple[Path, Path, int, str]] = []
        try:
            for index, output in enumerate(outputs):
                filename = f"{index:03d}-{Path(output.filename).name}"
                staging = staging_dir / filename
                destination = root / "output" / filename
                size, digest = await client.download(output, staging)
                downloaded.append((staging, destination, size, digest))

            # The official output paths remain untouched during transfer.
            # Acquire epoch -> Job locks only after every staged byte is
            # durable, then revalidate cancellation and attempt identity.
            current_job = await self.lock_job_as_leader(session, job.id)
            if current_job is None:
                return
            job = current_job
            if (
                job.prompt_id != expected_prompt_id
                or job.attempt_count != expected_attempt
                or JobStatus(job.status) in TERMINAL_JOB_STATUSES
            ):
                await self.commit_as_leader(session)
                return
            if job.cancel_requested or job.status == JobStatus.CANCELLING.value:
                await self.cancel_locked_job(
                    session,
                    job,
                    event="executor.cancelled_during_download",
                )
                await self.commit_as_leader(session)
                await self.publish({"event": "job.cancelled", "job_id": job.id})
                return

            output_dir = root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            for staging, destination, size, digest in downloaded:
                os.replace(staging, destination)
                session.add(
                    JobArtifact(
                        id=str(uuid.uuid4()),
                        job_id=job.id,
                        kind="output",
                        relative_path=str(destination.relative_to(root)).replace("\\", "/"),
                        content_type="application/octet-stream",
                        size_bytes=size,
                        sha256=digest,
                        download_confirmed=True,
                    )
                )
            directory_fd = os.open(output_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

            job.progress = 100
            await transition_job(
                session,
                job,
                JobStatus.SUCCEEDED,
                "executor.succeeded",
                {"outputs": len(outputs)},
            )
            if job.node_id:
                # ``execute`` keeps this Session (and its initially loaded Node)
                # alive across the whole GPU run.  Commits do not expire ORM
                # instances, so force a refresh under the row lock and merge
                # only GPU Control's warm-cache keys.
                node = await session.get(
                    Node,
                    job.node_id,
                    populate_existing=True,
                    with_for_update=True,
                )
                if node is not None:
                    labels = dict(node.labels or {})
                    labels["warm_workflow"] = job.workflow_key
                    labels["warm_workflow_at"] = datetime.now(UTC).isoformat()
                    node.labels = labels
            await release_lease(session, job, attempt_status=JobStatus.SUCCEEDED)
            await self.commit_as_leader(session)
            COMPLETED.labels(workflow.workflow_key).inc()
            await self.publish({"event": "job.succeeded", "job_id": job.id})
        finally:
            # Only the unique private staging directory is removed. A late or
            # cancelled download can therefore never erase a published file.
            shutil.rmtree(staging_dir, ignore_errors=True)

    async def fail_job(self, job_id: str, code: str, message: str) -> None:
        cancelled = False
        async with self.db.session() as session:
            await self.assert_scheduler_epoch(session)
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None:
                return
            if JobStatus(job.status) in TERMINAL_JOB_STATUSES:
                # Late transport/task exceptions cannot rewrite a committed
                # success, cancellation, timeout, or failure terminal state.
                return
            job.error_code = code
            job.error_message = message[:1000]
            if job.node_id and code == "COMFY_EXECUTION_ERROR":
                node = await session.scalar(
                    select(Node).where(Node.id == job.node_id).with_for_update()
                )
                if node is not None:
                    labels = dict(node.labels or {})
                    labels.pop("warm_workflow", None)
                    labels.pop("warm_workflow_at", None)
                    node.labels = labels
            if job.status == JobStatus.CANCELLING.value:
                # Cancellation is already a durable, authenticated intent.
                # An executor/transport error racing with that intent must
                # not enter the retry branch (CANCELLING -> RETRY_WAIT is
                # illegal) or turn a requested cancellation into FAILED.
                await transition_job(
                    session,
                    job,
                    JobStatus.CANCELLED,
                    "executor.cancelled_after_error",
                    {"error_code": code},
                )
                await release_lease(
                    session,
                    job,
                    attempt_status=JobStatus.CANCELLED,
                    attempt_error={"code": code, "message": message[:1000]},
                )
                cancelled = True
            elif (
                job.attempt_count < job.max_attempts
                and not job.prompt_id
                and job.submission_intent_at is None
            ):
                await transition_job(
                    session,
                    job,
                    JobStatus.RETRY_WAIT,
                    "executor.retry_wait",
                    {"error_code": code},
                )
                await release_lease(
                    session,
                    job,
                    attempt_status=JobStatus.FAILED,
                    attempt_error={"code": code, "message": message[:1000]},
                )
                await transition_job(session, job, JobStatus.QUEUED, "executor.retry_queued")
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
            else:
                await transition_job(
                    session, job, JobStatus.FAILED, "executor.failed", {"error_code": code}
                )
                await release_lease(
                    session,
                    job,
                    attempt_status=JobStatus.FAILED,
                    attempt_error={"code": code, "message": message[:1000]},
                )
            await self.commit_as_leader(session)
        if cancelled:
            await self.publish({"event": "job.cancelled", "job_id": job_id})
            return
        FAILED.labels(code).inc()
        await self.publish({"event": "job.failed", "job_id": job_id, "error_code": code})

    async def monitor_scheduler_lock(self, handle: SchedulerLockHandle) -> None:
        """Continuously prove singleton-lock ownership and fail closed on uncertainty."""

        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=SCHEDULER_LOCK_CHECK_INTERVAL_SECONDS,
                )
                return
            except TimeoutError:
                pass

            try:
                await asyncio.wait_for(
                    self.db.assert_scheduler_lock(handle),
                    timeout=SCHEDULER_LOCK_QUERY_TIMEOUT_SECONDS,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, SchedulerLockLost):
                    failure = exc
                else:
                    failure = SchedulerLockLost(
                        "scheduler advisory lock liveness check timed out or failed"
                    )
                self.mark_scheduler_lock_lost(failure)
                logger().error(
                    "scheduler.lock_lost",
                    error_type=type(exc).__name__,
                    backend_pid=handle.backend_pid,
                )
                if failure is exc:
                    raise
                raise failure from exc

    async def monitor_event_loop_lag(
        self,
        interval_seconds: float = SCHEDULER_LOOP_LAG_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        """Sample current event-loop wake delay without carrying prior drift."""

        interval_seconds = max(0.001, interval_seconds)
        loop = asyncio.get_running_loop()
        while not self.stop_event.is_set():
            wait_started_at = loop.time()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=interval_seconds,
                )
                return
            except TimeoutError:
                LOOP_LAG.set(
                    event_loop_wake_delay(
                        wait_started_at,
                        interval_seconds,
                        loop.time(),
                    )
                )

    def raise_if_scheduler_lock_lost(self) -> None:
        if self.scheduler_lock_failure is not None:
            raise self.scheduler_lock_failure

    @staticmethod
    async def cancel_tasks_bounded(
        tasks: list[asyncio.Task[Any]], timeout_seconds: float
    ) -> int:
        """Cancel tasks without allowing a failed lock holder to block takeover."""

        unique_tasks = list(dict.fromkeys(tasks))
        if not unique_tasks:
            return 0
        for task in unique_tasks:
            if not task.done():
                task.cancel()
        done, pending = await asyncio.wait(unique_tasks, timeout=timeout_seconds)
        for task in done:
            if not task.cancelled():
                task.exception()
        return len(pending)

    async def run(self) -> None:
        configure_logging("scheduler", self.settings.environment)
        runtime_identity = runtime_version_metadata()
        BUILD_INFO.info(
            {
                key: str(value) if value is not None else "unknown"
                for key, value in runtime_identity.items()
            }
        )
        BUILD_ALIGNED.set(1 if runtime_identity["version_aligned"] else 0)
        logger().info("scheduler.runtime_identity", **runtime_identity)
        start_http_server(9108)
        try:
            async with self.db.scheduler_lock() as lock_handle:
                try:
                    self.scheduler_lock_task = asyncio.create_task(
                        self.monitor_scheduler_lock(lock_handle)
                    )
                    await self.establish_scheduler_epoch(lock_handle)
                    self.raise_if_scheduler_lock_lost()
                    self.event_loop_lag_task = asyncio.create_task(
                        self.monitor_event_loop_lag()
                    )
                    # Arm the independent lag probe before startup reconciliation.
                    await asyncio.sleep(0)
                    await self.reconcile()
                    self.raise_if_scheduler_lock_lost()
                    self.health_task = asyncio.create_task(self.update_node_health())
                    self.redis_task = asyncio.create_task(self.redis_listener())
                    self.callback_task = asyncio.create_task(self.callback_loop())
                    while not self.stop_event.is_set():
                        await self.reconcile_batches()
                        self.raise_if_scheduler_lock_lost()
                        await self.schedule_available()
                        self.raise_if_scheduler_lock_lost()
                        self.wakeup.clear()
                        try:
                            await asyncio.wait_for(
                                self.wakeup.wait(),
                                timeout=self.settings.scheduler_fallback_scan_ms / 1000,
                            )
                        except TimeoutError:
                            continue
                    self.raise_if_scheduler_lock_lost()
                finally:
                    self.stop_event.set()
                    auxiliary_tasks = [
                        task
                        for task in (
                            self.health_task,
                            self.redis_task,
                            self.callback_task,
                            self.event_loop_lag_task,
                            self.scheduler_lock_task,
                        )
                        if task is not None
                    ]
                    if self.scheduler_lock_failure is not None:
                        pending = await self.cancel_tasks_bounded(
                            [
                                *auxiliary_tasks,
                                *self.executions.values(),
                                *self.batch_assemblies.values(),
                            ],
                            SCHEDULER_LOCK_FAILURE_GRACE_SECONDS,
                        )
                        if pending:
                            logger().error(
                                "scheduler.lock_failure_cleanup_timeout",
                                pending_tasks=pending,
                                grace_seconds=SCHEDULER_LOCK_FAILURE_GRACE_SECONDS,
                            )
                    else:
                        for task in auxiliary_tasks:
                            task.cancel()
                        await asyncio.gather(*auxiliary_tasks, return_exceptions=True)
                        if self.executions:
                            await asyncio.gather(
                                *self.executions.values(), return_exceptions=True
                            )
                        if self.batch_assemblies:
                            for task in self.batch_assemblies.values():
                                task.cancel()
                            await asyncio.gather(
                                *self.batch_assemblies.values(), return_exceptions=True
                            )
        finally:
            await self.redis.aclose()
            await self.db.close()


async def async_main() -> None:
    scheduler = Scheduler(get_settings())
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, scheduler.stop_event.set)
        except NotImplementedError:
            signal.signal(name, lambda *_: scheduler.stop_event.set())
    await scheduler.run()


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
