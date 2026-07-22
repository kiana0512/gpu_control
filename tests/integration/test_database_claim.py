from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.models import (
    ApiClient,
    Base,
    Job,
    Node,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import claim_next_job, release_lease
from packages.gpu_control_core.settings import Settings


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
