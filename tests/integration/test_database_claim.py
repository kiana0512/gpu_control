import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import (
    Scheduler,
    mark_gpu_finished,
    prepare_prompt_submission,
)
from packages.comfy_client import ComfyClient
from packages.gpu_control_core.batches import transition_batch
from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import BatchStatus, JobStatus
from packages.gpu_control_core.models import (
    ApiClient,
    Base,
    BatchCancelOperation,
    Job,
    JobAttempt,
    JobBatch,
    JobBatchItem,
    Node,
    NodeLease,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import claim_next_job, prompt_client_id, release_lease
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
