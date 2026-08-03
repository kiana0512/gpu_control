import asyncio
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from gpu_control_asset_api.main import create_app
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from packages.gpu_control_core.models import ApiClient, AssetJob, AssetWorker, Base, Node
from packages.gpu_control_core.security import sign_agent_request
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


def worker_headers(settings: Settings, path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    return {
        "Content-Type": "application/json",
        "X-Asset-Timestamp": timestamp,
        "X-Asset-Nonce": nonce,
        "X-Asset-Signature": sign_agent_request(
            "POST",
            path,
            body,
            timestamp,
            nonce,
            settings.asset_worker_hmac_secret,
        ),
    }


async def test_generation_takeover_serializes_with_lease_renewal(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        database_url=postgres_url(),
        redis_url="redis://127.0.0.1:6399/15",
        asset_root=tmp_path / "assets",
        job_root=tmp_path / "jobs",
        jwt_secret="test-jwt",
        api_key_pepper="test-pepper",
        node_agent_hmac_secret="test-agent",
        asset_worker_hmac_secret="a" * 32,
        alertmanager_webhook_token="development-only-change-me",
    )
    app = create_app(settings)
    worker_id = "asset-worker-3090-a"
    old_instance = hashlib.sha256(b"old-instance").hexdigest()[:32]
    new_instance = hashlib.sha256(b"new-instance").hexdigest()[:32]
    newest_instance = hashlib.sha256(b"newest-instance").hexdigest()[:32]
    old_started_at = datetime.now(UTC) - timedelta(minutes=2)
    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    async with app.router.lifespan_context(app):
        async with app.state.db.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.db.session() as db:
            db.add(ApiClient(id="asset-client", name="Asset", role="client"))
            db.add(
                Node(
                    id="worker-3090-a",
                    display_name="3090-A",
                    base_url="http://127.0.0.1:8188",
                    pool="PRIMARY",
                    mode="ACTIVE",
                    health="ONLINE",
                    current_jobs=0,
                    max_concurrency=1,
                    labels={},
                )
            )
            db.add(
                AssetWorker(
                    id=worker_id,
                    display_name="3090-A CPU Worker",
                    node_id="worker-3090-a",
                    hostname="worker-3090-a",
                    status="ONLINE",
                    blender_version="5.1.2",
                    skill_version="asset-skills-test",
                    cpu_count=32,
                    max_concurrency=1,
                    current_jobs=1,
                    agent_instance_id=old_instance,
                    agent_started_at=old_started_at,
                    last_heartbeat_at=now,
                )
            )
            await db.commit()
            db.add(
                AssetJob(
                    id=job_id,
                    client_id="asset-client",
                    external_asset_id="generation-race",
                    job_type="UV_PROCESS_V2",
                    status="RUNNING",
                    source_filename="asset.blend",
                    input_path=str(tmp_path / "asset.blend"),
                    input_sha256="a" * 64,
                    input_size_bytes=1,
                    options={},
                    request_hash="b" * 64,
                    request_id="generation-race",
                    worker_id=worker_id,
                    worker_instance_id=old_instance,
                    lease_token_hash="c" * 64,
                    lease_expires_at=now - timedelta(seconds=1),
                    created_at=now,
                )
            )
            await db.commit()

        path = "/internal/v1/assets/workers/heartbeat"
        heartbeat_payload = {
            "worker_id": worker_id,
            "node_id": "worker-3090-a",
            "display_name": "3090-A CPU Worker",
            "hostname": "worker-3090-a",
            "blender_version": "5.1.2",
            "skill_version": "asset-skills-test",
            "cpu_count": 32,
            "max_concurrency": 1,
            "current_jobs": 0,
            "load_1m": 0,
            "available_memory_mb": 100000,
            "agent_instance_id": new_instance,
            "agent_started_at": (old_started_at + timedelta(minutes=1)).isoformat(),
        }
        encoded = json.dumps(heartbeat_payload, separators=(",", ":")).encode()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            renewal_session = app.state.db.sessions()
            try:
                await renewal_session.begin()
                renewing_job = await renewal_session.scalar(
                    select(AssetJob).where(AssetJob.id == job_id).with_for_update()
                )
                assert renewing_job is not None
                renewing_job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=2)
                await renewal_session.flush()

                takeover = asyncio.create_task(
                    client.post(
                        path,
                        content=encoded,
                        headers=worker_headers(settings, path, encoded),
                    )
                )
                response = await asyncio.wait_for(takeover, timeout=2)
                assert response.status_code == 409, response.text
                assert response.json()["detail"]["code"] == (
                    "ASSET_WORKER_HEARTBEAT_BUSY_RETRY"
                )
                await renewal_session.commit()
                renewed = await client.post(
                    path,
                    content=encoded,
                    headers=worker_headers(settings, path, encoded),
                )
                assert renewed.status_code == 409, renewed.text
                assert renewed.json()["detail"]["code"] == (
                    "ASSET_WORKER_GENERATION_CONFLICT"
                )
            finally:
                if renewal_session.in_transaction():
                    await renewal_session.rollback()
                await renewal_session.close()

        async with app.state.db.session() as db:
            worker = await db.get(AssetWorker, worker_id)
            assert worker is not None
            assert worker.agent_instance_id == old_instance
            job = await db.get(AssetJob, job_id)
            assert job is not None
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            accepted = await client.post(
                path,
                content=encoded,
                headers=worker_headers(settings, path, encoded),
            )
            assert accepted.status_code == 200, accepted.text

            async with app.state.db.session() as db:
                job = await db.get(AssetJob, job_id, with_for_update=True)
                worker = await db.get(AssetWorker, worker_id, with_for_update=True)
                assert job is not None and worker is not None
                job.status = "QUEUED"
                job.worker_id = None
                job.worker_instance_id = None
                job.lease_token_hash = None
                job.lease_expires_at = None
                worker.current_jobs = 0
                await db.commit()

            # Emulate the old generation's claim transaction paused after it
            # has locked Worker and converted a queued Job to a live lease.
            claim_session = app.state.db.sessions()
            try:
                await claim_session.begin()
                claiming_worker = await claim_session.scalar(
                    select(AssetWorker)
                    .where(AssetWorker.id == worker_id)
                    .with_for_update()
                )
                claiming_job = await claim_session.scalar(
                    select(AssetJob).where(AssetJob.id == job_id).with_for_update()
                )
                assert claiming_worker is not None and claiming_job is not None
                claiming_worker.current_jobs = 1
                claiming_job.status = "CLAIMED"
                claiming_job.worker_id = worker_id
                claiming_job.worker_instance_id = new_instance
                claiming_job.lease_token_hash = "d" * 64
                claiming_job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=2)
                await claim_session.flush()

                newest_payload = {
                    **heartbeat_payload,
                    "agent_instance_id": newest_instance,
                    "agent_started_at": datetime.now(UTC).isoformat(),
                }
                newest_encoded = json.dumps(
                    newest_payload, separators=(",", ":")
                ).encode()
                takeover_after_claim = asyncio.create_task(
                    client.post(
                        path,
                        content=newest_encoded,
                        headers=worker_headers(settings, path, newest_encoded),
                    )
                )
                await asyncio.sleep(0.1)
                assert not takeover_after_claim.done()
                await claim_session.commit()
                rejected = await asyncio.wait_for(takeover_after_claim, timeout=2)
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["detail"]["code"] == (
                    "ASSET_WORKER_GENERATION_CONFLICT"
                )
            finally:
                if claim_session.in_transaction():
                    await claim_session.rollback()
                await claim_session.close()

        async with app.state.db.engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
