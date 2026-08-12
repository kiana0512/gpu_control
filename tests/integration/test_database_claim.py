import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import (
    Scheduler,
    mark_gpu_finished,
    persist_prompt_id,
    prepare_prompt_submission,
)
from packages.comfy_client import ComfyClient, ComfyError, ComfyOutput
from packages.gpu_control_core.batches import transition_batch
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import BatchStatus, JobStatus
from packages.gpu_control_core.models import (
    ApiClient,
    Base,
    BatchCancelOperation,
    CallbackAttempt,
    Job,
    JobArtifact,
    JobAttempt,
    JobBatch,
    JobBatchItem,
    JobCallback,
    JobEvent,
    Node,
    NodeLease,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import claim_next_job, prompt_client_id, release_lease
from packages.gpu_control_core.scheduling import (
    GPU_SPECIALIZATION_LABEL,
    MODELVIEW_INPAINT_NODE_ID,
    MODELVIEW_INPAINT_WORKFLOW_KEY,
    SUBSTANCE_DRAIN_OWNER,
    SUBSTANCE_DRAIN_OWNER_LABEL,
    SUBSTANCE_FENCE_LABEL,
    SUBSTANCE_PENDING_RESERVATION_LABEL,
    SUBSTANCE_RECOVERY_REQUIRED_LABEL,
)
from packages.gpu_control_core.settings import Settings
from tests.fake_comfyui.app import Behavior, State, create_app


async def make_database(path: Path) -> Database:
    database = Database(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}", job_root=path.parent / "jobs"
        )
    )
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


async def seed(database: Database) -> None:
    async with database.session() as session:
        session.add_all(
            [
                ApiClient(id="tenant-a", name="Tenant A", role="client", max_running=1),
                ApiClient(id="tenant-b", name="Tenant B", role="client", max_running=1),
                Node(
                    id="3090-a",
                    display_name="3090-A",
                    base_url="http://fake-a",
                    pool="PRIMARY",
                    mode="ACTIVE",
                    health="ONLINE",
                    max_concurrency=1,
                    current_jobs=0,
                    free_vram_mb=24000,
                    total_vram_mb=24576,
                    last_heartbeat_at=datetime.now(UTC),
                ),
                Workflow(key="fake", display_name="Fake", description="test"),
            ]
        )
        version = WorkflowVersion(
            workflow_key="fake",
            version="1",
            template={"9": {"class_type": "SaveImage", "inputs": {}}},
            parameter_schema={"type": "object"},
            bindings={},
            allowed_class_types=["SaveImage"],
            required_models=[],
            required_custom_nodes=[],
            min_vram_mb=0,
            timeout_seconds=60,
            node_labels={},
            output_nodes=["9"],
            enabled=True,
            template_sha256="x",
        )
        session.add(version)
        await session.flush()
        session.add(
            WorkflowNodeCompatibility(
                workflow_version_id=version.id, node_id="3090-a", compatible=True, reasons=[]
            )
        )
        for index, tenant in enumerate(("tenant-a", "tenant-b", "tenant-a")):
            session.add(
                Job(
                    id=f"job-{index}",
                    tenant_id=tenant,
                    workflow_key="fake",
                    workflow_version="1",
                    status="QUEUED",
                    priority="normal",
                    parameters={},
                    request_hash=str(index),
                    request_id=f"r{index}",
                    trace_id=f"t{index}",
                    job_dir=f"/tmp/job-{index}",
                )
            )
        await session.commit()


async def test_transactional_claim_enforces_single_node_slot(tmp_path: Path) -> None:
    database = await make_database(tmp_path / "claim.db")
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            first = await claim_next_job(session, "3090-a", 300)
        assert first is not None
    async with database.session() as session:
        async with session.begin():
            second = await claim_next_job(session, "3090-a", 300)
        assert second is None
    async with database.session() as session:
        claimed = await session.get(Job, first[0].id, with_for_update=True)
        assert claimed is not None
        await release_lease(session, claimed)
        await session.commit()
    async with database.session() as session:
        async with session.begin():
            third = await claim_next_job(session, "3090-a", 300)
        assert third is not None and third[0].tenant_id != first[0].tenant_id
    await database.close()


async def test_pinned_interactive_inpaint_can_use_fleet_capacity_above_tenant_default(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path / "interactive-inpaint-capacity.db")
    await seed(database)
    async with database.session() as session:
        node_a = await session.get(Node, "3090-a")
        assert node_a is not None
        node_a.current_jobs = 1
        session.add(
            Node(
                id="3090-b",
                display_name="3090-B",
                base_url="http://fake-b",
                pool="PRIMARY",
                mode="ACTIVE",
                health="ONLINE",
                max_concurrency=1,
                current_jobs=0,
                free_vram_mb=24000,
                total_vram_mb=24576,
                last_heartbeat_at=datetime.now(UTC),
            )
        )
        session.add(
            Workflow(
                key="modelview-inpaint",
                display_name="ModelView Inpaint",
                description="interactive test",
            )
        )
        version = WorkflowVersion(
            workflow_key="modelview-inpaint",
            version="1",
            template={"9": {"class_type": "SaveImage", "inputs": {}}},
            parameter_schema={"type": "object"},
            bindings={},
            allowed_class_types=["SaveImage"],
            required_models=[],
            required_custom_nodes=[],
            min_vram_mb=0,
            timeout_seconds=60,
            node_labels={},
            output_nodes=["9"],
            enabled=True,
            template_sha256="interactive",
        )
        session.add(version)
        await session.flush()
        session.add(
            WorkflowNodeCompatibility(
                workflow_version_id=version.id,
                node_id="3090-b",
                compatible=True,
                reasons=[],
            )
        )
        session.add_all(
            [
                Job(
                    id="already-running-for-tenant",
                    tenant_id="tenant-a",
                    workflow_key="fake",
                    workflow_version="1",
                    status=JobStatus.RUNNING.value,
                    priority="normal",
                    parameters={},
                    request_hash="already-running",
                    request_id="already-running",
                    trace_id="already-running",
                    job_dir=str(tmp_path / "already-running"),
                    node_id="3090-a",
                ),
                Job(
                    id="interactive-inpaint",
                    tenant_id="tenant-a",
                    workflow_key="modelview-inpaint",
                    workflow_version="1",
                    status=JobStatus.QUEUED.value,
                    priority="critical",
                    pinned=True,
                    parameters={},
                    request_hash="interactive-inpaint",
                    request_id="interactive-inpaint",
                    trace_id="interactive-inpaint",
                    job_dir=str(tmp_path / "interactive-inpaint"),
                ),
            ]
        )
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-b", 300, batch_max_running=3)

    assert claimed is not None
    assert claimed[0].id == "interactive-inpaint"
    await database.close()


async def test_gpu_claim_atomically_cleans_expired_substance_reservation(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path / "expired-substance-reservation.db")
    await seed(database)
    async with database.session() as session:
        node = await session.get(Node, "3090-a")
        assert node is not None
        node.mode = "DRAINING"
        node.labels = {
            SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER,
            SUBSTANCE_PENDING_RESERVATION_LABEL: {
                "job_ids": ["stale-production-bake"],
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        }
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None

    async with database.session() as session:
        node = await session.get(Node, "3090-a")
        assert node is not None
        assert node.mode == "ACTIVE"
        assert SUBSTANCE_DRAIN_OWNER_LABEL not in node.labels
        assert SUBSTANCE_PENDING_RESERVATION_LABEL not in node.labels
    await database.close()


async def test_health_probe_merges_concurrent_substance_interlocks(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "health-label-merge.db"
    database = await make_database(path)
    await seed(database)
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    probe_completed_at = datetime.now(UTC) - timedelta(seconds=30)
    recovery_observed_at = datetime.now(UTC)

    class ProbeClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = probe_completed_at
            return value if tz is not None else value.replace(tzinfo=None)

    class ConcurrentHealthClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def system_stats(self) -> dict[str, object]:
            # This commit occurs after Scheduler read its probe snapshot but
            # before Scheduler performs its health writeback.
            async with database.session() as session:
                node = await session.get(Node, "3090-a", with_for_update=True)
                assert node is not None
                node.mode = "DRAINING"
                node.labels = {
                    "concurrent_marker": "preserve-me",
                    SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER,
                    SUBSTANCE_PENDING_RESERVATION_LABEL: {
                        "job_ids": ["queued-bake"],
                        "expires_at": expires_at.isoformat(),
                    },
                    SUBSTANCE_FENCE_LABEL: ["running-bake"],
                    SUBSTANCE_RECOVERY_REQUIRED_LABEL: [
                        {
                            "job_id": "ambiguous-bake",
                            "worker_id": "asset-worker-3090-b-windows-01",
                            "lease_expired_at": datetime.now(UTC).isoformat(),
                            "idle_observed_at": recovery_observed_at.isoformat(),
                        }
                    ],
                }
                await session.commit()
            scheduler.stop_event.set()
            return {
                "devices": [
                    {
                        "vram_free": 12 * 1024 * 1024 * 1024,
                        "vram_total": 24 * 1024 * 1024 * 1024,
                    }
                ]
            }

        async def queue(self) -> dict[str, list[object]]:
            return {"queue_running": [], "queue_pending": []}

        async def object_info(self) -> dict[str, object]:
            return {"SaveImage": {}}

    async def no_identity(_: Node) -> None:
        return None

    async def no_agent_metrics(_: Node) -> None:
        return None

    monkeypatch.setattr(scheduler_main, "ComfyClient", ConcurrentHealthClient)
    monkeypatch.setattr(scheduler_main, "datetime", ProbeClock)
    scheduler.node_agent_identity = no_identity  # type: ignore[method-assign]
    scheduler.node_agent_gpu_metrics = no_agent_metrics  # type: ignore[method-assign]
    try:
        await scheduler.update_node_health()
        async with database.session() as session:
            node = await session.get(Node, "3090-a")
            assert node is not None
            assert node.mode == "DRAINING"
            assert node.labels["concurrent_marker"] == "preserve-me"
            assert node.labels[SUBSTANCE_DRAIN_OWNER_LABEL] == SUBSTANCE_DRAIN_OWNER
            assert node.labels[SUBSTANCE_FENCE_LABEL] == ["running-bake"]
            assert node.labels[SUBSTANCE_RECOVERY_REQUIRED_LABEL][0]["job_id"] == (
                "ambiguous-bake"
            )
            assert node.labels[SUBSTANCE_PENDING_RESERVATION_LABEL]["job_ids"] == [
                "queued-bake"
            ]
            assert node.labels["comfy_class_types"] == ["SaveImage"]
            assert node.health == "ONLINE"
            heartbeat = node.last_heartbeat_at
            assert heartbeat is not None
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            assert heartbeat == probe_completed_at
            assert heartbeat < recovery_observed_at
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_optional_inventory_timeout_does_not_fence_healthy_node(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "optional-inventory-timeout.db"
    database = await make_database(path)
    await seed(database)
    async with database.session() as session:
        node = await session.get(Node, "3090-a")
        assert node is not None
        node.labels = {
            "comfy_class_types": ["SaveImage"],
            "comfy_class_inventory_checked_at": datetime.now(UTC).isoformat(),
        }
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    scheduler.object_info_checked_at["3090-a"] = -60.0

    class BusyInventoryClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def system_stats(self) -> dict[str, object]:
            scheduler.stop_event.set()
            return {
                "devices": [
                    {
                        "vram_free": 12 * 1024 * 1024 * 1024,
                        "vram_total": 24 * 1024 * 1024 * 1024,
                    }
                ]
            }

        async def queue(self) -> dict[str, list[object]]:
            return {"queue_running": [], "queue_pending": []}

        async def object_info(self) -> dict[str, object]:
            raise ComfyError("COMFY_TIMEOUT", "busy optional inventory")

    async def no_identity(_: Node) -> None:
        return None

    async def no_agent_metrics(_: Node) -> None:
        return None

    monkeypatch.setattr(scheduler_main, "ComfyClient", BusyInventoryClient)
    scheduler.node_agent_identity = no_identity  # type: ignore[method-assign]
    scheduler.node_agent_gpu_metrics = no_agent_metrics  # type: ignore[method-assign]
    try:
        await scheduler.update_node_health()
        async with database.session() as session:
            node = await session.get(Node, "3090-a")
            assert node is not None
            assert node.health == "ONLINE"
            assert node.labels["comfy_class_types"] == ["SaveImage"]
            assert node.last_heartbeat_at is not None
        assert scheduler.object_info_checked_at["3090-a"] >= 0
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_completion_merges_labels_committed_after_node_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "completion-label-merge.db"
    database = await make_database(path)
    await seed(database)
    storage_root = tmp_path / "jobs"
    job_root = storage_root / "completion-label-job"
    for directory in ("comfy", "output"):
        (job_root / directory).mkdir(parents=True, exist_ok=True)

    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        node = await session.get(Node, "3090-a", with_for_update=True)
        assert job is not None and node is not None
        job.job_dir = str(job_root)
        job.status = JobStatus.RUNNING.value
        job.prompt_id = "prompt-label-merge"
        node.labels = {"snapshot_marker": "stale"}
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=storage_root,
        )
    )

    class CompletionClient:
        @staticmethod
        def outputs(
            _: dict[str, object],
            __: str,
            ___: set[str],
        ) -> list[ComfyOutput]:
            return [ComfyOutput("result.png", "", "output")]

        @staticmethod
        async def download(_: ComfyOutput, destination: Path) -> tuple[int, str]:
            payload = b"completed-output"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            return len(payload), "d" * 64

    async def no_publish(_: dict) -> None:
        return None

    scheduler.publish = no_publish  # type: ignore[method-assign]
    interlock_labels = {
        "concurrent_marker": "preserve-me",
        SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER,
        SUBSTANCE_PENDING_RESERVATION_LABEL: {
            "job_ids": ["queued-bake"],
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        SUBSTANCE_FENCE_LABEL: ["running-bake"],
        SUBSTANCE_RECOVERY_REQUIRED_LABEL: [
            {
                "job_id": "ambiguous-bake",
                "worker_id": "asset-worker-3090-b-windows-01",
                "lease_expired_at": datetime.now(UTC).isoformat(),
            }
        ],
    }
    try:
        async with scheduler.db.session() as completion_session:
            job = await completion_session.get(Job, job_id)
            workflow = await completion_session.scalar(select(WorkflowVersion))
            stale_node = await completion_session.get(Node, "3090-a")
            assert job is not None and workflow is not None and stale_node is not None
            assert stale_node.labels == {"snapshot_marker": "stale"}
            # Preserve the stale identity-map object across a transaction
            # boundary, just as execute() does after its intermediate commits.
            await completion_session.commit()

            async with database.session() as writer:
                current = await writer.get(Node, "3090-a", with_for_update=True)
                assert current is not None
                current.mode = "DRAINING"
                current.labels = interlock_labels
                await writer.commit()

            await scheduler.finish_from_history(
                completion_session,
                job,
                workflow,
                CompletionClient(),  # type: ignore[arg-type]
                {
                    "prompt-label-merge": {
                        "status": {"status_str": "success"},
                        "outputs": {},
                    }
                },
            )

        async with database.session() as session:
            node = await session.get(Node, "3090-a")
            completed = await session.get(Job, job_id)
            assert node is not None and completed is not None
            assert completed.status == JobStatus.SUCCEEDED.value
            assert node.mode == "DRAINING"
            assert node.labels["concurrent_marker"] == "preserve-me"
            assert node.labels[SUBSTANCE_DRAIN_OWNER_LABEL] == SUBSTANCE_DRAIN_OWNER
            assert node.labels[SUBSTANCE_FENCE_LABEL] == ["running-bake"]
            assert node.labels[SUBSTANCE_PENDING_RESERVATION_LABEL]["job_ids"] == [
                "queued-bake"
            ]
            assert node.labels[SUBSTANCE_RECOVERY_REQUIRED_LABEL][0]["job_id"] == (
                "ambiguous-bake"
            )
            assert node.labels["warm_workflow"] == "fake"
            assert "warm_workflow_at" in node.labels
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_callback_unknown_delivery_reuses_attempt_and_idempotency_key(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "callback-unknown-delivery.db"
    database = await make_database(path)
    await seed(database)
    callback_id = "callback-unknown-delivery"
    async with database.session() as session:
        job = await session.get(Job, "job-0", with_for_update=True)
        assert job is not None
        job.status = JobStatus.SUCCEEDED.value
        job.finished_at = datetime.now(UTC)
        session.add(
            JobCallback(
                id=callback_id,
                job_id=job.id,
                url="https://callback.example.com/hook",
                signing_secret_hash="unused-in-scheduler",
                status="PENDING",
                next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    idempotency_keys: list[str] = []

    class CallbackClient:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict[str, str],
        ) -> httpx.Response:
            del content
            idempotency_keys.append(headers["Idempotency-Key"])
            request = httpx.Request("POST", url)
            if len(idempotency_keys) == 1:
                raise httpx.ReadTimeout("response outcome is unknown", request=request)
            return httpx.Response(204, request=request)

    async def public_target(_: str) -> bool:
        return True

    monkeypatch.setattr(scheduler_main.httpx, "AsyncClient", CallbackClient)
    scheduler.callback_target_is_public = public_target  # type: ignore[method-assign]
    try:
        assert await scheduler.dispatch_one_callback() is True
        async with database.session() as session:
            callback = await session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.status == "DELIVERING"
            assert callback.next_attempt_at is not None
            assert await session.scalar(
                select(func.count(CallbackAttempt.id)).where(
                    CallbackAttempt.callback_id == callback_id
                )
            ) == 0

        # An unexpired ambiguous lease is not eligible for a concurrent replay.
        assert await scheduler.dispatch_one_callback() is False
        assert len(idempotency_keys) == 1

        async with database.session() as session:
            callback = await session.get(JobCallback, callback_id, with_for_update=True)
            assert callback is not None
            callback.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        assert await scheduler.dispatch_one_callback() is True
        assert idempotency_keys == [
            f"gpu-control-callback:{callback_id}:1",
            f"gpu-control-callback:{callback_id}:1",
        ]
        async with database.session() as session:
            callback = await session.get(JobCallback, callback_id)
            attempts = list(
                (
                    await session.scalars(
                        select(CallbackAttempt).where(
                            CallbackAttempt.callback_id == callback_id
                        )
                    )
                ).all()
            )
            assert callback is not None
            assert callback.status == "SUCCEEDED"
            assert callback.next_attempt_at is None
            assert [attempt.attempt for attempt in attempts] == [1]
            assert attempts[0].response_status == 204
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_callback_takeover_keeps_live_lease_and_ignores_late_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "callback-takeover.db"
    database = await make_database(path)
    await seed(database)
    callback_id = "callback-takeover"
    live_deadline = datetime.now(UTC) + timedelta(minutes=1)
    async with database.session() as session:
        job = await session.get(Job, "job-0", with_for_update=True)
        assert job is not None
        job.status = JobStatus.SUCCEEDED.value
        job.finished_at = datetime.now(UTC)
        session.add(
            JobCallback(
                id=callback_id,
                job_id=job.id,
                url="https://callback.example.com/hook",
                signing_secret_hash="unused-in-scheduler",
                status="DELIVERING",
                next_attempt_at=live_deadline,
            )
        )
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    try:
        # Startup reconciliation must not reset an unexpired delivery lease.
        await scheduler.reconcile()
        async with database.session() as session:
            callback = await session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.status == "DELIVERING"
            stored_deadline = callback.next_attempt_at
            assert stored_deadline is not None
            if stored_deadline.tzinfo is None:
                stored_deadline = stored_deadline.replace(tzinfo=UTC)
            assert stored_deadline == live_deadline

        old_deadline = live_deadline - timedelta(seconds=30)
        assert not await scheduler.finalize_callback_delivery(
            callback_id=callback_id,
            lease_deadline=old_deadline,
            attempt_number=1,
            status_code=200,
            error_code=None,
            duration_ms=10,
        )
        async with database.session() as session:
            callback = await session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.status == "DELIVERING"
            assert await session.scalar(
                select(func.count(CallbackAttempt.id)).where(
                    CallbackAttempt.callback_id == callback_id
                )
            ) == 0

        assert await scheduler.finalize_callback_delivery(
            callback_id=callback_id,
            lease_deadline=live_deadline,
            attempt_number=1,
            status_code=200,
            error_code=None,
            duration_ms=10,
        )
        async with database.session() as session:
            callback = await session.get(JobCallback, callback_id)
            attempt = await session.scalar(
                select(CallbackAttempt).where(
                    CallbackAttempt.callback_id == callback_id
                )
            )
            assert callback is not None and attempt is not None
            assert callback.status == "SUCCEEDED"
            assert attempt.attempt == 1
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_incompatible_workflow_is_not_claimed(tmp_path: Path) -> None:
    database = await make_database(tmp_path / "incompatible.db")
    await seed(database)
    async with database.session() as session:
        compatibility = await session.scalar(select(WorkflowNodeCompatibility))
        assert compatibility is not None
        compatibility.compatible = False
        await session.commit()
    async with database.session() as session:
        async with session.begin():
            assert await claim_next_job(session, "3090-a", 300) is None
    await database.close()


async def test_4070_specialization_filters_normal_gpu_work_and_hard_expires(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path / "4070-specialization.db")
    await seed(database)
    now = datetime.now(UTC)
    async with database.session() as session:
        version = await session.scalar(select(WorkflowVersion))
        assert version is not None
        node = Node(
            id=MODELVIEW_INPAINT_NODE_ID,
            display_name="4070Ti",
            base_url="http://fake-4070",
            pool="PRIMARY",
            mode="ACTIVE",
            health="ONLINE",
            labels={
                GPU_SPECIALIZATION_LABEL: {
                    "key": MODELVIEW_INPAINT_WORKFLOW_KEY,
                    "owner": "gpu-api",
                    "started_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=15)).isoformat(),
                }
            },
            max_concurrency=1,
            current_jobs=0,
            free_vram_mb=11000,
            total_vram_mb=12288,
            last_heartbeat_at=now,
        )
        session.add(node)
        await session.flush()
        session.add(
            WorkflowNodeCompatibility(
                workflow_version_id=version.id,
                node_id=node.id,
                compatible=True,
                reasons=[],
            )
        )
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            assert await claim_next_job(session, MODELVIEW_INPAINT_NODE_ID, 300) is None

    async with database.session() as session:
        node = await session.get(Node, MODELVIEW_INPAINT_NODE_ID)
        assert node is not None
        labels = dict(node.labels)
        labels[GPU_SPECIALIZATION_LABEL] = {
            "key": MODELVIEW_INPAINT_WORKFLOW_KEY,
            "owner": "gpu-api",
            "started_at": (now - timedelta(minutes=16)).isoformat(),
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
        }
        node.labels = labels
        await session.commit()
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, MODELVIEW_INPAINT_NODE_ID, 300)
        assert claimed is not None
        assert claimed[0].workflow_key == "fake"
    await database.close()


async def test_scheduler_skips_incompatible_node_and_claims_on_compatible_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mixed-node-compatibility.db"
    database = await make_database(path)
    await seed(database)
    async with database.session() as session:
        first_compatibility = await session.scalar(select(WorkflowNodeCompatibility))
        version = await session.scalar(select(WorkflowVersion))
        assert first_compatibility is not None and version is not None
        first_compatibility.compatible = False
        session.add(
            Node(
                id="3090-b",
                display_name="3090-B",
                base_url="http://fake-b",
                pool="PRIMARY",
                mode="ACTIVE",
                health="ONLINE",
                max_concurrency=1,
                current_jobs=0,
                free_vram_mb=24000,
                total_vram_mb=24576,
                last_heartbeat_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add(
            WorkflowNodeCompatibility(
                workflow_version_id=version.id,
                node_id="3090-b",
                compatible=True,
                reasons=[],
            )
        )
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )

    async def no_execute(_: str, recovering: bool = False) -> None:
        del recovering

    scheduler.execute = no_execute  # type: ignore[method-assign]
    try:
        await scheduler.schedule_available()
        if scheduler.executions:
            await asyncio.gather(*scheduler.executions.values())
        async with scheduler.db.session() as session:
            claimed = list(
                (
                    await session.scalars(
                        select(Job).where(Job.status == JobStatus.CLAIMED.value)
                    )
                ).all()
            )
            assert len(claimed) == 1
            assert claimed[0].node_id == "3090-b"
            first_node = await session.get(Node, "3090-a")
            assert first_node is not None and first_node.current_jobs == 0
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_workflow_pipeline_labels_are_rechecked_at_claim_time(tmp_path: Path) -> None:
    database = await make_database(tmp_path / "pipeline-label-gate.db")
    await seed(database)
    async with database.session() as session:
        node = await session.get(Node, "3090-a")
        version = await session.scalar(select(WorkflowVersion))
        assert node is not None and version is not None
        node.labels = {
            "imageclip_commit": "current",
            "imageclip_pipeline_sha256": "current-hash",
        }
        version.node_labels = {
            "imageclip_commit": "required",
            "imageclip_pipeline_sha256": "required-hash",
        }
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            assert await claim_next_job(session, "3090-a", 300) is None

    async with database.session() as session:
        node = await session.get(Node, "3090-a")
        assert node is not None
        node.labels = {
            "imageclip_commit": "required",
            "imageclip_pipeline_sha256": "required-hash",
        }
        await session.commit()
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
    await database.close()


async def test_batch_materialization_fails_closed_on_identity_drift(tmp_path: Path) -> None:
    path = tmp_path / "batch-identity-drift.db"
    database = await make_database(path)
    await seed(database)
    now = datetime.now(UTC)
    batch_id = "batch-identity-drift"
    async with database.session() as session:
        client = await session.get(ApiClient, "tenant-a")
        workflow = await session.scalar(select(WorkflowVersion))
        assert client is not None and workflow is not None
        client.max_queued = 100
        for queued_job in (await session.scalars(select(Job))).all():
            queued_job.status = JobStatus.CANCELLED.value
        workflow.node_labels = {
            "imageclip_commit": "2" * 40,
            "imageclip_pipeline_sha256": "3" * 64,
        }
        workflow.output_nodes = ["9"]
        workflow.template = {"9": {"class_type": "SaveImage", "inputs": {}}}
        session.add(
            JobBatch(
                id=batch_id,
                tenant_id="tenant-a",
                external_batch_id="assetclaw:identity-drift:g1",
                workflow_key=workflow.workflow_key,
                workflow_version=workflow.version,
                pipeline_commit="4" * 40,
                pipeline_sha256="5" * 64,
                output_node="SaveImage #9",
                status=BatchStatus.QUEUED.value,
                parameters={},
                request_hash="identity-drift",
                request_id="request-identity-drift",
                trace_id="trace-identity-drift",
                batch_dir=str(tmp_path / batch_id),
                manifest_sha256="a" * 64,
                archive_sha256="b" * 64,
                archive_size_bytes=1,
                total_items=1,
                pending_items=1,
                validated_at=now,
                queued_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            JobBatchItem(
                id="item-identity-drift",
                batch_id=batch_id,
                ordinal=0,
                input_relative_path="000.png",
                output_relative_path="000.png",
                input_size_bytes=1,
                input_sha256="c" * 64,
                width=1,
                height=1,
                image_format="PNG",
            )
        )
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    try:
        await scheduler.feed_batch_items()
        async with scheduler.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            assert batch is not None
            assert batch.status == BatchStatus.FAILED.value
            assert batch.error_code == "WORKFLOW_IDENTITY_DRIFT"
            assert await session.scalar(
                select(func.count(Job.id)).where(Job.batch_id == batch_id)
            ) == 0
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_production_jobs_precede_test_jobs_and_tests_use_idle_capacity(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path / "client-kind-priority.db")
    await seed(database)
    async with database.session() as session:
        test_client = await session.get(ApiClient, "tenant-b")
        assert test_client is not None
        test_client.client_kind = "test"
        production_jobs = list(
            (await session.scalars(select(Job).where(Job.tenant_id == "tenant-a"))).all()
        )
        test_job = await session.scalar(
            select(Job).where(Job.tenant_id == "tenant-b")
        )
        assert test_job is not None
        # Make the test job much older; production isolation must still win.
        test_job.created_at = datetime(2020, 1, 1, tzinfo=UTC)
        for extra in production_jobs[1:]:
            extra.status = JobStatus.CANCELLED.value
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            first = await claim_next_job(session, "3090-a", 300)
        assert first is not None and first[0].tenant_id == "tenant-a"

    async with database.session() as session:
        production = await session.get(Job, first[0].id, with_for_update=True)
        assert production is not None
        await release_lease(session, production)
        production.status = JobStatus.CANCELLED.value
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            second = await claim_next_job(session, "3090-a", 300)
        assert second is not None and second[0].tenant_id == "tenant-b"
    await database.close()


async def test_new_production_job_is_not_hidden_by_two_hundred_older_test_jobs(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path / "production-before-limit.db")
    await seed(database)
    now = datetime.now(UTC)
    async with database.session() as session:
        test_client = await session.get(ApiClient, "tenant-b")
        assert test_client is not None
        test_client.client_kind = "test"
        existing_jobs = list((await session.scalars(select(Job))).all())
        for existing in existing_jobs:
            existing.status = JobStatus.CANCELLED.value
        for index in range(225):
            session.add(
                Job(
                    id=f"old-test-{index:03d}",
                    tenant_id="tenant-b",
                    workflow_key="fake",
                    workflow_version="1",
                    status=JobStatus.QUEUED.value,
                    priority="normal",
                    parameters={},
                    request_hash=f"old-test-{index:03d}",
                    request_id=f"old-test-{index:03d}",
                    trace_id=f"old-test-{index:03d}",
                    job_dir=str(tmp_path / f"old-test-{index:03d}"),
                    created_at=now - timedelta(days=1, seconds=index),
                )
            )
        session.add(
            Job(
                id="new-production",
                tenant_id="tenant-a",
                workflow_key="fake",
                workflow_version="1",
                status=JobStatus.QUEUED.value,
                priority="normal",
                parameters={},
                request_hash="new-production",
                request_id="new-production",
                trace_id="new-production",
                job_dir=str(tmp_path / "new-production"),
                created_at=now,
            )
        )
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)

    assert claimed is not None
    assert claimed[0].id == "new-production"
    await database.close()


async def test_retry_reuses_durable_lease_with_fresh_token(tmp_path: Path) -> None:
    database = await make_database(tmp_path / "retry-claim.db")
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            first = await claim_next_job(session, "3090-a", 300)
        assert first is not None
        job_id = first[0].id
        lease_id = first[1].id
        first_token = first[1].token

    async with database.session() as session:
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        await release_lease(
            session,
            job,
            attempt_status=JobStatus.FAILED,
            attempt_error={"code": "TEST_FAILURE"},
        )
        job.status = JobStatus.QUEUED.value
        job.node_id = None
        job.prompt_id = None
        for other in (
            await session.scalars(select(Job).where(Job.id != job_id))
        ).all():
            other.status = JobStatus.CANCELLED.value
        await session.commit()

    async with database.session() as session:
        async with session.begin():
            retried = await claim_next_job(session, "3090-a", 300)
        assert retried is not None and retried[0].id == job_id
        assert retried[1].id == lease_id
        assert retried[1].token != first_token
        assert retried[1].active is True
        assert retried[0].submission_client_id == prompt_client_id(job_id, 2)
        assert retried[0].submission_intent_at is None
        attempts = list(
            (
                await session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == job_id)
                    .order_by(JobAttempt.attempt)
                )
            ).all()
        )
        assert [attempt.status for attempt in attempts] == ["FAILED", "CLAIMED"]
        assert [attempt.prompt_client_id for attempt in attempts] == [
            prompt_client_id(job_id, 1),
            prompt_client_id(job_id, 2),
        ]
        assert attempts[0].finished_at is not None
        assert attempts[0].error == {"code": "TEST_FAILURE"}
        assert await session.scalar(
            select(func.count(NodeLease.id)).where(NodeLease.job_id == job_id)
        ) == 1
    await database.close()


async def test_prompt_submission_intent_is_durable_and_counted_once(tmp_path: Path) -> None:
    database = await make_database(tmp_path / "submission-intent.db")
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job = await session.get(Job, claimed[0].id, with_for_update=True)
        assert job is not None
        expected = prompt_client_id(job.id, 1)
        assert await prepare_prompt_submission(session, job) == expected
        first_intent_at = job.submission_intent_at
        assert await prepare_prompt_submission(session, job) == expected
        await session.commit()

    async with database.session() as session:
        job = await session.get(Job, claimed[0].id)
        attempt = await session.scalar(
            select(JobAttempt).where(JobAttempt.job_id == claimed[0].id)
        )
        assert job is not None and attempt is not None
        assert job.submission_client_id == expected
        assert job.submission_intent_at is not None and first_intent_at is not None
        stored_intent_at = job.submission_intent_at
        if stored_intent_at.tzinfo is None:
            stored_intent_at = stored_intent_at.replace(tzinfo=UTC)
        if first_intent_at.tzinfo is None:
            first_intent_at = first_intent_at.replace(tzinfo=UTC)
        assert stored_intent_at == first_intent_at
        assert attempt.prompt_client_id == expected
        assert attempt.prompt_attempts == 1
    await database.close()


async def test_recovered_completion_does_not_fabricate_gpu_start_time(
    tmp_path: Path,
) -> None:
    database = await make_database(tmp_path / "missing-gpu-start.db")
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job = await session.get(Job, claimed[0].id, with_for_update=True)
        assert job is not None
        job.status = JobStatus.SUBMITTED.value
        await mark_gpu_finished(session, job)
        await session.commit()

    async with database.session() as session:
        attempt = await session.scalar(
            select(JobAttempt).where(JobAttempt.job_id == claimed[0].id)
        )
        assert attempt is not None
        assert attempt.gpu_started_at is None
        assert attempt.gpu_finished_at is not None
    await database.close()


async def test_scheduler_restart_routes_submit_intent_to_reconciliation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scheduler-restart-submit-intent.db"
    database = await make_database(path)
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        await prepare_prompt_submission(session, job)
        await session.commit()

    restarted = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    recovered: list[tuple[str, bool]] = []

    async def record_recovery(candidate_job_id: str, recovering: bool = False) -> None:
        recovered.append((candidate_job_id, recovering))

    restarted.execute = record_recovery  # type: ignore[method-assign]
    try:
        await restarted.reconcile()
        if restarted.executions:
            await asyncio.gather(*restarted.executions.values())
        assert recovered == [(job_id, True)]
        async with restarted.db.session() as session:
            durable_job = await session.get(Job, job_id)
            assert durable_job is not None
            assert durable_job.status == JobStatus.CLAIMED.value
            assert durable_job.attempt_count == 1
            assert durable_job.submission_intent_at is not None
            assert await session.scalar(
                select(func.count(JobAttempt.id)).where(JobAttempt.job_id == job_id)
            ) == 1
    finally:
        await restarted.redis.aclose()
        await restarted.db.close()
        await database.close()


async def test_post_accepted_then_persistence_crash_never_requeues_or_resubmits(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "post-accepted-persistence-crash.db"
    database = await make_database(path)
    await seed(database)
    job_root = tmp_path / "crash-job"
    for directory in ("input", "workflow", "comfy", "output"):
        (job_root / directory).mkdir(parents=True, exist_ok=True)
    (job_root / "workflow" / "rendered.api.json").write_text(
        '{"9":{"class_type":"SaveImage","inputs":{}}}',
        encoding="utf-8",
    )
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        job.job_dir = str(job_root)
        await session.commit()

    state = State(behavior=Behavior(duration_seconds=60))

    def fake_comfy_client(_: str) -> ComfyClient:
        return ComfyClient(
            "http://fake",
            transport=httpx.ASGITransport(app=create_app(state)),
        )

    original_persist_prompt_id = scheduler_main.persist_prompt_id

    async def crash_before_prompt_id_commit(session, job, prompt_id: str) -> None:
        await original_persist_prompt_id(session, job, prompt_id)
        raise RuntimeError("injected crash before prompt_id commit")

    monkeypatch.setattr(scheduler_main, "ComfyClient", fake_comfy_client)
    monkeypatch.setattr(
        scheduler_main, "persist_prompt_id", crash_before_prompt_id_commit
    )
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )

    async def no_publish(_: dict) -> None:
        return None

    scheduler.publish = no_publish  # type: ignore[method-assign]
    try:
        await scheduler.execute(job_id)
        assert len(state.prompts) == 1
        async with scheduler.db.session() as session:
            failed = await session.get(Job, job_id)
            assert failed is not None
            assert failed.status == JobStatus.FAILED.value
            assert failed.attempt_count == 1
            assert failed.prompt_id is None
            assert failed.submission_client_id == prompt_client_id(job_id, 1)
            assert failed.submission_intent_at is not None

        # A fresh scheduler ignores this terminal fail-closed job.  In
        # particular, it cannot clear the intent, requeue the attempt, or issue
        # a second POST /prompt for the same frame.
        restarted = Scheduler(
            Settings(
                database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
                job_root=tmp_path / "jobs",
            )
        )
        try:
            await restarted.reconcile()
            assert restarted.executions == {}
            assert len(state.prompts) == 1
        finally:
            await restarted.redis.aclose()
            await restarted.db.close()
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_cancelled_batch_requires_locked_valid_cancel_audit(tmp_path: Path) -> None:
    path = tmp_path / "cancel-audit-gate.db"
    database = await make_database(path)
    await seed(database)
    now = datetime.now(UTC)

    async def add_terminal_cancel(
        batch_id: str, *, with_operation: bool
    ) -> None:
        async with database.session() as session:
            session.add(
                JobBatch(
                    id=batch_id,
                    tenant_id="tenant-a",
                    external_batch_id=f"assetclaw:{batch_id}",
                    workflow_key="fake",
                    workflow_version="1",
                    status=BatchStatus.CANCELLING.value,
                    parameters={},
                    request_hash=f"hash-{batch_id}",
                    request_id=f"request-{batch_id}",
                    trace_id=f"trace-{batch_id}",
                    batch_dir=str(tmp_path / batch_id),
                    manifest_sha256="a" * 64,
                    archive_sha256="b" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    cancel_requested=True,
                    validated_at=now,
                    queued_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                JobBatchItem(
                    id=f"item-{batch_id}",
                    batch_id=batch_id,
                    ordinal=0,
                    input_relative_path="000.png",
                    output_relative_path="000.png",
                    input_size_bytes=1,
                    input_sha256="c" * 64,
                    width=1,
                    height=1,
                    image_format="PNG",
                    status="CANCELLED",
                )
            )
            if with_operation:
                session.add(
                    BatchCancelOperation(
                        id=f"operation-{batch_id}",
                        batch_id=batch_id,
                        tenant_id="tenant-a",
                        idempotency_key=f"assetclaw:{batch_id}:cancel",
                        request_id=f"cancel-request-{batch_id}",
                        requested_by="tenant-a",
                        source="public_api",
                        reason="test cancellation",
                        status="REQUESTED",
                        requested_at=now,
                        accepted_at=now,
                    )
                )
            await session.commit()

    await add_terminal_cancel("missing-audit", with_operation=False)
    await add_terminal_cancel("valid-audit", with_operation=True)
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    try:
        assert await scheduler.sync_batch_state("missing-audit") is False
        assert await scheduler.sync_batch_state("valid-audit") is False
        async with scheduler.db.session() as session:
            missing = await session.get(JobBatch, "missing-audit")
            valid = await session.get(JobBatch, "valid-audit")
            operation = await session.get(BatchCancelOperation, "operation-valid-audit")
            assert missing is not None and valid is not None and operation is not None
            assert missing.status == BatchStatus.FAILED.value
            assert missing.error_code == "CANCEL_AUDIT_MISSING"
            assert valid.status == BatchStatus.CANCELLED.value
            assert operation.status == "COMPLETED"
            assert operation.finished_at is not None
            assert operation.cancelled_items == 1
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_batch_transitions_do_not_fabricate_gpu_timestamps(tmp_path: Path) -> None:
    database = await make_database(tmp_path / "batch-gpu-timestamps.db")
    await seed(database)
    now = datetime.now(UTC)
    batch_id = "batch-without-gpu-evidence"
    async with database.session() as session:
        batch = JobBatch(
            id=batch_id,
            tenant_id="tenant-a",
            external_batch_id="assetclaw:no-gpu-evidence:g1",
            workflow_key="fake",
            workflow_version="1",
            status=BatchStatus.QUEUED.value,
            parameters={},
            request_hash="no-gpu-evidence",
            request_id="request-no-gpu-evidence",
            trace_id="trace-no-gpu-evidence",
            batch_dir=str(tmp_path / batch_id),
            manifest_sha256="a" * 64,
            archive_sha256="b" * 64,
            archive_size_bytes=1,
            total_items=1,
            queued_items=1,
            validated_at=now,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        await session.flush()
        await transition_batch(session, batch, BatchStatus.RUNNING, "test.running")
        assert batch.started_at is None
        await transition_batch(session, batch, BatchStatus.ASSEMBLING, "test.assembling")
        assert batch.execution_finished_at is None
        assert batch.assembling_at is not None
        await transition_batch(session, batch, BatchStatus.SUCCEEDED, "test.succeeded")
        assert batch.execution_finished_at is None
        assert batch.artifact_ready_at is not None
        assert batch.finished_at is not None
        await session.commit()

    async with database.session() as session:
        persisted = await session.get(JobBatch, batch_id)
        assert persisted is not None
        assert persisted.started_at is None
        assert persisted.execution_finished_at is None
    await database.close()


async def test_cancel_committed_after_upload_prevents_prompt_submission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "cancel-after-upload.db"
    database = await make_database(path)
    await seed(database)
    job_root = tmp_path / "cancel-after-upload-job"
    for directory in ("input", "workflow", "comfy", "output"):
        (job_root / directory).mkdir(parents=True, exist_ok=True)
    (job_root / "input" / "source.png").write_bytes(b"input")
    (job_root / "workflow" / "rendered.api.json").write_text(
        '{"9":{"class_type":"SaveImage","inputs":{}}}',
        encoding="utf-8",
    )
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        job.job_dir = str(job_root)
        await session.commit()

    upload_calls = 0
    submit_calls = 0

    class CancelAfterUploadClient:
        def __init__(self, _: str) -> None:
            pass

        async def free(self) -> dict[str, object]:
            return {"released": True}

        async def queue(self) -> dict[str, object]:
            return {"queue_running": [], "queue_pending": []}

        async def system_stats(self) -> dict[str, object]:
            return {
                "devices": [
                    {
                        "vram_total": 24 * 1024 * 1024 * 1024,
                        "vram_free": 23 * 1024 * 1024 * 1024,
                    }
                ]
            }

        async def upload(
            self,
            _: Path,
            *,
            mask: bool,
            subfolder: str,
        ) -> dict[str, object]:
            nonlocal upload_calls
            upload_calls += 1
            assert mask is False
            assert subfolder == job_id
            async with database.session() as cancellation_session:
                cancelling = await cancellation_session.get(
                    Job,
                    job_id,
                    with_for_update=True,
                )
                assert cancelling is not None
                cancelling.cancel_requested = True
                cancelling.status = JobStatus.CANCELLING.value
                await cancellation_session.commit()
            return {"attempt": 1, "overwrite": True, "verified": True}

        async def submit(self, _: dict[str, object], __: str) -> str:
            nonlocal submit_calls
            submit_calls += 1
            return "must-not-be-submitted"

        async def close(self) -> None:
            return None

    monkeypatch.setattr(scheduler_main, "ComfyClient", CancelAfterUploadClient)
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    published: list[dict[str, object]] = []

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.publish = record_publish  # type: ignore[method-assign]
    try:
        await scheduler.execute(job_id)
        assert upload_calls == 1
        assert submit_calls == 0
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert job.prompt_id is None
            assert job.submission_intent_at is None
            assert attempt.prompt_attempts == 0
            assert attempt.status == JobStatus.CANCELLED.value
            assert lease.active is False
            assert node.current_jobs == 0
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_cancel_during_submission_recovery_interrupts_before_terminal_cancel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "cancel-during-submission-recovery.db"
    database = await make_database(path)
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        client_id = await prepare_prompt_submission(session, job)
        await session.commit()

    recovered_prompt_id = "accepted-before-recovery-cancel"
    interrupt_calls: list[str] = []

    class CancelDuringRecoveryClient:
        def __init__(self, _: str) -> None:
            pass

        async def prompt_ids_for_client(self, candidate_client_id: str) -> list[str]:
            assert candidate_client_id == client_id
            async with database.session() as cancellation_session:
                cancelling = await cancellation_session.get(
                    Job,
                    job_id,
                    with_for_update=True,
                )
                assert cancelling is not None
                cancelling.cancel_requested = True
                cancelling.status = JobStatus.CANCELLING.value
                await cancellation_session.commit()
            return [recovered_prompt_id]

        async def interrupt(self) -> dict[str, object]:
            interrupt_calls.append("interrupt")
            return {"interrupted": True}

        async def close(self) -> None:
            return None

    monkeypatch.setattr(scheduler_main, "ComfyClient", CancelDuringRecoveryClient)
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    published: list[dict[str, object]] = []
    cancel_calls: list[str] = []
    cancel_locked_job = scheduler.cancel_locked_job

    async def assert_interrupt_precedes_cancel(session, job, *, event: str) -> None:
        assert interrupt_calls == ["interrupt"]
        cancel_calls.append(event)
        await cancel_locked_job(session, job, event=event)

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.cancel_locked_job = assert_interrupt_precedes_cancel  # type: ignore[method-assign]
    scheduler.publish = record_publish  # type: ignore[method-assign]
    try:
        await scheduler.execute(job_id, recovering=True)
        assert interrupt_calls == ["interrupt"]
        assert cancel_calls == ["scheduler.cancelled_after_submission_recovery"]
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            forbidden_events = await session.scalar(
                select(func.count(JobEvent.id)).where(
                    JobEvent.job_id == job_id,
                    JobEvent.status.in_(
                        [
                            JobStatus.SUBMITTED.value,
                            JobStatus.FAILED.value,
                        ]
                    ),
                )
            )
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert job.prompt_id == recovered_prompt_id
            assert job.error_code is None
            assert attempt.prompt_id == recovered_prompt_id
            assert attempt.status == JobStatus.CANCELLED.value
            assert forbidden_events == 0
            assert lease.active is False
            assert node.current_jobs == 0
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_cancel_committed_during_download_prevents_artifact_publish(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cancel-during-download.db"
    database = await make_database(path)
    await seed(database)
    storage_root = tmp_path / "jobs"
    job_root = storage_root / "cancel-during-download-job"
    for directory in ("comfy", "output"):
        (job_root / directory).mkdir(parents=True, exist_ok=True)
    prompt_id = "cancel-during-download-prompt"
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        job.job_dir = str(job_root)
        await prepare_prompt_submission(session, job)
        await persist_prompt_id(session, job, prompt_id)
        job.status = JobStatus.RUNNING.value
        await session.commit()

    download_destinations: list[Path] = []

    class CancelDuringDownloadClient:
        @staticmethod
        def outputs(
            _: dict[str, object],
            __: str,
            ___: set[str],
        ) -> list[ComfyOutput]:
            return [ComfyOutput("result.png", "", "output")]

        @staticmethod
        async def download(_: ComfyOutput, destination: Path) -> tuple[int, str]:
            payload = b"downloaded-but-not-published"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            download_destinations.append(destination)
            async with database.session() as cancellation_session:
                cancelling = await cancellation_session.get(
                    Job,
                    job_id,
                    with_for_update=True,
                )
                assert cancelling is not None
                cancelling.cancel_requested = True
                cancelling.status = JobStatus.CANCELLING.value
                await cancellation_session.commit()
            return len(payload), "d" * 64

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=storage_root,
        )
    )
    published: list[dict[str, object]] = []

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.publish = record_publish  # type: ignore[method-assign]
    try:
        async with scheduler.db.session() as completion_session:
            job = await completion_session.get(Job, job_id)
            workflow = await completion_session.scalar(select(WorkflowVersion))
            assert job is not None and workflow is not None
            await scheduler.finish_from_history(
                completion_session,
                job,
                workflow,
                CancelDuringDownloadClient(),  # type: ignore[arg-type]
                {
                    prompt_id: {
                        "status": {"status_str": "success"},
                        "outputs": {},
                    }
                },
            )

        assert len(download_destinations) == 1
        assert not (job_root / "output" / "000-result.png").exists()
        assert list((job_root / "output").glob("000-*")) == []
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            artifact_count = await session.scalar(
                select(func.count(JobArtifact.id)).where(JobArtifact.job_id == job_id)
            )
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert attempt.status == JobStatus.CANCELLED.value
            assert lease.active is False
            assert node.current_jobs == 0
            assert artifact_count == 0
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_late_start_and_progress_events_cannot_revive_cancelled_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "late-events-after-cancel.db"
    database = await make_database(path)
    await seed(database)
    prompt_id = "late-events-prompt"
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        await prepare_prompt_submission(session, job)
        await persist_prompt_id(session, job, prompt_id)
        job.status = JobStatus.SUBMITTED.value
        await session.commit()

    class LateEventsClient:
        def __init__(self, _: str) -> None:
            pass

        async def events(
            self,
            candidate_prompt_id: str,
            _: str,
        ) -> AsyncIterator[dict[str, object]]:
            assert candidate_prompt_id == prompt_id
            async with database.session() as cancellation_session:
                cancelling = await cancellation_session.get(
                    Job,
                    job_id,
                    with_for_update=True,
                )
                assert cancelling is not None
                cancelling.cancel_requested = True
                cancelling.status = JobStatus.CANCELLING.value
                await cancellation_session.commit()
            yield {
                "type": "execution_start",
                "data": {"prompt_id": candidate_prompt_id},
            }
            yield {
                "type": "progress",
                "data": {
                    "prompt_id": candidate_prompt_id,
                    "value": 99,
                    "max": 100,
                },
            }
            yield {
                "type": "executing",
                "data": {"prompt_id": candidate_prompt_id, "node": "9"},
            }
            yield {
                "type": "execution_interrupted",
                "data": {"prompt_id": candidate_prompt_id},
            }

        async def interrupt(self) -> dict[str, object]:
            return {"interrupted": True}

        async def close(self) -> None:
            return None

    monkeypatch.setattr(scheduler_main, "ComfyClient", LateEventsClient)
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    published: list[dict[str, object]] = []

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.publish = record_publish  # type: ignore[method-assign]
    try:
        await scheduler.execute(job_id)
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            running_events = await session.scalar(
                select(func.count(JobEvent.id)).where(
                    JobEvent.job_id == job_id,
                    JobEvent.status == JobStatus.RUNNING.value,
                )
            )
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert job.started_at is None
            assert job.progress == 0
            assert attempt.gpu_started_at is None
            assert attempt.gpu_finished_at is not None
            assert attempt.status == JobStatus.CANCELLED.value
            assert running_events == 0
            assert lease.active is False
            assert node.current_jobs == 0
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_timeout_watchdog_preserves_authenticated_cancellation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "timeout-boundary-cancel.db"
    database = await make_database(path)
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        job.cancel_requested = True
        job.status = JobStatus.CANCELLING.value
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    interrupt_calls: list[str] = []
    published: list[dict[str, object]] = []

    class RecordingClient:
        async def interrupt(self) -> dict[str, object]:
            interrupt_calls.append("interrupt")
            return {"interrupted": True}

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.publish = record_publish  # type: ignore[method-assign]
    parent_task = asyncio.create_task(asyncio.Event().wait())
    timeout_event = asyncio.Event()
    try:
        await scheduler.timeout_watchdog(
            job_id,
            timeout_seconds=0,
            client=RecordingClient(),  # type: ignore[arg-type]
            parent_task=parent_task,
            timeout_event=timeout_event,
        )
        await asyncio.gather(parent_task, return_exceptions=True)

        assert interrupt_calls == ["interrupt"]
        assert parent_task.cancelled()
        assert not timeout_event.is_set()
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            timed_out_events = await session.scalar(
                select(func.count(JobEvent.id)).where(
                    JobEvent.job_id == job_id,
                    JobEvent.status == JobStatus.TIMED_OUT.value,
                )
            )
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert job.error_code is None
            assert attempt.status == JobStatus.CANCELLED.value
            assert lease.active is False
            assert node.current_jobs == 0
            assert timed_out_events == 0
    finally:
        if not parent_task.done():
            parent_task.cancel()
            await asyncio.gather(parent_task, return_exceptions=True)
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_executor_error_racing_with_cancel_does_not_retry_or_fail(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cancel-error-race.db"
    database = await make_database(path)
    await seed(database)
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        job.status = JobStatus.CANCELLING.value
        job.cancel_requested = True
        await session.commit()

    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    published: list[dict[str, object]] = []

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.publish = record_publish  # type: ignore[method-assign]
    try:
        await scheduler.fail_job(job_id, "COMFY_CONNECT_ERROR", "connection dropped")
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert job.attempt_count == 1
            assert attempt.status == JobStatus.CANCELLED.value
            assert lease.active is False
            assert node.current_jobs == 0
        assert published == [{"event": "job.cancelled", "job_id": job_id}]

        async with scheduler.db.session() as session:
            terminal = await session.scalar(select(Job).where(Job.id != job_id).limit(1))
            assert terminal is not None
            terminal_id = terminal.id
            terminal.status = JobStatus.SUCCEEDED.value
            terminal.error_code = None
            terminal.error_message = None
            await session.commit()
        await scheduler.fail_job(
            terminal_id, "LATE_TRANSPORT_ERROR", "must not rewrite success"
        )
        async with scheduler.db.session() as session:
            terminal = await session.get(Job, terminal_id)
            assert terminal is not None
            assert terminal.status == JobStatus.SUCCEEDED.value
            assert terminal.error_code is None
            assert terminal.error_message is None
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()


async def test_execution_interrupted_finishes_gpu_timing_and_durable_cancel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "execution-interrupted-cancel.db"
    database = await make_database(path)
    await seed(database)
    prompt_id = "interrupted-prompt"
    async with database.session() as session:
        async with session.begin():
            claimed = await claim_next_job(session, "3090-a", 300)
        assert claimed is not None
        job_id = claimed[0].id
        job = await session.get(Job, job_id, with_for_update=True)
        assert job is not None
        await prepare_prompt_submission(session, job)
        await persist_prompt_id(session, job, prompt_id)
        job.status = JobStatus.RUNNING.value
        await session.commit()

    class InterruptedClient:
        def __init__(self, _: str) -> None:
            pass

        async def events(
            self, candidate_prompt_id: str, _: str
        ) -> AsyncIterator[dict[str, object]]:
            assert candidate_prompt_id == prompt_id
            async with database.session() as cancellation_session:
                cancelling = await cancellation_session.get(
                    Job, job_id, with_for_update=True
                )
                assert cancelling is not None
                cancelling.cancel_requested = True
                cancelling.status = JobStatus.CANCELLING.value
                await cancellation_session.commit()
            yield {
                "type": "execution_interrupted",
                "data": {"prompt_id": candidate_prompt_id},
            }

        async def close(self) -> None:
            return None

    monkeypatch.setattr(scheduler_main, "ComfyClient", InterruptedClient)
    scheduler = Scheduler(
        Settings(
            database_url=f"sqlite+aiosqlite:///{path.as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    published: list[dict[str, object]] = []

    async def record_publish(payload: dict[str, object]) -> None:
        published.append(payload)

    scheduler.publish = record_publish  # type: ignore[method-assign]
    try:
        await scheduler.execute(job_id)
        async with scheduler.db.session() as session:
            job = await session.get(Job, job_id)
            attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.job_id == job_id)
            )
            lease = await session.scalar(
                select(NodeLease).where(NodeLease.job_id == job_id)
            )
            node = await session.get(Node, "3090-a")
            assert job is not None and attempt is not None and lease is not None
            assert node is not None
            assert job.status == JobStatus.CANCELLED.value
            assert attempt.gpu_finished_at is not None
            assert attempt.status == JobStatus.CANCELLED.value
            assert lease.active is False
            assert node.current_jobs == 0
        assert published == [{"event": "job.cancelled", "job_id": job_id}]
    finally:
        await scheduler.redis.aclose()
        await scheduler.db.close()
        await database.close()
