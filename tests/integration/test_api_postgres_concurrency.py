import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from gpu_control_api.main import create_app
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from packages.gpu_control_core.database import Database
from packages.gpu_control_core.enums import JobStatus
from packages.gpu_control_core.models import (
    ApiClient,
    AuditLog,
    Base,
    Job,
    JobBatch,
    Node,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.repository import claim_next_job
from packages.gpu_control_core.security import hash_password
from packages.gpu_control_core.settings import Settings


def postgres_url() -> str:
    url = os.environ.get("GPU_CONTROL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("GPU_CONTROL_TEST_POSTGRES_URL is required for PostgreSQL lock tests")
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost"} or not str(
        parsed.database or ""
    ).startswith("gpu_control_test_"):
        pytest.fail(
            "destructive PostgreSQL concurrency test requires a loopback "
            "gpu_control_test_* disposable database"
        )
    return url


@pytest.mark.parametrize("locked_row", ["batch", "job"])
async def test_node_interrupt_fails_fast_on_scheduler_owned_rows(
    tmp_path: Path,
    locked_row: str,
) -> None:
    settings = Settings(
        environment="test",
        database_url=postgres_url(),
        redis_url="redis://127.0.0.1:6399/15",
        job_root=tmp_path / "jobs",
        jwt_secret="test-jwt",
        api_key_pepper="test-pepper",
        node_agent_hmac_secret="test-agent",
        alertmanager_webhook_token="development-only-change-me",
    )
    app = create_app(settings)
    batch_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    async with app.router.lifespan_context(app):
        async with app.state.db.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.db.session() as db:
            db.add(
                ApiClient(
                    id="admin",
                    name="admin",
                    role="admin",
                    password_hash=hash_password("correct-password"),
                    max_queued=20,
                    max_running=3,
                )
            )
            db.add(
                Node(
                    id="worker-3090-a",
                    display_name="3090-A",
                    base_url="http://127.0.0.1:8188",
                    pool="PRIMARY",
                    mode="ACTIVE",
                    health="ONLINE",
                    current_jobs=1,
                    max_concurrency=1,
                    labels={},
                )
            )
            db.add(
                JobBatch(
                    id=batch_id,
                    tenant_id="admin",
                    external_batch_id=f"interrupt-lock:{locked_row}",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    pipeline_commit="7" * 40,
                    pipeline_sha256="8" * 64,
                    output_node="SaveImage #9",
                    status="RUNNING",
                    failure_policy="all_or_nothing",
                    output_naming="preserve_stem_png",
                    parameters={},
                    request_hash="a" * 64,
                    request_id=f"interrupt-lock-{locked_row}",
                    trace_id=f"interrupt-lock-{locked_row}",
                    batch_dir=str(tmp_path / batch_id),
                    manifest_sha256="b" * 64,
                    archive_sha256="c" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    created_at=now,
                    validated_at=now,
                    queued_at=now,
                    started_at=now,
                    updated_at=now,
                )
            )
            await db.commit()
            db.add(
                Job(
                    id=job_id,
                    tenant_id="admin",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    status="RUNNING",
                    priority="batch",
                    parameters={},
                    request_hash="d" * 64,
                    request_id=f"interrupt-job-{locked_row}",
                    trace_id=f"interrupt-job-{locked_row}",
                    job_dir=str(tmp_path / job_id),
                    batch_id=batch_id,
                    node_id="worker-3090-a",
                    created_at=now,
                )
            )
            await db.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/admin/auth/login",
                json={"username": "admin", "password": "correct-password"},
            )
            auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
            lock_session = app.state.db.sessions()
            try:
                await lock_session.begin()
                model = JobBatch if locked_row == "batch" else Job
                row_id = batch_id if locked_row == "batch" else job_id
                locked = await lock_session.scalar(
                    select(model).where(model.id == row_id).with_for_update()
                )
                assert locked is not None

                response = await asyncio.wait_for(
                    client.post(
                        "/admin/nodes/worker-3090-a/interrupt",
                        headers=auth,
                        json={"reason": "postgres lock-order proof", "confirm": True},
                    ),
                    timeout=2,
                )
                assert response.status_code == 409, response.text
                assert response.json()["detail"]["code"] == "NODE_INTERRUPT_BUSY_RETRY"
            finally:
                await lock_session.rollback()
                await lock_session.close()

        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-a")
            job = await db.get(Job, job_id)
            retry_audit = await db.scalar(
                select(AuditLog).where(
                    AuditLog.action == "node.interrupt.busy_retry",
                    AuditLog.target_id == "worker-3090-a",
                )
            )
            assert node is not None and node.mode == "ACTIVE"
            assert job is not None and job.status == "RUNNING"
            assert job.cancel_requested is False
            assert retry_audit is not None and retry_audit.result == "REJECTED"
        async with app.state.db.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))


async def test_concurrent_postgres_claims_cannot_skip_production_behind_test_backlog(
    tmp_path: Path,
) -> None:
    database = Database(
        Settings(
            environment="test",
            database_url=postgres_url(),
            job_root=tmp_path / "jobs",
            jwt_secret="test-jwt",
            api_key_pepper="test-pepper",
            node_agent_hmac_secret="test-agent",
            alertmanager_webhook_token="development-only-change-me",
        )
    )
    now = datetime.now(UTC)
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as db:
            db.add_all(
                [
                    ApiClient(
                        id="production",
                        name="Production",
                        role="client",
                        client_kind="production",
                        max_running=3,
                    ),
                    ApiClient(
                        id="load-test",
                        name="Load test",
                        role="client",
                        client_kind="test",
                        max_running=3,
                    ),
                    Node(
                        id="worker-a",
                        display_name="Worker A",
                        base_url="http://127.0.0.1:18188",
                        pool="PRIMARY",
                        mode="ACTIVE",
                        health="ONLINE",
                        max_concurrency=1,
                        current_jobs=0,
                    ),
                    Node(
                        id="worker-b",
                        display_name="Worker B",
                        base_url="http://127.0.0.1:28188",
                        pool="PRIMARY",
                        mode="ACTIVE",
                        health="ONLINE",
                        max_concurrency=1,
                        current_jobs=0,
                    ),
                    Workflow(
                        key="claim-test",
                        display_name="Claim test",
                        description="test",
                    ),
                ]
            )
            workflow = WorkflowVersion(
                workflow_key="claim-test",
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
                template_sha256="f" * 64,
            )
            db.add(workflow)
            await db.flush()
            db.add_all(
                [
                    WorkflowNodeCompatibility(
                        workflow_version_id=workflow.id,
                        node_id=node_id,
                        compatible=True,
                        reasons=[],
                    )
                    for node_id in ("worker-a", "worker-b")
                ]
            )
            for index in range(225):
                db.add(
                    Job(
                        id=f"pg-test-{index:03d}",
                        tenant_id="load-test",
                        workflow_key="claim-test",
                        workflow_version="1",
                        status=JobStatus.QUEUED.value,
                        priority="normal",
                        parameters={},
                        request_hash=f"pg-test-{index:03d}",
                        request_id=f"pg-test-{index:03d}",
                        trace_id=f"pg-test-{index:03d}",
                        job_dir=str(tmp_path / f"pg-test-{index:03d}"),
                        created_at=now - timedelta(days=1, seconds=index),
                    )
                )
            for index in range(2):
                db.add(
                    Job(
                        id=f"pg-production-{index}",
                        tenant_id="production",
                        workflow_key="claim-test",
                        workflow_version="1",
                        status=JobStatus.QUEUED.value,
                        priority="normal",
                        parameters={},
                        request_hash=f"pg-production-{index}",
                        request_id=f"pg-production-{index}",
                        trace_id=f"pg-production-{index}",
                        job_dir=str(tmp_path / f"pg-production-{index}"),
                        created_at=now,
                    )
                )
            await db.commit()

        async def claim(node_id: str) -> Job:
            async with database.session() as db:
                async with db.begin():
                    result = await claim_next_job(db, node_id, 300)
                assert result is not None
                return result[0]

        claimed = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        assert {job.id for job in claimed} == {"pg-production-0", "pg-production-1"}
        assert all(job.tenant_id == "production" for job in claimed)
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
        await database.close()
