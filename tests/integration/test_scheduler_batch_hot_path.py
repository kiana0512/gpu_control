import asyncio
import hashlib
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import Scheduler
from packages.gpu_control_core import batches as batch_module
from packages.gpu_control_core import database as database_module
from packages.gpu_control_core.batches import BuiltBatchArchive
from packages.gpu_control_core.enums import BatchItemStatus, BatchStatus, JobStatus
from packages.gpu_control_core.models import (
    ApiClient,
    Base,
    BatchArtifact,
    Job,
    JobArtifact,
    JobBatch,
    JobBatchItem,
    Node,
    Workflow,
    WorkflowVersion,
)
from packages.gpu_control_core.settings import Settings

PIPELINE_COMMIT = "1" * 40
PIPELINE_SHA256 = "2" * 64


async def make_scheduler(tmp_path: Path, **overrides: Any) -> Scheduler:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'scheduler.db').as_posix()}",
        job_root=tmp_path / "jobs",
        **overrides,
    )
    scheduler = Scheduler(settings)
    async with scheduler.db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with scheduler.db.session() as session:
        session.add_all(
            [
                ApiClient(
                    id="tenant-a",
                    name="Tenant A",
                    role="client",
                    max_queued=100,
                    max_running=3,
                ),
                ApiClient(
                    id="tenant-b",
                    name="Tenant B",
                    role="client",
                    max_queued=100,
                    max_running=3,
                ),
                Node(
                    id="worker-a",
                    display_name="Worker A",
                    base_url="http://127.0.0.1:18188",
                    mode="ACTIVE",
                    health="ONLINE",
                ),
                Node(
                    id="worker-b",
                    display_name="Worker B",
                    base_url="http://127.0.0.1:28188",
                    mode="ACTIVE",
                    health="ONLINE",
                ),
                Workflow(key="fake", display_name="Fake", description="test"),
            ]
        )
        session.add(
            WorkflowVersion(
                workflow_key="fake",
                version="1",
                template={
                    "9": {
                        "class_type": "SaveImage",
                        "inputs": {"filename_prefix": ""},
                    }
                },
                parameter_schema={
                    "type": "object",
                    "properties": {"image_filename": {"type": "string"}},
                    "required": ["image_filename"],
                    "additionalProperties": False,
                },
                bindings={"image_filename": "9.inputs.filename_prefix"},
                allowed_class_types=["SaveImage"],
                required_models=[],
                required_custom_nodes=[],
                min_vram_mb=0,
                timeout_seconds=60,
                node_labels={
                    "imageclip_commit": PIPELINE_COMMIT,
                    "imageclip_pipeline_sha256": PIPELINE_SHA256,
                },
                output_nodes=["9"],
                enabled=True,
                template_sha256="3" * 64,
            )
        )
        await session.commit()
    return scheduler


async def add_batch(
    scheduler: Scheduler,
    batch_id: str,
    tenant_id: str,
    total_items: int,
    *,
    created_at: datetime,
    running_items: int = 0,
) -> None:
    batch_dir = scheduler.settings.job_root / "batch-fixtures" / batch_id
    input_dir = batch_dir / "input"
    input_dir.mkdir(parents=True)
    payloads: list[bytes] = []
    for ordinal in range(total_items):
        payload = f"{batch_id}:{ordinal}".encode()
        payloads.append(payload)
        (input_dir / f"{ordinal:03d}.png").write_bytes(payload)

    async with scheduler.db.session() as session:
        batch = JobBatch(
            id=batch_id,
            tenant_id=tenant_id,
            external_batch_id=f"assetclaw:{batch_id}",
            workflow_key="fake",
            workflow_version="1",
            pipeline_commit=PIPELINE_COMMIT,
            pipeline_sha256=PIPELINE_SHA256,
            output_node="SaveImage #9",
            status=BatchStatus.QUEUED.value,
            parameters={},
            request_hash=hashlib.sha256(batch_id.encode()).hexdigest(),
            request_id=f"request-{batch_id}",
            trace_id=f"trace-{batch_id}",
            batch_dir=str(batch_dir),
            manifest_sha256="4" * 64,
            archive_sha256="5" * 64,
            archive_size_bytes=sum(map(len, payloads)),
            total_items=total_items,
            pending_items=total_items - running_items,
            running_items=running_items,
            validated_at=created_at,
            queued_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(batch)
        items: list[JobBatchItem] = []
        for ordinal, payload in enumerate(payloads):
            item = JobBatchItem(
                id=f"item-{batch_id}-{ordinal}",
                batch_id=batch_id,
                ordinal=ordinal,
                input_relative_path=f"{ordinal:03d}.png",
                output_relative_path=f"{ordinal:03d}.png",
                input_size_bytes=len(payload),
                input_sha256=hashlib.sha256(payload).hexdigest(),
                width=1,
                height=1,
                image_format="PNG",
                status=(
                    BatchItemStatus.RUNNING.value
                    if ordinal < running_items
                    else BatchItemStatus.PENDING.value
                ),
            )
            items.append(item)
            session.add(item)
        await session.flush()
        for item in items[:running_items]:
            job = Job(
                id=f"running-{batch_id}-{item.ordinal}",
                tenant_id=tenant_id,
                workflow_key="fake",
                workflow_version="1",
                status=JobStatus.RUNNING.value,
                priority="batch",
                parameters={},
                request_hash=f"running-{batch_id}-{item.ordinal}",
                request_id=f"request-{batch_id}",
                trace_id=f"trace-{batch_id}",
                job_dir=str(scheduler.settings.job_root / f"running-{batch_id}-{item.ordinal}"),
                batch_id=batch_id,
                batch_item_id=item.id,
                created_at=created_at,
            )
            session.add(job)
            await session.flush()
            item.job_id = job.id
        await session.commit()


async def add_completed_batch(
    scheduler: Scheduler,
    batch_id: str,
    total_items: int,
) -> dict[int, str]:
    now = datetime.now(UTC)
    await add_batch(
        scheduler,
        batch_id,
        "tenant-a",
        total_items,
        created_at=now,
    )
    latest_sha_by_ordinal: dict[int, str] = {}
    async with scheduler.db.session() as session:
        batch = await session.get(JobBatch, batch_id)
        items = list(
            (
                await session.scalars(
                    select(JobBatchItem)
                    .where(JobBatchItem.batch_id == batch_id)
                    .order_by(JobBatchItem.ordinal)
                )
            ).all()
        )
        assert batch is not None
        batch.status = BatchStatus.RUNNING.value
        batch.pending_items = 0
        batch.queued_items = total_items
        for item in items:
            job = Job(
                id=f"completed-{batch_id}-{item.ordinal}",
                tenant_id="tenant-a",
                workflow_key="fake",
                workflow_version="1",
                status=JobStatus.SUCCEEDED.value,
                priority="batch",
                parameters={},
                request_hash=f"completed-{batch_id}-{item.ordinal}",
                request_id=f"request-{batch_id}",
                trace_id=f"trace-{batch_id}",
                job_dir=str(scheduler.settings.job_root / f"completed-{batch_id}-{item.ordinal}"),
                batch_id=batch_id,
                batch_item_id=item.id,
                finished_at=now,
                created_at=now,
            )
            session.add(job)
            await session.flush()
            item.job_id = job.id
            item.status = BatchItemStatus.QUEUED.value
            old_sha = f"{item.ordinal + 10:064x}"
            latest_sha = f"{item.ordinal + 100:064x}"
            latest_sha_by_ordinal[item.ordinal] = latest_sha
            session.add_all(
                [
                    JobArtifact(
                        id=f"artifact-old-{item.ordinal}",
                        job_id=job.id,
                        kind="output",
                        relative_path=f"output/old-{item.ordinal}.png",
                        content_type="image/png",
                        size_bytes=10,
                        sha256=old_sha,
                        created_at=now - timedelta(minutes=1),
                    ),
                    JobArtifact(
                        id=f"artifact-new-{item.ordinal}",
                        job_id=job.id,
                        kind="output",
                        relative_path=f"output/new-{item.ordinal}.png",
                        content_type="image/png",
                        size_bytes=20,
                        sha256=latest_sha,
                        created_at=now,
                    ),
                ]
            )
        await session.commit()
    return latest_sha_by_ordinal


def sql_recorder(scheduler: Scheduler) -> tuple[list[str], Any]:
    statements: list[str] = []

    def record(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select"):
            statements.append(normalized)

    event.listen(scheduler.db.engine.sync_engine, "before_cursor_execute", record)
    return statements, record


async def close_scheduler(scheduler: Scheduler) -> None:
    await scheduler.redis.aclose()
    await scheduler.db.close()


async def test_sync_and_assembly_bulk_read_latest_artifacts_in_ordinal_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = await make_scheduler(tmp_path)
    expected_sha = await add_completed_batch(scheduler, "batch-completed", 6)
    statements, recorder = sql_recorder(scheduler)
    try:
        assert await scheduler.sync_batch_state("batch-completed") is True
        artifact_selects = [value for value in statements if "job_artifacts" in value]
        assert len(artifact_selects) == 1
        assert "row_number() over" in artifact_selects[0]
        async with scheduler.db.session() as session:
            items = list(
                (
                    await session.scalars(
                        select(JobBatchItem)
                        .where(JobBatchItem.batch_id == "batch-completed")
                        .order_by(JobBatchItem.ordinal)
                    )
                ).all()
            )
            assert [item.output_sha256 for item in items] == [
                expected_sha[ordinal] for ordinal in range(6)
            ]

        captured_ordinals: list[int] = []
        expected_archive_sha = hashlib.sha256(b"archive").hexdigest()

        def fake_archive(
            _batch_id: str,
            _external_batch_id: str,
            batch_dir: Path,
            frames: list[batch_module.ArchiveFrame],
            _workflow_identity: dict[str, str | None],
            staging_path: Path,
            _cancel_event: threading.Event,
            _total_items: int,
        ) -> BuiltBatchArchive:
            captured_ordinals.extend(frame.ordinal for frame in frames)
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(b"archive")
            return BuiltBatchArchive(
                path=staging_path,
                size_bytes=len(b"archive"),
                sha256=expected_archive_sha,
                manifest={},
            )

        async def no_publish(_payload: dict[str, Any]) -> None:
            return None

        monkeypatch.setattr(scheduler_main, "build_result_archive", fake_archive)
        scheduler.publish = no_publish  # type: ignore[method-assign]
        statements.clear()
        await scheduler.assemble_batch("batch-completed")
        assert captured_ordinals == list(range(6))
        assert len([value for value in statements if "job_artifacts" in value]) == 1
        assert len([value for value in statements if re.search(r"\bfrom jobs\b", value)]) == 1
        async with scheduler.db.session() as session:
            batch = await session.get(JobBatch, "batch-completed")
            artifact = await session.scalar(
                select(BatchArtifact).where(BatchArtifact.batch_id == "batch-completed")
            )
            assert batch is not None and batch.status == BatchStatus.SUCCEEDED.value
            assert artifact is not None and artifact.sha256 == expected_archive_sha
    finally:
        event.remove(scheduler.db.engine.sync_engine, "before_cursor_execute", recorder)
        await close_scheduler(scheduler)


async def test_cancelled_old_archive_thread_cannot_overwrite_new_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = await make_scheduler(tmp_path)
    await add_completed_batch(scheduler, "batch-race", 1)
    assert await scheduler.sync_batch_state("batch-race") is True
    old_started = threading.Event()
    release_old = threading.Event()
    old_finished = threading.Event()
    staging_paths: list[Path] = []
    invocation_lock = threading.Lock()
    invocation = 0
    new_payload = b"new-leader-archive"
    old_payload = b"stale-old-leader-archive"

    def racing_archive(
        _batch_id: str,
        _external_batch_id: str,
        _batch_dir: Path,
        _frames: list[batch_module.ArchiveFrame],
        _workflow_identity: dict[str, str | None],
        staging_path: Path,
        _cancel_event: threading.Event,
        _total_items: int,
    ) -> BuiltBatchArchive:
        nonlocal invocation
        with invocation_lock:
            invocation += 1
            call = invocation
        staging_paths.append(staging_path)
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        if call == 1:
            staging_path.write_bytes(old_payload)
            old_started.set()
            assert release_old.wait(timeout=5)
            old_finished.set()
            payload = old_payload
        else:
            staging_path.write_bytes(new_payload)
            payload = new_payload
        return BuiltBatchArchive(
            path=staging_path,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            manifest={},
        )

    async def no_publish(_payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(scheduler_main, "build_result_archive", racing_archive)
    monkeypatch.setattr(scheduler_main, "ARCHIVE_BUILD_CANCEL_GRACE_SECONDS", 0.01)
    scheduler.publish = no_publish  # type: ignore[method-assign]
    try:
        old_task = asyncio.create_task(scheduler.assemble_batch("batch-race"))
        assert await asyncio.to_thread(old_started.wait, 2)
        old_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old_task

        await scheduler.assemble_batch("batch-race")
        release_old.set()
        assert await asyncio.to_thread(old_finished.wait, 2)

        final_path = (
            tmp_path
            / "jobs"
            / "batch-fixtures"
            / "batch-race"
            / "output"
            / "batch-race-rgba.zip"
        )
        assert final_path.read_bytes() == new_payload
        assert len(staging_paths) == 2
        assert staging_paths[0] != staging_paths[1]
        assert all(path != final_path for path in staging_paths)
        assert staging_paths[0].read_bytes() == old_payload
        async with scheduler.db.session() as session:
            artifact = await session.scalar(
                select(BatchArtifact).where(BatchArtifact.batch_id == "batch-race")
            )
            assert artifact is not None
            assert artifact.sha256 == hashlib.sha256(new_payload).hexdigest()
            assert artifact.sha256 == hashlib.sha256(final_path.read_bytes()).hexdigest()
    finally:
        release_old.set()
        await close_scheduler(scheduler)


async def test_feed_materializes_multiple_frames_round_robin_with_bulk_lookups(
    tmp_path: Path,
) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=6,
        default_tenant_max_queued=6,
        batch_feed_window=4,
    )
    now = datetime.now(UTC)
    await add_batch(scheduler, "batch-a", "tenant-a", 5, created_at=now)
    await add_batch(
        scheduler,
        "batch-b",
        "tenant-a",
        5,
        created_at=now + timedelta(milliseconds=1),
    )
    async with scheduler.db.session() as session:
        client = await session.get(ApiClient, "tenant-a")
        assert client is not None
        client.max_queued = 6
        await session.commit()

    statements, recorder = sql_recorder(scheduler)
    try:
        await scheduler.feed_batch_items()
        feed_statements = list(statements)
        async with scheduler.db.session() as session:
            rows = (
                await session.execute(
                    select(JobBatchItem.batch_id, JobBatchItem.ordinal)
                    .where(JobBatchItem.job_id.is_not(None))
                    .order_by(JobBatchItem.batch_id, JobBatchItem.ordinal)
                )
            ).all()
            by_batch = {
                batch_id: [ordinal for row_batch_id, ordinal in rows if row_batch_id == batch_id]
                for batch_id in ("batch-a", "batch-b")
            }
            assert by_batch == {"batch-a": [0, 1, 2], "batch-b": [0, 1, 2]}
            assert (
                await session.scalar(
                    select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value)
                )
                == 6
            )

        assert len([value for value in feed_statements if "from api_clients" in value]) == 2
        assert len([value for value in feed_statements if "from workflow_versions" in value]) == 1
        assert len([value for value in feed_statements if "count(jobs.id)" in value]) == 2
        assert (
            len([value for value in feed_statements if "count(job_batch_items.id)" in value]) == 3
        )
        bounded_pending = [
            value
            for value in feed_statements
            if "row_number() over" in value and "job_batch_items" in value
        ]
        assert len(bounded_pending) == 1
        assert "case" in bounded_pending[0]
    finally:
        event.remove(scheduler.db.engine.sync_engine, "before_cursor_execute", recorder)
        await close_scheduler(scheduler)


async def test_feed_respects_system_tenant_and_batch_window_limits(tmp_path: Path) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=4,
        default_tenant_max_queued=10,
        batch_feed_window=3,
    )
    now = datetime.now(UTC)
    await add_batch(
        scheduler,
        "batch-a",
        "tenant-a",
        5,
        created_at=now,
        running_items=1,
    )
    await add_batch(
        scheduler,
        "batch-b",
        "tenant-b",
        5,
        created_at=now + timedelta(milliseconds=1),
    )
    async with scheduler.db.session() as session:
        tenant_a = await session.get(ApiClient, "tenant-a")
        assert tenant_a is not None
        tenant_a.max_queued = 3
        session.add(
            Job(
                id="existing-queued-a",
                tenant_id="tenant-a",
                workflow_key="fake",
                workflow_version="1",
                status=JobStatus.QUEUED.value,
                priority="normal",
                parameters={},
                request_hash="existing-queued-a",
                request_id="existing-queued-a",
                trace_id="existing-queued-a",
                job_dir=str(tmp_path / "existing-queued-a"),
                created_at=now,
            )
        )
        await session.commit()
    try:
        await scheduler.feed_batch_items()
        async with scheduler.db.session() as session:
            assert (
                await session.scalar(
                    select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value)
                )
                == 4
            )
            assert (
                await session.scalar(
                    select(func.count(Job.id)).where(
                        Job.tenant_id == "tenant-a",
                        Job.status == JobStatus.QUEUED.value,
                    )
                )
                == 3
            )
            batch_a_window = await session.scalar(
                select(func.count(JobBatchItem.id)).where(
                    JobBatchItem.batch_id == "batch-a",
                    JobBatchItem.status.in_(
                        [BatchItemStatus.QUEUED.value, BatchItemStatus.RUNNING.value]
                    ),
                )
            )
            batch_b_window = await session.scalar(
                select(func.count(JobBatchItem.id)).where(
                    JobBatchItem.batch_id == "batch-b",
                    JobBatchItem.status.in_(
                        [BatchItemStatus.QUEUED.value, BatchItemStatus.RUNNING.value]
                    ),
                )
            )
            assert batch_a_window == 3
            assert batch_b_window == 1
    finally:
        await close_scheduler(scheduler)


async def test_feed_reserves_system_queue_headroom_from_test_batches(
    tmp_path: Path,
) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=4,
        system_production_queue_reserve=2,
        default_tenant_max_queued=10,
        batch_feed_window=10,
    )
    await add_batch(
        scheduler,
        "test-batch",
        "tenant-b",
        6,
        created_at=datetime.now(UTC),
    )
    async with scheduler.db.session() as session:
        test_client = await session.get(ApiClient, "tenant-b")
        assert test_client is not None
        test_client.client_kind = "test"
        await session.commit()

    try:
        await scheduler.feed_batch_items()
        async with scheduler.db.session() as session:
            queued = int(
                await session.scalar(
                    select(func.count(Job.id)).where(
                        Job.status == JobStatus.QUEUED.value
                    )
                )
                or 0
            )
            materialized = int(
                await session.scalar(
                    select(func.count(Job.id)).where(Job.batch_id == "test-batch")
                )
                or 0
            )
        assert queued == 2
        assert materialized == 2
    finally:
        await close_scheduler(scheduler)


async def test_feed_pauses_old_test_batch_when_production_arrives(
    tmp_path: Path,
) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=8,
        system_production_queue_reserve=2,
        default_tenant_max_queued=10,
        batch_feed_window=8,
    )
    now = datetime.now(UTC)
    await add_batch(
        scheduler,
        "old-test-batch",
        "tenant-b",
        6,
        created_at=now - timedelta(hours=1),
    )
    await add_batch(
        scheduler,
        "new-production-batch",
        "tenant-a",
        3,
        created_at=now,
    )
    async with scheduler.db.session() as session:
        test_client = await session.get(ApiClient, "tenant-b")
        assert test_client is not None
        test_client.client_kind = "test"
        await session.commit()

    try:
        await scheduler.feed_batch_items()
        async with scheduler.db.session() as session:
            test_jobs = int(
                await session.scalar(
                    select(func.count(Job.id)).where(Job.batch_id == "old-test-batch")
                )
                or 0
            )
            production_jobs = int(
                await session.scalar(
                    select(func.count(Job.id)).where(
                        Job.batch_id == "new-production-batch"
                    )
                )
                or 0
            )
        assert test_jobs == 0
        assert production_jobs == 3
    finally:
        await close_scheduler(scheduler)


async def test_feed_rolls_back_database_and_all_promoted_job_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=10,
        default_tenant_max_queued=10,
        batch_feed_window=3,
    )
    await add_batch(
        scheduler,
        "batch-cleanup",
        "tenant-a",
        3,
        created_at=datetime.now(UTC),
    )
    real_transition = batch_module.transition_job
    transition_calls = 0

    async def fail_during_second_item(*args: Any, **kwargs: Any) -> None:
        nonlocal transition_calls
        transition_calls += 1
        if transition_calls == 3:
            raise RuntimeError("injected materialization failure")
        await real_transition(*args, **kwargs)

    monkeypatch.setattr(batch_module, "transition_job", fail_during_second_item)
    try:
        with pytest.raises(RuntimeError, match="injected materialization failure"):
            await scheduler.feed_batch_items()
        async with scheduler.db.session() as session:
            assert (
                await session.scalar(
                    select(func.count(Job.id)).where(Job.batch_id == "batch-cleanup")
                )
                == 0
            )
            items = list(
                (
                    await session.scalars(
                        select(JobBatchItem).where(JobBatchItem.batch_id == "batch-cleanup")
                    )
                ).all()
            )
            assert all(item.status == BatchItemStatus.PENDING.value for item in items)
            assert all(item.job_id is None for item in items)
        assert list(scheduler.settings.job_root.rglob("request.sanitized.json")) == []
    finally:
        await close_scheduler(scheduler)


def test_round_robin_allocations_obey_all_limits_and_tick_cap() -> None:
    allocations = scheduler_main.round_robin_feed_allocations(
        [
            ("batch-a1", "tenant-a"),
            ("batch-b1", "tenant-b"),
            ("batch-a2", "tenant-a"),
        ],
        {"batch-a1": 4, "batch-b1": 4, "batch-a2": 4},
        {"tenant-a": 3, "tenant-b": 2},
        total_budget=10,
    )

    assert allocations == {"batch-a1": 2, "batch-b1": 2, "batch-a2": 1}
    assert sum(allocations.values()) == 5
    assert scheduler_main.materialization_tick_budget(100, 4, 2) == 8
    assert scheduler_main.materialization_tick_budget(1000, 100, 100) == 32


def test_global_admission_lock_uses_distinct_signed_bigint_key() -> None:
    assert 2**31 - 1 < database_module.ADMISSION_LOCK_ID <= 2**63 - 1
    assert database_module.ADMISSION_LOCK_ID != database_module.SCHEDULER_LOCK_ID


def test_bounded_pending_query_compiles_for_postgresql() -> None:
    statement = scheduler_main.bounded_pending_items_statement(
        {"batch-a": 2, "batch-b": 1}
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "row_number() over" in sql
    assert "case" in sql
    assert "for update of job_batch_items skip locked" in sql


async def test_feed_acquires_global_then_sorted_tenant_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=2,
        default_tenant_max_queued=2,
        batch_feed_window=1,
    )
    now = datetime.now(UTC)
    await add_batch(scheduler, "batch-b", "tenant-b", 1, created_at=now)
    await add_batch(
        scheduler,
        "batch-a",
        "tenant-a",
        1,
        created_at=now + timedelta(milliseconds=1),
    )
    lock_calls: list[str] = []

    async def record_global_lock(_session: AsyncSession) -> None:
        lock_calls.append("global")

    async def record_tenant_lock(_session: AsyncSession, tenant_id: str) -> None:
        lock_calls.append(f"tenant:{tenant_id}")

    monkeypatch.setattr(
        scheduler.db,
        "acquire_global_admission_transaction_lock",
        record_global_lock,
    )
    monkeypatch.setattr(
        scheduler.db,
        "acquire_tenant_transaction_lock",
        record_tenant_lock,
    )
    try:
        await scheduler.feed_batch_items()
        assert lock_calls == ["global", "tenant:tenant-a", "tenant:tenant-b"]
    finally:
        await close_scheduler(scheduler)


async def test_feed_hard_caps_materialization_rows_per_tick(tmp_path: Path) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=100,
        default_tenant_max_queued=100,
        batch_feed_window=100,
    )
    await add_batch(
        scheduler,
        "batch-cap",
        "tenant-a",
        100,
        created_at=datetime.now(UTC),
    )
    try:
        await scheduler.feed_batch_items()
        async with scheduler.db.session() as session:
            assert (
                await session.scalar(
                    select(func.count(Job.id)).where(Job.batch_id == "batch-cap")
                )
                == scheduler_main.MAX_BATCH_MATERIALIZATIONS_PER_TICK
            )
    finally:
        await close_scheduler(scheduler)


async def test_feed_preserves_job_dirs_when_commit_outcome_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = await make_scheduler(
        tmp_path,
        system_max_queued=10,
        default_tenant_max_queued=10,
        batch_feed_window=3,
    )
    await add_batch(
        scheduler,
        "batch-commit-unknown",
        "tenant-a",
        3,
        created_at=datetime.now(UTC),
    )
    real_commit = AsyncSession.commit
    injected = False

    async def commit_then_raise(session: AsyncSession) -> None:
        nonlocal injected
        await real_commit(session)
        if not injected:
            injected = True
            raise RuntimeError("injected unknown commit outcome")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_raise)
    try:
        with pytest.raises(RuntimeError, match="unknown commit outcome"):
            await scheduler.feed_batch_items()
        assert injected is True
        async with scheduler.db.session() as session:
            persisted_jobs = list(
                (
                    await session.scalars(
                        select(Job)
                        .where(Job.batch_id == "batch-commit-unknown")
                        .order_by(Job.id)
                    )
                ).all()
            )
        assert len(persisted_jobs) == 3
        assert all(
            (Path(job.job_dir) / "request.sanitized.json").is_file()
            for job in persisted_jobs
        )
    finally:
        await close_scheduler(scheduler)
