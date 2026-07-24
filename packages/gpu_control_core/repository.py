import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .enums import TERMINAL_JOB_STATUSES, JobStatus, Priority
from .models import (
    ApiClient,
    Job,
    JobAttempt,
    JobEvent,
    Node,
    NodeLease,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from .scheduling import OverflowGuard, QueueSnapshot, base_exclusion, overflow_exclusion
from .state_machine import require_transition

ACTIVE_STATUSES = (
    JobStatus.CLAIMED.value,
    JobStatus.UPLOADING.value,
    JobStatus.SUBMITTED.value,
    JobStatus.RUNNING.value,
    JobStatus.DOWNLOADING.value,
    JobStatus.CANCELLING.value,
)


async def transition_job(
    session: AsyncSession,
    job: Job,
    target: JobStatus,
    event: str,
    details: dict[str, Any] | None = None,
) -> None:
    current = JobStatus(job.status)
    require_transition(current, target)
    sequence = await session.scalar(
        select(func.coalesce(func.max(JobEvent.sequence), 0)).where(JobEvent.job_id == job.id)
    )
    job.status = target.value
    job.updated_at = datetime.now(UTC)
    if target == JobStatus.RUNNING and job.started_at is None:
        job.started_at = datetime.now(UTC)
    if target in TERMINAL_JOB_STATUSES:
        job.finished_at = datetime.now(UTC)
    session.add(
        JobEvent(
            job_id=job.id,
            sequence=int(sequence or 0) + 1,
            previous_status=current.value,
            status=target.value,
            event=event,
            details=details or {},
        )
    )


def priority_rank(priority: str, waited_seconds: float, aging_seconds: int) -> int:
    base = {Priority.BATCH.value: 0, Priority.NORMAL.value: 1, Priority.CRITICAL.value: 2}.get(
        priority, 1
    )
    return min(2, base + int(waited_seconds // aging_seconds))


def choose_fair_job(
    jobs: Sequence[Job],
    tenant_last_scheduled: dict[str, datetime | None],
    now: datetime,
    aging_seconds: int,
) -> Job | None:
    if not jobs:
        return None
    ranked: list[tuple[int, Job]] = []
    for job in jobs:
        created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
        rank = priority_rank(job.priority, max(0, (now - created).total_seconds()), aging_seconds)
        ranked.append((rank + (10 if job.pinned else 0), job))
    best_band = max(rank for rank, _ in ranked)
    band = [job for rank, job in ranked if rank == best_band]
    minimum = datetime.min.replace(tzinfo=UTC)

    def aware(value: datetime | None, fallback: datetime) -> datetime:
        if value is None:
            return fallback
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    band.sort(
        key=lambda j: (
            aware(tenant_last_scheduled.get(j.tenant_id), minimum),
            aware(j.created_at, minimum),
            j.id,
        )
    )
    return band[0]


async def claim_next_job(
    session: AsyncSession,
    node_id: str,
    aging_seconds: int,
    lease_seconds: int = 1800,
    queue_snapshot: QueueSnapshot | None = None,
    overflow_guard: OverflowGuard | None = None,
    heartbeat_timeout_seconds: int = 20,
    batch_max_running: int = 3,
) -> tuple[Job, NodeLease] | None:
    now = datetime.now(UTC)
    node = await session.scalar(select(Node).where(Node.id == node_id).with_for_update())
    if node is None or node.current_jobs >= node.max_concurrency:
        return None
    if queue_snapshot is not None:
        reason = base_exclusion(node, now, heartbeat_timeout_seconds)
        if reason is None and node.pool != "PRIMARY" and overflow_guard is not None:
            reason = overflow_exclusion(node, queue_snapshot, overflow_guard, now)
        if reason is not None:
            return None
    jobs = list(
        (
            await session.scalars(
                select(Job)
                .join(
                    WorkflowVersion,
                    and_(
                        WorkflowVersion.workflow_key == Job.workflow_key,
                        WorkflowVersion.version == Job.workflow_version,
                        WorkflowVersion.enabled.is_(True),
                    ),
                )
                .join(
                    WorkflowNodeCompatibility,
                    and_(
                        WorkflowNodeCompatibility.workflow_version_id == WorkflowVersion.id,
                        WorkflowNodeCompatibility.node_id == node_id,
                        WorkflowNodeCompatibility.compatible.is_(True),
                    ),
                )
                .where(Job.status == JobStatus.QUEUED.value)
                .where((Job.not_before.is_(None)) | (Job.not_before <= now))
                .order_by(Job.pinned.desc(), Job.created_at.asc())
                .limit(200)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if not jobs:
        return None
    tenant_ids = {job.tenant_id for job in jobs}
    clients = list(
        (await session.scalars(select(ApiClient).where(ApiClient.id.in_(tenant_ids)))).all()
    )
    client_by_id = {candidate.id: candidate for candidate in clients}
    remaining = jobs
    chosen: Job | None = None
    client: ApiClient | None = None
    while remaining:
        chosen = choose_fair_job(
            remaining, {c.id: c.last_scheduled_at for c in clients}, now, aging_seconds
        )
        if chosen is None:
            return None
        client = client_by_id.get(chosen.tenant_id)
        if client is None:
            break
        running = await session.scalar(
            select(func.count(Job.id)).where(
                Job.tenant_id == client.id, Job.status.in_(ACTIVE_STATUSES)
            )
        )
        running_limit = (
            max(client.max_running, batch_max_running)
            if chosen.batch_id is not None
            else client.max_running
        )
        if int(running or 0) < running_limit:
            break
        remaining = [job for job in remaining if job.tenant_id != client.id]
        chosen = None
        client = None
    if chosen is None:
        return None
    token = secrets.token_hex(32)
    lease = await session.scalar(
        select(NodeLease).where(NodeLease.job_id == chosen.id).with_for_update()
    )
    if lease is None:
        lease = NodeLease(
            id=str(uuid.uuid4()),
            node_id=node.id,
            job_id=chosen.id,
            token=token,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        session.add(lease)
    else:
        # A job owns one durable lease row.  Retries reactivate that row with a
        # fresh token instead of violating node_leases.job_id's unique key.
        lease.node_id = node.id
        lease.token = token
        lease.active = True
        lease.acquired_at = now
        lease.expires_at = now + timedelta(seconds=lease_seconds)
        lease.released_at = None
    chosen.node_id = node.id
    chosen.claimed_at = now
    chosen.attempt_count += 1
    node.current_jobs += 1
    node.last_assigned_at = now
    if client is not None:
        client.last_scheduled_at = now
    await transition_job(
        session, chosen, JobStatus.CLAIMED, "scheduler.claimed", {"node_id": node.id}
    )
    session.add(
        JobAttempt(
            job_id=chosen.id,
            attempt=chosen.attempt_count,
            node_id=node.id,
            lease_token=token,
            status=JobStatus.CLAIMED.value,
        )
    )
    await session.flush()
    return chosen, lease


async def release_lease(
    session: AsyncSession,
    job: Job,
    *,
    attempt_status: JobStatus | None = None,
    attempt_error: dict[str, Any] | None = None,
) -> None:
    if not job.node_id:
        return
    node = await session.scalar(select(Node).where(Node.id == job.node_id).with_for_update())
    lease = await session.scalar(
        select(NodeLease)
        .where(NodeLease.job_id == job.id, NodeLease.active.is_(True))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if lease is not None:
        lease.active = False
        lease.released_at = now
    attempt = await session.scalar(
        select(JobAttempt)
        .where(JobAttempt.job_id == job.id, JobAttempt.attempt == job.attempt_count)
        .with_for_update()
    )
    if attempt is not None:
        attempt.prompt_id = job.prompt_id
        if attempt_status is not None:
            attempt.status = attempt_status.value
            attempt.finished_at = now
            attempt.error = attempt_error or {}
    if node is not None:
        node.current_jobs = max(0, node.current_jobs - 1)
