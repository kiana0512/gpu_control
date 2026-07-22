import asyncio
import ipaddress
import json
import signal
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.comfy_client import ComfyClient, ComfyError
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import TERMINAL_JOB_STATUSES, JobStatus, NodeHealth
from packages.gpu_control_core.logging import bind_context, configure_logging, logger, reset_context
from packages.gpu_control_core.models import (
    CallbackAttempt,
    Job,
    JobArtifact,
    JobCallback,
    Node,
    SystemSetting,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import claim_next_job, release_lease, transition_job
from packages.gpu_control_core.scheduling import OverflowGuard, QueueSnapshot, choose_node
from packages.gpu_control_core.security import derive_callback_secret, sign_callback_payload
from packages.gpu_control_core.settings import Settings, get_settings
from packages.gpu_control_core.storage import LocalJobStorage

DECISION = Histogram(
    "gpu_control_scheduler_decision_duration_seconds", "Scheduler decision latency"
)
LOOP_LAG = Gauge("gpu_control_scheduler_loop_lag_seconds", "Scheduler event loop lag")
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


class Scheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.storage = LocalJobStorage(settings.job_root)
        self.stop_event = asyncio.Event()
        self.wakeup = asyncio.Event()
        self.executions: dict[str, asyncio.Task[None]] = {}
        self.health_task: asyncio.Task[None] | None = None
        self.redis_task: asyncio.Task[None] | None = None
        self.callback_task: asyncio.Task[None] | None = None

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
                "redis_event_failed",
                event="redis.publish_failed",
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
                    "redis_listener_degraded",
                    event="redis.listen_failed",
                    error_code="REDIS_UNAVAILABLE",
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(2)

    async def callback_target_is_public(self, url: str) -> bool:
        host = urlparse(url).hostname
        if not host:
            return False
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM
            )
        except OSError:
            return False
        for record in records:
            address = ipaddress.ip_address(record[4][0])
            if not address.is_global:
                return False
        return bool(records)

    async def dispatch_one_callback(self) -> bool:
        now = datetime.now(UTC)
        async with self.db.session() as session:
            callback = await session.scalar(
                select(JobCallback)
                .join(Job, Job.id == JobCallback.job_id)
                .where(
                    JobCallback.status.in_(["PENDING", "RETRY"]),
                    JobCallback.next_attempt_at <= now,
                    Job.status.in_([status.value for status in TERMINAL_JOB_STATUSES]),
                )
                .order_by(JobCallback.next_attempt_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if callback is None:
                return False
            callback.status = "DELIVERING"
            job = await session.get(Job, callback.job_id)
            attempts = int(
                await session.scalar(
                    select(func.count(CallbackAttempt.id)).where(
                        CallbackAttempt.callback_id == callback.id
                    )
                )
                or 0
            )
            await session.commit()

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
        secret = derive_callback_secret(callback.id, self.settings.api_key_pepper)
        headers = {
            "Content-Type": "application/json",
            "X-GPU-Control-Timestamp": timestamp,
            "X-GPU-Control-Signature": sign_callback_payload(body, timestamp, secret),
            "X-Request-ID": job.request_id,
        }
        status_code: int | None = None
        error_code: str | None = None
        started = asyncio.get_running_loop().time()
        try:
            if not await self.callback_target_is_public(callback.url):
                raise RuntimeError(
                    "callback target did not resolve exclusively to public addresses"
                )
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(callback.url, content=body, headers=headers)
                status_code = response.status_code
                response.raise_for_status()
        except Exception as exc:
            error_code = "CALLBACK_DELIVERY_FAILED"
            logger().warning(
                "callback_delivery_failed",
                event="callback.failed",
                job_id=job.id,
                error_code=error_code,
                error_type=type(exc).__name__,
                attempt=attempts + 1,
            )
        duration_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        async with self.db.session() as session:
            current = await session.get(JobCallback, callback.id, with_for_update=True)
            if current is None:
                return True
            session.add(
                CallbackAttempt(
                    callback_id=current.id,
                    attempt=attempts + 1,
                    response_status=status_code,
                    error_code=error_code,
                    duration_ms=duration_ms,
                )
            )
            if error_code is None:
                current.status = "SUCCEEDED"
                current.next_attempt_at = None
                CALLBACK_ATTEMPTS.labels("success").inc()
            elif attempts + 1 >= 6:
                current.status = "FAILED"
                current.next_attempt_at = None
                CALLBACK_ATTEMPTS.labels("failed").inc()
                CALLBACK_FAILURES.inc()
            else:
                delays = (10, 60, 300, 1800, 7200)
                current.status = "RETRY"
                current.next_attempt_at = now + timedelta(
                    seconds=delays[min(attempts, len(delays) - 1)]
                )
                CALLBACK_ATTEMPTS.labels("retry").inc()
            await session.commit()
        return True

    async def callback_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                dispatched = await self.dispatch_one_callback()
            except Exception:
                logger().exception(
                    "callback_dispatcher_failed",
                    event="callback.dispatcher_failed",
                    error_code="CALLBACK_INTERNAL_ERROR",
                )
                dispatched = False
            if not dispatched:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=1)
                except TimeoutError:
                    continue

    async def update_node_health(self) -> None:
        while not self.stop_event.is_set():
            async with self.db.session() as session:
                nodes = list((await session.scalars(select(Node))).all())
                for node in nodes:
                    try:
                        async with ComfyClient(
                            node.base_url, connect_timeout=2, read_timeout=3
                        ) as client:
                            stats, queue = await asyncio.gather(
                                client.system_stats(), client.queue()
                            )
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
                        node.foreign_queue_detected = foreign
                        node.health = (
                            NodeHealth.DEGRADED.value if foreign else NodeHealth.ONLINE.value
                        )
                        node.last_heartbeat_at = datetime.now(UTC)
                        devices = stats.get("devices", [])
                        if devices:
                            device = devices[0]
                            node.free_vram_mb = int(device.get("vram_free", 0)) // (1024 * 1024)
                            node.total_vram_mb = int(device.get("vram_total", 0)) // (1024 * 1024)
                        NODE_HEALTH.labels(node.id).set(
                            1 if node.health == NodeHealth.ONLINE.value else 0
                        )
                        NODE_JOBS.labels(node.id).set(node.current_jobs)
                    except Exception as exc:
                        node.health = NodeHealth.OFFLINE.value
                        NODE_HEALTH.labels(node.id).set(0)
                        logger().warning(
                            "node_health_failed",
                            node_id=node.id,
                            event="node.health_failed",
                            error_code="COMFY_HEALTH_FAILED",
                            error_type=type(exc).__name__,
                        )
                await session.commit()
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

    async def reconcile(self) -> None:
        async with self.db.session() as session:
            delivering_callbacks = list(
                (
                    await session.scalars(
                        select(JobCallback).where(JobCallback.status == "DELIVERING")
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
                if job.prompt_id and job.node_id:
                    logger().info(
                        "recover_submitted_job",
                        job_id=job.id,
                        prompt_id=job.prompt_id,
                        node_id=job.node_id,
                        event="scheduler.recover_submitted",
                    )
                    self.executions[job.id] = asyncio.create_task(
                        self.execute(job.id, recovering=True)
                    )
                elif job.status in {JobStatus.CLAIMED.value, JobStatus.UPLOADING.value}:
                    await release_lease(session, job)
                    await transition_job(
                        session, job, JobStatus.RETRY_WAIT, "scheduler.recover_pre_submit"
                    )
                    await transition_job(session, job, JobStatus.QUEUED, "scheduler.requeued")
            await session.commit()

    async def schedule_available(self) -> None:
        started = asyncio.get_running_loop().time()
        while not self.stop_event.is_set():
            async with self.db.session() as session:
                snapshot = await self.queue_snapshot(session)
                if snapshot.depth == 0:
                    break
                nodes = list((await session.scalars(select(Node))).all())
                guard = await self.guard(session)
                node, exclusions = choose_node(
                    nodes, snapshot, guard, self.settings.node_heartbeat_timeout_seconds
                )
                if node is None:
                    logger().debug(
                        "no_eligible_node", event="scheduler.no_node", exclusions=exclusions
                    )
                    break
                await session.rollback()
                async with session.begin():
                    assignment = await claim_next_job(
                        session,
                        node.id,
                        self.settings.priority_aging_seconds,
                        queue_snapshot=snapshot,
                        overflow_guard=guard,
                        heartbeat_timeout_seconds=self.settings.node_heartbeat_timeout_seconds,
                    )
                if assignment is None:
                    break
                job, _ = assignment
                if node.pool == "OVERFLOW":
                    OVERFLOW.inc()
                logger().info(
                    "job_assigned",
                    event="scheduler.assigned",
                    job_id=job.id,
                    node_id=node.id,
                    candidates=len(nodes),
                    exclusions=exclusions,
                )
                self.executions[job.id] = asyncio.create_task(self.execute(job.id))
        DECISION.observe(asyncio.get_running_loop().time() - started)

    async def execute(self, job_id: str, recovering: bool = False) -> None:
        token = None
        timeout_event = asyncio.Event()
        timeout_task: asyncio.Task[None] | None = None
        try:
            async with self.db.session() as session:
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
                    if recovering and job.prompt_id:
                        history = await client.history(job.prompt_id)
                        if job.prompt_id in history:
                            await self.finish_from_history(session, job, workflow, client, history)
                            return
                        queue = await client.queue()
                        prompt_ids = {
                            str(item[1])
                            for key in ("queue_running", "queue_pending")
                            for item in queue.get(key, [])
                            if isinstance(item, list) and len(item) > 1
                        }
                        if job.prompt_id not in prompt_ids:
                            job.error_code = "COMFY_RECOVERY_UNKNOWN"
                            job.error_message = (
                                "prompt_id 不在 ComfyUI 队列或历史中，未盲目重复提交"
                            )
                            await transition_job(
                                session, job, JobStatus.FAILED, "scheduler.recovery_unknown"
                            )
                            await release_lease(session, job)
                            await session.commit()
                            return
                    if not job.prompt_id:
                        await transition_job(
                            session, job, JobStatus.UPLOADING, "executor.uploading"
                        )
                        await session.commit()
                        root = Path(job.job_dir)
                        uploads: list[dict[str, Any]] = []
                        for path in sorted((root / "input").glob("*")):
                            if path.is_file() and not path.name.endswith(".json"):
                                uploads.append(
                                    await client.upload(
                                        path, mask=path.name.startswith("mask-"), subfolder=job.id
                                    )
                                )
                        self.storage.atomic_json(root / "comfy" / "upload.responses.json", uploads)
                        rendered = json.loads(
                            (root / "workflow" / "rendered.api.json").read_text(encoding="utf-8")
                        )
                        prompt_id = await client.submit(rendered, f"gpu-control-{job.id}")
                        job.prompt_id = prompt_id
                        await transition_job(
                            session,
                            job,
                            JobStatus.SUBMITTED,
                            "executor.submitted",
                            {"prompt_id": prompt_id},
                        )
                        self.storage.atomic_json(
                            root / "comfy" / "submit.response.json", {"prompt_id": prompt_id}
                        )
                        await session.commit()
                    if job.cancel_requested:
                        await client.interrupt()
                        if job.status != JobStatus.CANCELLING.value:
                            await transition_job(
                                session, job, JobStatus.CANCELLING, "executor.cancelling"
                            )
                        await transition_job(
                            session, job, JobStatus.CANCELLED, "executor.cancelled"
                        )
                        await release_lease(session, job)
                        await session.commit()
                        return
                    if job.status == JobStatus.SUBMITTED.value:
                        await transition_job(session, job, JobStatus.RUNNING, "executor.running")
                        await session.commit()
                    cancellation_task = asyncio.create_task(self.watch_cancellation(job.id, client))
                    try:
                        try:
                            async for event in client.events(
                                job.prompt_id or "", f"gpu-control-{job.id}"
                            ):
                                if event.get("type") == "progress":
                                    data = event.get("data", {})
                                    maximum = max(float(data.get("max", 1)), 1)
                                    job.progress = min(
                                        99, float(data.get("value", 0)) / maximum * 100
                                    )
                                    await session.commit()
                                    await self.publish(
                                        {
                                            "event": "job.progress",
                                            "job_id": job.id,
                                            "progress": job.progress,
                                        }
                                    )
                        except ComfyError:
                            await session.refresh(job)
                            if not job.cancel_requested:
                                raise
                    finally:
                        cancellation_task.cancel()
                        await asyncio.gather(cancellation_task, return_exceptions=True)
                    await session.refresh(job)
                    if job.cancel_requested:
                        if job.status != JobStatus.CANCELLING.value:
                            await transition_job(
                                session, job, JobStatus.CANCELLING, "executor.cancelling"
                            )
                        await transition_job(
                            session, job, JobStatus.CANCELLED, "executor.cancelled"
                        )
                        await release_lease(session, job)
                        await session.commit()
                        await self.publish({"event": "job.cancelled", "job_id": job.id})
                        return
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
                "job_execution_failed",
                event="executor.failed",
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
                cancel_requested = await session.scalar(
                    select(Job.cancel_requested).where(Job.id == job_id)
                )
            if cancel_requested:
                try:
                    await client.interrupt()
                except Exception as exc:
                    logger().warning(
                        "cancel_interrupt_failed",
                        event="executor.cancel_interrupt_failed",
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
        timeout_event.set()
        try:
            await asyncio.wait_for(client.interrupt(), timeout=5)
        except Exception as exc:
            logger().warning(
                "timeout_interrupt_failed",
                event="executor.timeout_interrupt_failed",
                job_id=job_id,
                error_code="COMFY_INTERRUPT_FAILED",
                error_type=type(exc).__name__,
            )
        timed_out = False
        async with self.db.session() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if job is not None and JobStatus(job.status) not in TERMINAL_JOB_STATUSES:
                job.error_code = "JOB_TIMEOUT"
                job.error_message = f"任务超过工作流截止时间 {timeout_seconds} 秒"
                await transition_job(
                    session,
                    job,
                    JobStatus.TIMED_OUT,
                    "executor.timed_out",
                    {"timeout_seconds": timeout_seconds},
                )
                await release_lease(session, job)
                await session.commit()
                timed_out = True
        if timed_out:
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
        entry = history.get(job.prompt_id or "")
        if not entry:
            raise ComfyError("COMFY_OUTPUT_MISSING", "history does not contain prompt")
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyError("COMFY_EXECUTION_ERROR", "ComfyUI execution error", {"status": status})
        outputs = client.outputs(history, job.prompt_id or "")
        if not outputs:
            raise ComfyError("COMFY_OUTPUT_MISSING", "ComfyUI completed without outputs")
        if job.status != JobStatus.DOWNLOADING.value:
            await transition_job(session, job, JobStatus.DOWNLOADING, "executor.downloading")
            await session.commit()
        root = Path(job.job_dir)
        self.storage.atomic_json(root / "comfy" / "history.json", history)
        for index, output in enumerate(outputs):
            destination = root / "output" / f"{index:03d}-{Path(output.filename).name}"
            size, digest = await client.download(output, destination)
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
        job.progress = 100
        await transition_job(
            session, job, JobStatus.SUCCEEDED, "executor.succeeded", {"outputs": len(outputs)}
        )
        await release_lease(session, job)
        await session.commit()
        COMPLETED.labels(workflow.workflow_key).inc()
        await self.publish({"event": "job.succeeded", "job_id": job.id})

    async def fail_job(self, job_id: str, code: str, message: str) -> None:
        async with self.db.session() as session:
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None:
                return
            job.error_code = code
            job.error_message = message[:1000]
            if job.status not in {
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
                JobStatus.SUCCEEDED.value,
            }:
                if job.attempt_count < job.max_attempts and not job.prompt_id:
                    await transition_job(
                        session,
                        job,
                        JobStatus.RETRY_WAIT,
                        "executor.retry_wait",
                        {"error_code": code},
                    )
                    await release_lease(session, job)
                    await transition_job(session, job, JobStatus.QUEUED, "executor.retry_queued")
                else:
                    await transition_job(
                        session, job, JobStatus.FAILED, "executor.failed", {"error_code": code}
                    )
                    await release_lease(session, job)
            await session.commit()
        FAILED.labels(code).inc()
        await self.publish({"event": "job.failed", "job_id": job_id, "error_code": code})

    async def run(self) -> None:
        configure_logging("scheduler", self.settings.environment)
        start_http_server(9108)
        async with self.db.session() as lock_session:
            if not await self.db.acquire_scheduler_lock(lock_session):
                raise RuntimeError("another scheduler owns the PostgreSQL advisory lock")
            await self.reconcile()
            self.health_task = asyncio.create_task(self.update_node_health())
            self.redis_task = asyncio.create_task(self.redis_listener())
            self.callback_task = asyncio.create_task(self.callback_loop())
            expected = asyncio.get_running_loop().time()
            try:
                while not self.stop_event.is_set():
                    expected += self.settings.scheduler_fallback_scan_ms / 1000
                    await self.schedule_available()
                    LOOP_LAG.set(max(0, asyncio.get_running_loop().time() - expected))
                    self.wakeup.clear()
                    try:
                        await asyncio.wait_for(
                            self.wakeup.wait(),
                            timeout=self.settings.scheduler_fallback_scan_ms / 1000,
                        )
                    except TimeoutError:
                        continue
            finally:
                self.stop_event.set()
                for task in (self.health_task, self.redis_task, self.callback_task):
                    if task:
                        task.cancel()
                await asyncio.gather(
                    *(
                        task
                        for task in (self.health_task, self.redis_task, self.callback_task)
                        if task
                    ),
                    return_exceptions=True,
                )
                if self.executions:
                    await asyncio.gather(*self.executions.values(), return_exceptions=True)
                await self.db.release_scheduler_lock(lock_session)
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
