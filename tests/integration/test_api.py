import asyncio
import hashlib
import io
import json
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from gpu_control_api.main import _merge_service_parameter, create_app
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.gpu_control_core.enums import BatchStatus
from packages.gpu_control_core.models import (
    Alert,
    ApiClient,
    ApiKey,
    AssetArtifact,
    AssetJob,
    AssetWorker,
    AuditLog,
    Base,
    BatchArtifact,
    BatchCancelOperation,
    BatchIdempotencyKey,
    IdempotencyKey,
    Job,
    JobArtifact,
    JobAttempt,
    JobBatch,
    JobBatchItem,
    JobEvent,
    Node,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.security import hash_api_secret, hash_password, sign_agent_request
from packages.gpu_control_core.settings import Settings


def test_modelview_prompt_form_field_merges_without_ambiguity() -> None:
    assert json.loads(_merge_service_parameter("{}", "prompt", "修复左侧边缘")) == {
        "prompt": "修复左侧边缘"
    }
    assert json.loads(
        _merge_service_parameter('{"prompt":"修复左侧边缘"}', "prompt", "修复左侧边缘")
    ) == {"prompt": "修复左侧边缘"}
    try:
        _merge_service_parameter('{"prompt":"A"}', "prompt", "B")
    except ValueError as exc:
        assert "不能冲突" in str(exc)
    else:
        raise AssertionError("conflicting prompt sources must fail closed")


async def test_api_version_exposes_immutable_build_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPU_CONTROL_BUILD_VERSION", "1.5.5")
    monkeypatch.setenv("GPU_CONTROL_BUILD_REVISION", "a" * 40)
    monkeypatch.setattr("gpu_control_api.main.package_version", lambda _: "1.5.5")
    async for _, client in prepared_app(tmp_path):
        response = await client.get("/api/v1/version")
        assert response.status_code == 200
        payload = response.json()
        assert payload["component"] == "api"
        assert payload["version"] == "1.5.5"
        assert payload["build_version"] == "1.5.5"
        assert payload["source_revision"] == "a" * 40
        assert payload["provenance_complete"] is True
        assert payload["version_aligned"] is True


async def prepared_app(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        redis_url="redis://127.0.0.1:6399/15",
        job_root=tmp_path / "jobs",
        jwt_secret="test-jwt-secret-at-least-32-bytes-long",
        api_key_pepper="test-pepper",
        node_agent_hmac_secret="test-agent",
        alertmanager_webhook_token="development-only-change-me",
        system_max_queued=200,
        default_tenant_max_queued=200,
        allowed_callback_hosts="callback.example.com",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.db.session() as session:
            session.add_all(
                [
                    ApiClient(
                        id="admin",
                        name="admin",
                        role="admin",
                        password_hash=hash_password("correct-password"),
                        max_queued=200,
                        max_running=3,
                    ),
                    ApiClient(
                        id="tenant-b",
                        name="Tenant B",
                        role="client",
                        max_queued=200,
                        max_running=1,
                    ),
                    ApiClient(
                        id="tenant",
                        name="Tenant",
                        role="client",
                        max_queued=200,
                        max_running=1,
                        callback_hosts=["callback.example.com"],
                    ),
                    ApiKey(
                        id=str(uuid.uuid4()),
                        client_id="tenant",
                        prefix="abcd1234",
                        secret_hash=hash_api_secret("secret", settings.api_key_pepper),
                    ),
                    ApiKey(
                        id=str(uuid.uuid4()),
                        client_id="tenant-b",
                        prefix="tenantb1",
                        secret_hash=hash_api_secret("secret-b", settings.api_key_pepper),
                    ),
                    ApiKey(
                        id=str(uuid.uuid4()),
                        client_id="admin",
                        prefix="adminkey",
                        secret_hash=hash_api_secret("admin-secret", settings.api_key_pepper),
                    ),
                    Node(
                        id="worker-3090-a",
                        display_name="3090 A",
                        base_url="http://10.0.0.11:8188",
                        agent_url="http://10.0.0.11:8190",
                        pool="PRIMARY",
                        mode="ACTIVE",
                        health="ONLINE",
                        total_vram_mb=24576,
                        free_vram_mb=23000,
                        labels={
                            "gpu_family": "3090",
                            "comfy_class_types": ["KSampler", "LoadImage", "SaveImage"],
                        },
                    ),
                    Node(
                        id="worker-3090-b",
                        display_name="3090 B",
                        base_url="http://10.0.0.12:8188",
                        agent_url="http://10.0.0.12:8190",
                        pool="PRIMARY",
                        mode="ACTIVE",
                        health="ONLINE",
                        total_vram_mb=24576,
                        free_vram_mb=23000,
                        labels={
                            "gpu_family": "3090",
                            "comfy_class_types": ["KSampler", "LoadImage", "SaveImage"],
                        },
                    ),
                    Node(
                        id="control-4090",
                        display_name="4090 Control",
                        base_url="http://10.0.0.10:8188",
                        agent_url="http://10.0.0.10:8190",
                        pool="OVERFLOW",
                        mode="RESERVED",
                        health="ONLINE",
                        total_vram_mb=24576,
                        free_vram_mb=22000,
                        labels={
                            "gpu_family": "4090",
                            "comfy_class_types": ["KSampler", "LoadImage", "SaveImage"],
                        },
                    ),
                    Workflow(key="fake", display_name="Fake", description="test"),
                ]
            )
            session.add(
                WorkflowVersion(
                    workflow_key="fake",
                    version="1",
                    template={
                        "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
                        "3": {"class_type": "KSampler", "inputs": {"steps": 20}},
                        "9": {"class_type": "SaveImage", "inputs": {}},
                    },
                    parameter_schema={
                        "type": "object",
                        "properties": {
                            "steps": {"type": "integer", "minimum": 1, "maximum": 100},
                            "image_filename": {"type": "string"},
                        },
                        "required": ["steps"],
                        "additionalProperties": False,
                    },
                    bindings={
                        "steps": "3.inputs.steps",
                        "image_filename": "1.inputs.image",
                    },
                    allowed_class_types=["LoadImage", "KSampler", "SaveImage"],
                    required_models=[],
                    required_custom_nodes=[],
                    min_vram_mb=0,
                    timeout_seconds=60,
                    node_labels={
                        "imageclip_commit": "7" * 40,
                        "imageclip_pipeline_sha256": "8" * 64,
                    },
                    output_nodes=["9"],
                    enabled=True,
                    template_sha256="x",
                )
            )
            await session.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


async def install_imageclip_batch_workflow(app: FastAPI) -> None:
    async with app.state.db.session() as session:
        session.add(
            Workflow(
                key="imageclip-rgba",
                display_name="ImageClip RGBA",
                description="batch admission test",
            )
        )
        session.add(
            WorkflowVersion(
                workflow_key="imageclip-rgba",
                version="test-1",
                template={
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "placeholder.png"},
                    },
                    "9": {"class_type": "SaveImage", "inputs": {}},
                },
                parameter_schema={
                    "type": "object",
                    "properties": {"image_filename": {"type": "string"}},
                    "required": ["image_filename"],
                    "additionalProperties": False,
                },
                bindings={"image_filename": "1.inputs.image"},
                allowed_class_types=["LoadImage", "SaveImage"],
                required_models=[],
                required_custom_nodes=[],
                min_vram_mb=0,
                timeout_seconds=60,
                node_labels={
                    "imageclip_commit": "7" * 40,
                    "imageclip_pipeline_sha256": "8" * 64,
                },
                output_nodes=["9"],
                enabled=True,
                template_sha256="batch-admission-test",
            )
        )
        await session.commit()


def imageclip_batch_files(external_batch_id: str) -> dict[str, Any]:
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (3, 2), "white").save(image, format="PNG")
    image_bytes = image.getvalue()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("shot/0001.png", image_bytes)
    manifest = {
        "schema_version": "1.0",
        "external_batch_id": external_batch_id,
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": {},
        "frames": [
            {
                "ordinal": 0,
                "relative_path": "shot/0001.png",
                "size_bytes": len(image_bytes),
                "sha256": hashlib.sha256(image_bytes).hexdigest(),
            }
        ],
    }
    return {
        "archive": ("frames.zip", archive.getvalue(), "application/zip"),
        "manifest": (None, json.dumps(manifest)),
    }


async def test_expired_batch_idempotency_key_can_be_reused_concurrently(
    tmp_path: Path,
) -> None:
    headers = {
        "X-API-Key": "gpc_abcd1234_secret",
        "Idempotency-Key": "reusable-batch-key",
    }
    async for app, client in prepared_app(tmp_path):
        await install_imageclip_batch_workflow(app)
        first = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files=imageclip_batch_files("expired-original"),
        )
        assert first.status_code == 202, first.text
        first_batch_id = first.json()["batch_id"]
        async with app.state.db.session() as db:
            expired = await db.scalar(
                select(BatchIdempotencyKey).where(
                    BatchIdempotencyKey.client_id == "tenant",
                    BatchIdempotencyKey.key == "reusable-batch-key",
                )
            )
            assert expired is not None
            expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        async def submit(api_client: httpx.AsyncClient = client) -> httpx.Response:
            return await api_client.post(
                "/api/v1/batches/imageclip-rgba",
                headers=headers,
                files=imageclip_batch_files("reused-concurrently"),
            )

        responses = await asyncio.gather(*(submit() for _ in range(20)))
        assert sum(response.status_code == 202 for response in responses) == 1
        assert sum(response.status_code == 200 for response in responses) == 19
        reused_batch_ids = {response.json()["batch_id"] for response in responses}
        assert len(reused_batch_ids) == 1
        assert first_batch_id not in reused_batch_ids
        async with app.state.db.session() as db:
            current = await db.scalar(
                select(BatchIdempotencyKey).where(
                    BatchIdempotencyKey.client_id == "tenant",
                    BatchIdempotencyKey.key == "reusable-batch-key",
                )
            )
            assert current is not None
            assert current.batch_id in reused_batch_ids
            assert await db.scalar(select(func.count(JobBatch.id))) == 2


async def test_expired_batch_idempotency_key_inserted_before_locked_recheck_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {
        "X-API-Key": "gpc_abcd1234_secret",
        "Idempotency-Key": "locked-expired-key",
    }
    async for app, client in prepared_app(tmp_path):
        await install_imageclip_batch_workflow(app)
        old_batch_id = str(uuid.uuid4())
        async with app.state.db.session() as db:
            db.add(
                JobBatch(
                    id=old_batch_id,
                    tenant_id="tenant",
                    external_batch_id="old-locked-expired",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    status=BatchStatus.SUCCEEDED.value,
                    request_hash="a" * 64,
                    request_id="old-locked-expired",
                    trace_id="old-locked-expired",
                    batch_dir=str(tmp_path / "old-locked-expired"),
                    manifest_sha256="b" * 64,
                    archive_sha256="c" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    pending_items=0,
                )
            )
            await db.commit()

        injected = False

        async def inject_expired_key(db: AsyncSession, batch_id: str = old_batch_id) -> None:
            nonlocal injected
            if injected:
                return
            injected = True
            db.add(
                BatchIdempotencyKey(
                    client_id="tenant",
                    key="locked-expired-key",
                    request_hash="d" * 64,
                    batch_id=batch_id,
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await db.flush()

        monkeypatch.setattr(
            app.state.db,
            "acquire_global_admission_transaction_lock",
            inject_expired_key,
        )
        created = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files=imageclip_batch_files("locked-recheck-reuse"),
        )
        assert created.status_code == 202, created.text
        assert created.json()["batch_id"] != old_batch_id
        async with app.state.db.session() as db:
            current = await db.scalar(
                select(BatchIdempotencyKey).where(
                    BatchIdempotencyKey.client_id == "tenant",
                    BatchIdempotencyKey.key == "locked-expired-key",
                )
            )
            assert current is not None
            assert current.batch_id == created.json()["batch_id"]


async def test_uncommitted_imageclip_batch_directory_is_removed_on_unexpected_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for app, client in prepared_app(tmp_path):
        await install_imageclip_batch_workflow(app)

        async def fail_global_admission_lock(_: AsyncSession) -> None:
            raise RuntimeError("injected admission failure")

        monkeypatch.setattr(
            app.state.db,
            "acquire_global_admission_transaction_lock",
            fail_global_admission_lock,
        )
        response = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers={
                "X-API-Key": "gpc_abcd1234_secret",
                "Idempotency-Key": "cleanup-on-failure",
            },
            files=imageclip_batch_files("cleanup-on-failure"),
        )
        assert response.status_code == 500, response.text
        permanent = [path for path in (tmp_path / "jobs").glob("batches/*/*/*/*") if path.is_dir()]
        assert permanent == []
        staging = tmp_path / "jobs" / ".batch-staging"
        assert not staging.exists() or not any(staging.iterdir())


async def test_lost_batch_commit_acknowledgement_preserves_committed_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_commit = AsyncSession.commit
    async for app, client in prepared_app(tmp_path):
        await install_imageclip_batch_workflow(app)
        raised = False

        async def commit_then_lose_acknowledgement(db: AsyncSession) -> None:
            nonlocal raised
            commits_batch = any(isinstance(item, JobBatch) for item in db.identity_map.values())
            await original_commit(db)
            if commits_batch and not raised:
                raised = True
                raise RuntimeError("injected lost commit acknowledgement")

        monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_acknowledgement)
        response = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers={
                "X-API-Key": "gpc_abcd1234_secret",
                "Idempotency-Key": "lost-commit-ack",
            },
            files=imageclip_batch_files("lost-commit-ack"),
        )
        assert response.status_code == 500, response.text
        assert raised is True
        async with app.state.db.session() as db:
            batch = await db.scalar(
                select(JobBatch).where(
                    JobBatch.tenant_id == "tenant",
                    JobBatch.external_batch_id == "lost-commit-ack",
                )
            )
            assert batch is not None
            assert Path(batch.batch_dir).is_dir()


async def test_api_key_rbac_validation_and_idempotency(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):
        # First-party LAN callers are intentionally auto-enrolled by source IP.
        assert (await client.get("/api/v1/workflows")).status_code == 200
        headers = {"X-API-Key": "gpc_abcd1234_secret", "Idempotency-Key": "same"}
        files = {
            "workflow_key": (None, "fake"),
            "workflow_version": (None, "1"),
            "parameters": (None, '{"steps":20}'),
        }
        first = await client.post("/api/v1/jobs", headers=headers, files=files)
        assert first.status_code == 202, first.text
        second = await client.post("/api/v1/jobs", headers=headers, files=files)
        assert second.status_code == 200 and second.json()["job_id"] == first.json()["job_id"]
        conflict_files = {**files, "parameters": (None, '{"steps":21}')}
        conflict = await client.post("/api/v1/jobs", headers=headers, files=conflict_files)
        assert conflict.status_code == 409
        invalid_files = {**files, "parameters": (None, '{"steps":999}')}
        assert (
            await client.post(
                "/api/v1/jobs", headers={"X-API-Key": "gpc_abcd1234_secret"}, files=invalid_files
            )
        ).status_code == 422


async def test_job_create_acquires_global_admission_before_tenant_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for app, client in prepared_app(tmp_path):
        lock_calls: list[str] = []

        async def record_global_lock(
            _session: object,
            calls: list[str] = lock_calls,
        ) -> None:
            calls.append("global")

        async def record_tenant_lock(
            _session: object,
            tenant_id: str,
            calls: list[str] = lock_calls,
        ) -> None:
            calls.append(f"tenant:{tenant_id}")

        monkeypatch.setattr(
            app.state.db,
            "acquire_global_admission_transaction_lock",
            record_global_lock,
        )
        monkeypatch.setattr(
            app.state.db,
            "acquire_tenant_transaction_lock",
            record_tenant_lock,
        )
        response = await client.post(
            "/api/v1/jobs",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
            },
        )
        assert response.status_code == 202, response.text
        assert lock_calls == ["global", "tenant:tenant"]


async def test_production_admission_atomically_preempts_new_test_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as db:
            load_client = await db.get(ApiClient, "tenant-b")
            assert load_client is not None
            load_client.client_kind = "test"
            await db.commit()

        first_lock_entered = asyncio.Event()
        second_lock_entered = asyncio.Event()
        allow_second_admission = asyncio.Event()
        lock_calls = 0

        async def ordered_global_lock(
            _session: object,
            first_entered: asyncio.Event = first_lock_entered,
            second_entered: asyncio.Event = second_lock_entered,
            allow_second: asyncio.Event = allow_second_admission,
        ) -> None:
            nonlocal lock_calls
            lock_calls += 1
            if lock_calls == 1:
                first_entered.set()
                return
            second_entered.set()
            await allow_second.wait()

        monkeypatch.setattr(
            app.state.db,
            "acquire_global_admission_transaction_lock",
            ordered_global_lock,
        )
        production_task = asyncio.create_task(
            client.post(
                "/api/v1/jobs",
                headers={
                    "X-API-Key": "gpc_abcd1234_secret",
                    "Idempotency-Key": "production-first",
                },
                files={
                    "workflow_key": (None, "fake"),
                    "workflow_version": (None, "1"),
                    "parameters": (None, '{"steps":20}'),
                },
            )
        )
        await asyncio.wait_for(first_lock_entered.wait(), timeout=2)
        test_task = asyncio.create_task(
            client.post(
                "/api/v1/jobs",
                headers={
                    "X-API-Key": "gpc_tenantb1_secret-b",
                    "Idempotency-Key": "test-after-production",
                },
                files={
                    "workflow_key": (None, "fake"),
                    "workflow_version": (None, "1"),
                    "parameters": (None, '{"steps":20}'),
                },
            )
        )
        await asyncio.wait_for(second_lock_entered.wait(), timeout=2)
        production = await asyncio.wait_for(production_task, timeout=5)
        assert production.status_code == 202, production.text
        allow_second_admission.set()
        preempted = await asyncio.wait_for(test_task, timeout=5)
        assert preempted.status_code == 503, preempted.text
        assert preempted.headers["Retry-After"] == "5"
        assert preempted.json()["detail"] == {
            "code": "LOAD_TEST_PREEMPTED",
            "message": "真实生产任务已进入系统，新的压力测试任务已暂停接收",
            "retryable": True,
        }
        async with app.state.db.session() as db:
            test_job_count = await db.scalar(
                select(func.count(Job.id)).where(Job.tenant_id == "tenant-b")
            )
            assert test_job_count == 0


async def test_test_idempotency_replay_remains_available_after_preemption(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as db:
            load_client = await db.get(ApiClient, "tenant-b")
            assert load_client is not None
            load_client.client_kind = "test"
            await db.commit()
        test_request = {
            "workflow_key": (None, "fake"),
            "workflow_version": (None, "1"),
            "parameters": (None, '{"steps":20}'),
        }
        test_headers = {
            "X-API-Key": "gpc_tenantb1_secret-b",
            "Idempotency-Key": "existing-test-job",
        }
        created = await client.post("/api/v1/jobs", headers=test_headers, files=test_request)
        assert created.status_code == 202, created.text

        production = await client.post(
            "/api/v1/jobs",
            headers={
                "X-API-Key": "gpc_abcd1234_secret",
                "Idempotency-Key": "production-arrived",
            },
            files=test_request,
        )
        assert production.status_code == 202, production.text

        replay = await client.post("/api/v1/jobs", headers=test_headers, files=test_request)
        assert replay.status_code == 200, replay.text
        assert replay.json()["job_id"] == created.json()["job_id"]

        rejected = await client.post(
            "/api/v1/jobs",
            headers={
                "X-API-Key": "gpc_tenantb1_secret-b",
                "Idempotency-Key": "new-test-job",
            },
            files=test_request,
        )
        assert rejected.status_code == 503, rejected.text


async def test_test_jobs_and_batches_stop_before_production_queue_reserve(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        app.state.settings.system_max_queued = 4
        app.state.settings.system_production_queue_reserve = 2
        await install_imageclip_batch_workflow(app)
        async with app.state.db.session() as db:
            load_client = await db.get(ApiClient, "tenant-b")
            assert load_client is not None
            load_client.client_kind = "test"
            now = datetime.now(UTC)
            for index in range(2):
                db.add(
                    Job(
                        id=f"queued-test-{index}",
                        tenant_id="tenant-b",
                        workflow_key="fake",
                        workflow_version="1",
                        status="QUEUED",
                        priority="normal",
                        parameters={},
                        request_hash=f"queued-test-{index}",
                        request_id=f"queued-test-{index}",
                        trace_id=f"queued-test-{index}",
                        job_dir=str(tmp_path / f"queued-test-{index}"),
                        created_at=now - timedelta(minutes=1),
                    )
                )
            await db.commit()

        test_headers = {"X-API-Key": "gpc_tenantb1_secret-b"}
        rejected_job = await client.post(
            "/api/v1/jobs",
            headers=test_headers,
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
            },
        )
        assert rejected_job.status_code == 429, rejected_job.text
        assert rejected_job.json()["detail"]["reason"] == "PRODUCTION_QUEUE_RESERVED"

        rejected_batch = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers={
                **test_headers,
                "Idempotency-Key": "test-batch-reserve",
            },
            files=imageclip_batch_files("test-batch-reserve"),
        )
        assert rejected_batch.status_code == 429, rejected_batch.text
        assert rejected_batch.json()["detail"]["reason"] == "PRODUCTION_QUEUE_RESERVED"

        production = await client.post(
            "/api/v1/jobs",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
            },
        )
        assert production.status_code == 202, production.text
        async with app.state.db.session() as db:
            queued = await db.scalar(
                select(func.count(Job.id)).where(Job.status == "QUEUED")
            )
        assert queued == 3


async def test_active_production_asset_work_preempts_new_test_batch(
    tmp_path: Path,
) -> None:
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
    image_bytes = image.getvalue()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("0001.png", image_bytes)
    manifest = {
        "schema_version": "1.0",
        "external_batch_id": "preempted-test-batch",
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": {},
        "frames": [
            {
                "ordinal": 0,
                "relative_path": "0001.png",
                "size_bytes": len(image_bytes),
                "sha256": hashlib.sha256(image_bytes).hexdigest(),
            }
        ],
    }
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as db:
            load_client = await db.get(ApiClient, "tenant-b")
            assert load_client is not None
            load_client.client_kind = "test"
            db.add(
                Workflow(
                    key="imageclip-rgba",
                    display_name="ImageClip RGBA",
                    description="admission test",
                )
            )
            db.add(
                WorkflowVersion(
                    workflow_key="imageclip-rgba",
                    version="admission-test",
                    template={
                        "1": {
                            "class_type": "LoadImage",
                            "inputs": {"image": "placeholder.png"},
                        },
                        "9": {"class_type": "SaveImage", "inputs": {}},
                    },
                    parameter_schema={
                        "type": "object",
                        "properties": {"image_filename": {"type": "string"}},
                        "required": ["image_filename"],
                        "additionalProperties": False,
                    },
                    bindings={"image_filename": "1.inputs.image"},
                    allowed_class_types=["LoadImage", "SaveImage"],
                    required_models=[],
                    required_custom_nodes=[],
                    min_vram_mb=0,
                    timeout_seconds=60,
                    node_labels={
                        "imageclip_commit": "7" * 40,
                        "imageclip_pipeline_sha256": "8" * 64,
                    },
                    output_nodes=["9"],
                    enabled=True,
                    template_sha256="admission-test",
                )
            )
            db.add(
                AssetJob(
                    id="production-asset-active",
                    client_id="tenant",
                    external_asset_id="production-asset-active",
                    job_type="UV_PROCESS_V2",
                    status="QUEUED",
                    source_filename="asset.fbx",
                    input_path=str(tmp_path / "asset.fbx"),
                    input_sha256="a" * 64,
                    input_size_bytes=1,
                    options={},
                    request_hash="b" * 64,
                    request_id="production-asset-active",
                )
            )
            await db.commit()

        response = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers={
                "X-API-Key": "gpc_tenantb1_secret-b",
                "Idempotency-Key": "preempted-test-batch",
            },
            files={
                "archive": ("frames.zip", archive.getvalue(), "application/zip"),
                "manifest": (None, json.dumps(manifest)),
            },
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "LOAD_TEST_PREEMPTED"
        async with app.state.db.session() as db:
            test_batches = await db.scalar(
                select(func.count(JobBatch.id)).where(JobBatch.tenant_id == "tenant-b")
            )
            assert test_batches == 0


async def test_batch_api_idempotency_isolation_and_parent_only_admin_list(
    tmp_path: Path,
) -> None:
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (3, 2), "white").save(image, format="PNG")
    image_bytes = image.getvalue()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("shot/0001.png", image_bytes)
    manifest = {
        "schema_version": "1.0",
        "external_batch_id": "animation-batch-001",
        "failure_policy": "all_or_nothing",
        "output_naming": "preserve_stem_png",
        "parameters": {},
        "frames": [
            {
                "ordinal": 0,
                "relative_path": "shot/0001.png",
                "size_bytes": len(image_bytes),
                "sha256": hashlib.sha256(image_bytes).hexdigest(),
            }
        ],
    }
    headers = {
        "X-API-Key": "gpc_abcd1234_secret",
        "Idempotency-Key": "animation-batch-001",
    }
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as session:
            session.add(
                Workflow(
                    key="imageclip-rgba",
                    display_name="ImageClip RGBA",
                    description="batch test",
                )
            )
            session.add(
                WorkflowVersion(
                    workflow_key="imageclip-rgba",
                    version="test-1",
                    template={
                        "1": {
                            "class_type": "LoadImage",
                            "inputs": {"image": "placeholder.png"},
                        },
                        "9": {"class_type": "SaveImage", "inputs": {}},
                    },
                    parameter_schema={
                        "type": "object",
                        "properties": {"image_filename": {"type": "string"}},
                        "required": ["image_filename"],
                        "additionalProperties": False,
                    },
                    bindings={"image_filename": "1.inputs.image"},
                    allowed_class_types=["LoadImage", "SaveImage"],
                    required_models=[],
                    required_custom_nodes=[],
                    min_vram_mb=0,
                    timeout_seconds=60,
                    node_labels={
                        "imageclip_commit": "7" * 40,
                        "imageclip_pipeline_sha256": "8" * 64,
                    },
                    output_nodes=["9"],
                    enabled=True,
                    template_sha256="batch-test",
                )
            )
            await session.commit()
        first = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files={
                "archive": ("frames.zip", archive.getvalue(), "application/zip"),
                "manifest": (None, json.dumps(manifest)),
            },
        )
        assert first.status_code == 202, first.text
        batch_id = first.json()["batch_id"]
        expected_identity = {
            "workflow_key": "imageclip-rgba",
            "workflow_version": "test-1",
            "pipeline_commit": "7" * 40,
            "pipeline_sha256": "8" * 64,
            "output_node": "SaveImage #9",
        }
        assert {key: first.json()[key] for key in expected_identity} == expected_identity
        assert first.json()["validated_at"] is not None
        assert first.json()["queued_at"] is not None
        assert first.json()["updated_at"] is not None
        assert first.json()["started_at"] is None

        repeated = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files={
                "archive": ("frames.zip", archive.getvalue(), "application/zip"),
                "manifest": (None, json.dumps(manifest)),
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["batch_id"] == batch_id

        changed = json.loads(json.dumps(manifest))
        changed["frames"][0]["sha256"] = "0" * 64
        conflict = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files={
                "archive": ("frames.zip", archive.getvalue(), "application/zip"),
                "manifest": (None, json.dumps(changed)),
            },
        )
        assert conflict.status_code == 409

        own_status = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert own_status.status_code == 200
        assert {key: own_status.json()[key] for key in expected_identity} == expected_identity
        assert own_status.json()["counts"] == {
            "total": 1,
            "pending": 1,
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
        }
        manifest_status = await client.get(
            f"/api/v1/batches/{batch_id}/manifest",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert {key: manifest_status.json()[key] for key in expected_identity} == expected_identity
        async with app.state.db.session() as session:
            workflow_row = await session.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_key == "imageclip-rgba",
                    WorkflowVersion.version == "test-1",
                )
            )
            assert workflow_row is not None
            workflow_row.node_labels = {
                "imageclip_commit": "a" * 40,
                "imageclip_pipeline_sha256": "b" * 64,
            }
            workflow_row.output_nodes = ["1"]
            workflow_row.enabled = False
            existing_key = await session.scalar(
                select(BatchIdempotencyKey).where(
                    BatchIdempotencyKey.client_id == "tenant",
                    BatchIdempotencyKey.key == "animation-batch-001",
                )
            )
            assert existing_key is not None
            canonical_manifest = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            existing_key.request_hash = hashlib.sha256(
                b"test-1\x00" + canonical_manifest
            ).hexdigest()
            session.add(
                WorkflowVersion(
                    workflow_key="imageclip-rgba",
                    version="test-2",
                    template={
                        "1": {
                            "class_type": "LoadImage",
                            "inputs": {"image": "placeholder.png"},
                        },
                        "9": {"class_type": "SaveImage", "inputs": {}},
                    },
                    parameter_schema={
                        "type": "object",
                        "properties": {"image_filename": {"type": "string"}},
                        "required": ["image_filename"],
                        "additionalProperties": False,
                    },
                    bindings={"image_filename": "1.inputs.image"},
                    allowed_class_types=["LoadImage", "SaveImage"],
                    required_models=[],
                    required_custom_nodes=[],
                    min_vram_mb=0,
                    timeout_seconds=60,
                    node_labels={
                        "imageclip_commit": "a" * 40,
                        "imageclip_pipeline_sha256": "b" * 64,
                    },
                    output_nodes=["9"],
                    enabled=True,
                    template_sha256="batch-test-2",
                )
            )
            await session.commit()
        immutable_status = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert {key: immutable_status.json()[key] for key in expected_identity} == expected_identity
        replay_after_workflow_switch = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files={
                "archive": ("frames.zip", archive.getvalue(), "application/zip"),
                "manifest": (None, json.dumps(manifest)),
            },
        )
        assert replay_after_workflow_switch.status_code == 200
        assert replay_after_workflow_switch.json()["batch_id"] == batch_id
        assert {
            key: replay_after_workflow_switch.json()[key] for key in expected_identity
        } == expected_identity
        legacy_replay_conflict = await client.post(
            "/api/v1/batches/imageclip-rgba",
            headers=headers,
            files={
                "archive": ("frames.zip", archive.getvalue(), "application/zip"),
                "manifest": (None, json.dumps(changed)),
            },
        )
        assert legacy_replay_conflict.status_code == 409
        assert legacy_replay_conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
        foreign_status = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_tenantb1_secret-b"},
        )
        assert foreign_status.status_code == 404

        capacity = await client.get(
            "/api/v1/scheduler/capacity",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert capacity.status_code == 200
        assert capacity.json()["schema_version"] == "1.0"
        assert capacity.json()["advisory"] is True
        assert capacity.json()["client"]["id"] == "tenant"
        assert capacity.json()["client"]["kind"] == "production"

        async with app.state.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            session.add(
                Job(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    status="QUEUED",
                    priority="batch",
                    parameters={},
                    request_hash="child-hash",
                    request_id="child-request",
                    trace_id="child-trace",
                    job_dir=str(tmp_path / "child"),
                    batch_id=batch_id,
                )
            )
            session.add(
                Job(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant",
                    workflow_key="fake",
                    workflow_version="1",
                    status="SUCCEEDED",
                    priority="normal",
                    parameters={},
                    request_hash="regular-hash",
                    request_id="regular-request",
                    trace_id="regular-trace",
                    job_dir=str(tmp_path / "regular"),
                )
            )
            artifact_id = str(uuid.uuid4())
            result_path = Path(batch.batch_dir) / "output" / "result.zip"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_bytes(b"result")
            session.add(
                BatchArtifact(
                    id=artifact_id,
                    batch_id=batch_id,
                    kind="result_archive",
                    relative_path="output/result.zip",
                    filename="result.zip",
                    content_type="application/zip",
                    size_bytes=6,
                    sha256=hashlib.sha256(b"result").hexdigest(),
                )
            )
            batch.status = "SUCCEEDED"
            batch.pending_items = 0
            batch.succeeded_items = 1
            batch.progress = 100
            await session.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        listed = await client.get("/admin/jobs", headers=admin_headers)
        assert listed.status_code == 200
        batch_rows = [row for row in listed.json() if row["kind"] == "batch"]
        assert len(batch_rows) == 1
        assert batch_rows[0]["job_id"] == batch_id
        assert batch_rows[0]["external_batch_id"] == "animation-batch-001"
        assert batch_rows[0]["artifacts"][0]["download_url"].startswith(
            f"/admin/batches/{batch_id}/artifacts/"
        )
        admin_download = await client.get(
            batch_rows[0]["artifacts"][0]["download_url"], headers=admin_headers
        )
        assert admin_download.status_code == 200
        assert admin_download.content == b"result"
        public_detail = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        public_url = public_detail.json()["artifacts"][0]["download_url"]
        assert {
            key: public_detail.json()["artifact"][key] for key in expected_identity
        } == expected_identity
        assert public_url.startswith(f"/api/v1/batches/{batch_id}/artifacts/")
        full_download = await client.get(public_url, headers={"X-API-Key": "gpc_abcd1234_secret"})
        assert full_download.content == b"result"
        assert full_download.headers["accept-ranges"] == "bytes"
        assert full_download.headers["x-artifact-sha256"] == hashlib.sha256(b"result").hexdigest()
        ranged_download = await client.get(
            public_url,
            headers={
                "X-API-Key": "gpc_abcd1234_secret",
                "X-Request-ID": "assetclaw-range-test",
                "Range": "bytes=2-",
            },
        )
        assert ranged_download.status_code == 206
        assert ranged_download.content == b"sult"
        assert ranged_download.headers["content-range"] == "bytes 2-5/6"
        assert ranged_download.headers["content-length"] == "4"
        assert ranged_download.headers["x-request-id"] == "assetclaw-range-test"
        assert ranged_download.headers["x-artifact-sha256"] == hashlib.sha256(b"result").hexdigest()
        assert (
            await client.get(public_url, headers={"X-API-Key": "gpc_tenantb1_secret-b"})
        ).status_code == 404
        async with app.state.db.session() as session:
            assert await session.get(JobBatch, batch_id) is not None


async def test_batch_performance_serializes_authoritative_node_attempts(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        batch_id = str(uuid.uuid4())
        first_job_id = str(uuid.uuid4())
        second_job_id = str(uuid.uuid4())
        started = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
        async with app.state.db.session() as session:
            node_a = await session.get(Node, "worker-3090-a")
            node_b = await session.get(Node, "worker-3090-b")
            assert node_a is not None and node_b is not None
            node_a.max_concurrency = 2
            node_b.max_concurrency = 3
            node_a.labels = {
                **node_a.labels,
                "gpu_model": "NVIDIA GeForce RTX 3090",
                "node_agent_version": "1.5.5",
            }
            node_b.labels = {
                **node_b.labels,
                "gpu_model": "NVIDIA GeForce RTX 4090",
                "node_agent_version": "1.5.5",
            }
            session.add(
                JobBatch(
                    id=batch_id,
                    tenant_id="tenant",
                    external_batch_id="assetclaw:performance:g1",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    pipeline_commit="7" * 40,
                    pipeline_sha256="8" * 64,
                    output_node="SaveImage #9",
                    status="SUCCEEDED",
                    failure_policy="all_or_nothing",
                    output_naming="preserve_stem_png",
                    parameters={},
                    request_hash="batch-performance-hash",
                    request_id="batch-performance-request",
                    trace_id="batch-performance-trace",
                    batch_dir=str(tmp_path / "batch-performance"),
                    manifest_sha256="1" * 64,
                    archive_sha256="2" * 64,
                    archive_size_bytes=2,
                    total_items=2,
                    pending_items=0,
                    succeeded_items=2,
                    progress=100,
                    validated_at=started - timedelta(seconds=2),
                    queued_at=started - timedelta(seconds=1),
                    started_at=started,
                    execution_finished_at=started + timedelta(seconds=32),
                    assembling_at=started + timedelta(seconds=33),
                    artifact_ready_at=started + timedelta(seconds=34),
                    finished_at=started + timedelta(seconds=35),
                    created_at=started - timedelta(seconds=3),
                    updated_at=started + timedelta(seconds=35),
                )
            )
            jobs = [
                Job(
                    id=first_job_id,
                    tenant_id="tenant",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    status="SUCCEEDED",
                    priority="batch",
                    parameters={},
                    request_hash="child-performance-1",
                    request_id="child-performance-request-1",
                    trace_id="child-performance-trace-1",
                    job_dir=str(tmp_path / "child-performance-1"),
                    batch_id=batch_id,
                    node_id="worker-3090-b",
                    attempt_count=2,
                ),
                Job(
                    id=second_job_id,
                    tenant_id="tenant",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    status="SUCCEEDED",
                    priority="batch",
                    parameters={},
                    request_hash="child-performance-2",
                    request_id="child-performance-request-2",
                    trace_id="child-performance-trace-2",
                    job_dir=str(tmp_path / "child-performance-2"),
                    batch_id=batch_id,
                    node_id="worker-3090-a",
                    attempt_count=1,
                ),
            ]
            session.add_all(jobs)
            session.add_all(
                [
                    JobBatchItem(
                        id=str(uuid.uuid4()),
                        batch_id=batch_id,
                        ordinal=0,
                        input_relative_path="0000.png",
                        output_relative_path="0000.png",
                        input_size_bytes=1,
                        input_sha256="3" * 64,
                        width=10,
                        height=20,
                        image_format="PNG",
                        status="SUCCEEDED",
                        job_id=first_job_id,
                        node_id="worker-3090-b",
                        attempts=2,
                    ),
                    JobBatchItem(
                        id=str(uuid.uuid4()),
                        batch_id=batch_id,
                        ordinal=1,
                        input_relative_path="0001.png",
                        output_relative_path="0001.png",
                        input_size_bytes=1,
                        input_sha256="4" * 64,
                        width=10,
                        height=20,
                        image_format="PNG",
                        status="SUCCEEDED",
                        job_id=second_job_id,
                        node_id="worker-3090-a",
                        attempts=1,
                    ),
                ]
            )
            session.add_all(
                [
                    JobAttempt(
                        job_id=first_job_id,
                        attempt=1,
                        node_id="worker-3090-a",
                        lease_token="lease-child-a-1",
                        status="FAILED",
                        upload_attempts=1,
                        prompt_attempts=1,
                        gpu_started_at=started,
                        gpu_finished_at=started + timedelta(seconds=10),
                        finished_at=started + timedelta(seconds=10),
                    ),
                    JobAttempt(
                        job_id=first_job_id,
                        attempt=2,
                        node_id="worker-3090-b",
                        lease_token="lease-child-a-2",
                        status="SUCCEEDED",
                        upload_attempts=1,
                        prompt_attempts=1,
                        gpu_started_at=started + timedelta(seconds=11),
                        gpu_finished_at=started + timedelta(seconds=31),
                        finished_at=started + timedelta(seconds=31),
                    ),
                    JobAttempt(
                        job_id=second_job_id,
                        attempt=1,
                        node_id="worker-3090-a",
                        lease_token="lease-child-b-1",
                        status="SUCCEEDED",
                        upload_attempts=1,
                        prompt_attempts=1,
                        gpu_started_at=started + timedelta(seconds=2),
                        gpu_finished_at=started + timedelta(seconds=32),
                        finished_at=started + timedelta(seconds=32),
                    ),
                ]
            )
            await session.commit()

        response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert response.status_code == 200, response.text
        performance = response.json()["performance"]
        assert performance["gpu_service_ms_total"] == 60_000
        assert performance["gpu_service_measurements_complete"] is True
        assert performance["reassignments"] == 1
        assert performance["scheduler_restarts"] is None
        assert performance["straggler_ratio"] == 0.015625
        nodes = {node["node_id"]: node for node in performance["nodes"]}
        assert nodes["worker-3090-a"] == {
            **nodes["worker-3090-a"],
            "gpu_model": "NVIDIA GeForce RTX 3090",
            "frames_assigned": 2,
            "frames_final_assignment": 1,
            "frames_succeeded": 1,
            "frames_failed": 0,
            "gpu_service_ms": 40_000,
            "gpu_service_measurements_complete": True,
            "frame_ms_p50": 10_000,
            "frame_ms_p95": 30_000,
            "node_started_at": started.isoformat(),
            "node_finished_at": (started + timedelta(seconds=32)).isoformat(),
            "reassignments_in": 0,
            "reassignments_out": 1,
            "max_concurrent_prompts": 2,
        }
        assert nodes["worker-3090-b"]["frames_assigned"] == 1
        assert nodes["worker-3090-b"]["frames_final_assignment"] == 1
        assert nodes["worker-3090-b"]["reassignments_in"] == 1
        assert nodes["worker-3090-b"]["reassignments_out"] == 0
        assert nodes["worker-3090-b"]["max_concurrent_prompts"] == 3
        assert (
            nodes["worker-3090-b"]["node_started_at"]
            == (started + timedelta(seconds=11)).isoformat()
        )
        assert (
            nodes["worker-3090-b"]["node_finished_at"]
            == (started + timedelta(seconds=31)).isoformat()
        )

        async with app.state.db.session() as session:
            incomplete = await session.scalar(
                select(JobAttempt).where(JobAttempt.lease_token == "lease-child-a-2")
            )
            assert incomplete is not None
            incomplete.gpu_finished_at = None
            await session.commit()
        incomplete_response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        incomplete_performance = incomplete_response.json()["performance"]
        assert incomplete_performance["gpu_service_measurements_complete"] is False
        assert incomplete_performance["frames_per_gpu_minute"] is None
        assert incomplete_performance["megapixels_per_gpu_second"] is None
        assert incomplete_performance["straggler_ratio"] is None
        incomplete_nodes = {node["node_id"]: node for node in incomplete_performance["nodes"]}
        assert incomplete_nodes["worker-3090-b"]["gpu_service_measurements_complete"] is False
        assert incomplete_nodes["worker-3090-b"]["node_finished_at"] is None

        async with app.state.db.session() as session:
            missing = await session.scalar(
                select(JobAttempt).where(JobAttempt.lease_token == "lease-child-a-2")
            )
            assert missing is not None
            missing.gpu_started_at = None
            await session.commit()
        missing_response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        missing_performance = missing_response.json()["performance"]
        assert missing_performance["gpu_service_measurements_complete"] is False
        assert missing_performance["frames_per_gpu_minute"] is None
        assert missing_performance["straggler_ratio"] is None
        missing_nodes = {node["node_id"]: node for node in missing_performance["nodes"]}
        assert missing_nodes["worker-3090-b"]["gpu_service_measurements_complete"] is False
        assert missing_nodes["worker-3090-b"]["node_finished_at"] is None

        async with app.state.db.session() as session:
            negative = await session.scalar(
                select(JobAttempt).where(JobAttempt.lease_token == "lease-child-a-2")
            )
            assert negative is not None
            negative.gpu_started_at = started + timedelta(seconds=40)
            negative.gpu_finished_at = started + timedelta(seconds=31)
            await session.commit()
        negative_response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert negative_response.json()["performance"]["straggler_ratio"] is None

        async with app.state.db.session() as session:
            restored = await session.scalar(
                select(JobAttempt).where(JobAttempt.lease_token == "lease-child-a-2")
            )
            batch = await session.get(JobBatch, batch_id)
            assert restored is not None and batch is not None
            restored.gpu_started_at = started + timedelta(seconds=11)
            restored.gpu_finished_at = started + timedelta(seconds=31)
            batch.execution_finished_at = started - timedelta(seconds=1)
            await session.commit()
        negative_parent_response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert negative_parent_response.json()["performance"]["straggler_ratio"] is None

        async with app.state.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            assert batch is not None
            batch.execution_finished_at = started + timedelta(seconds=32)
            batch.status = "RUNNING"
            await session.commit()
        nonterminal_response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        assert nonterminal_response.json()["performance"]["straggler_ratio"] is None

        async with app.state.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            moved_attempt = await session.scalar(
                select(JobAttempt).where(JobAttempt.lease_token == "lease-child-a-2")
            )
            moved_item = await session.scalar(
                select(JobBatchItem).where(
                    JobBatchItem.batch_id == batch_id,
                    JobBatchItem.ordinal == 0,
                )
            )
            moved_job = await session.get(Job, first_job_id)
            assert (
                batch is not None
                and moved_attempt is not None
                and moved_item is not None
                and moved_job is not None
            )
            batch.status = "SUCCEEDED"
            moved_attempt.node_id = "worker-3090-a"
            moved_item.node_id = "worker-3090-a"
            moved_job.node_id = "worker-3090-a"
            await session.commit()
        single_node_response = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
        )
        single_node_performance = single_node_response.json()["performance"]
        assert len(single_node_performance["nodes"]) == 1
        assert single_node_performance["straggler_ratio"] is None


async def test_batch_cancel_operation_child_guards_and_audit(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        batch_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        artifact_id = str(uuid.uuid4())
        parent_artifact_id = str(uuid.uuid4())
        batch_dir = tmp_path / "jobs" / "batches" / batch_id
        child_dir = tmp_path / "jobs" / child_id
        batch_dir.mkdir(parents=True)
        child_dir.mkdir(parents=True)
        parent_output = batch_dir / "result.zip"
        parent_output.write_bytes(b"premature-parent-result")
        child_output = child_dir / "output.png"
        child_output.write_bytes(b"child-result")
        now = datetime.now(UTC)
        async with app.state.db.session() as session:
            session.add(
                JobBatch(
                    id=batch_id,
                    tenant_id="tenant",
                    external_batch_id="assetclaw:cancel-contract:g1",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    pipeline_commit="7" * 40,
                    pipeline_sha256="8" * 64,
                    output_node="SaveImage #9",
                    status="QUEUED",
                    failure_policy="all_or_nothing",
                    output_naming="preserve_stem_png",
                    parameters={},
                    request_hash="batch-cancel-hash",
                    request_id="batch-cancel-create",
                    trace_id="batch-cancel-trace",
                    batch_dir=str(batch_dir),
                    manifest_sha256="1" * 64,
                    archive_sha256="2" * 64,
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
                Job(
                    id=child_id,
                    tenant_id="tenant",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    status="RUNNING",
                    priority="batch",
                    parameters={},
                    request_hash="batch-child-hash",
                    request_id="batch-child-request",
                    trace_id="batch-child-trace",
                    job_dir=str(child_dir),
                    batch_id=batch_id,
                    node_id="worker-3090-a",
                )
            )
            session.add(
                JobArtifact(
                    id=artifact_id,
                    job_id=child_id,
                    kind="output",
                    relative_path="output.png",
                    content_type="image/png",
                    size_bytes=len(b"child-result"),
                    sha256=hashlib.sha256(b"child-result").hexdigest(),
                )
            )
            session.add(
                BatchArtifact(
                    id=parent_artifact_id,
                    batch_id=batch_id,
                    kind="result_archive",
                    relative_path="result.zip",
                    filename="result.zip",
                    content_type="application/zip",
                    size_bytes=len(b"premature-parent-result"),
                    sha256=hashlib.sha256(b"premature-parent-result").hexdigest(),
                )
            )
            await session.commit()

        api_headers = {"X-API-Key": "gpc_abcd1234_secret"}
        child_list = await client.get(f"/api/v1/jobs/{child_id}/artifacts", headers=api_headers)
        assert child_list.status_code == 409
        assert child_list.json()["detail"]["code"] == "ARTIFACT_NOT_READY"
        child_download = await client.get(
            f"/api/v1/jobs/{child_id}/artifacts/{artifact_id}", headers=api_headers
        )
        assert child_download.status_code == 409
        assert child_download.json()["detail"]["code"] == "ARTIFACT_NOT_READY"
        parent_download = await client.get(
            f"/api/v1/batches/{batch_id}/artifacts/{parent_artifact_id}",
            headers=api_headers,
        )
        assert parent_download.status_code == 409
        assert parent_download.json()["detail"]["code"] == "ARTIFACT_NOT_READY"

        for parent_status in ("RUNNING", "ASSEMBLING"):
            async with app.state.db.session() as session:
                guarded_batch = await session.get(JobBatch, batch_id)
                assert guarded_batch is not None
                guarded_batch.status = parent_status
                await session.commit()
            for artifact_url in (
                f"/api/v1/jobs/{child_id}/artifacts",
                f"/api/v1/jobs/{child_id}/artifacts/{artifact_id}",
                f"/api/v1/batches/{batch_id}/artifacts/{parent_artifact_id}",
            ):
                blocked = await client.get(artifact_url, headers=api_headers)
                assert blocked.status_code == 409
                assert blocked.json()["detail"]["code"] == "ARTIFACT_NOT_READY"

        async with app.state.db.session() as session:
            guarded_batch = await session.get(JobBatch, batch_id)
            assert guarded_batch is not None
            guarded_batch.status = "QUEUED"
            await session.commit()

        rejected_public = await client.post(f"/api/v1/jobs/{child_id}/cancel", headers=api_headers)
        assert rejected_public.status_code == 409
        assert rejected_public.json()["detail"]["code"] == "BATCH_CHILD_CANCEL_FORBIDDEN"

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        rejected_node_interrupt = await client.post(
            "/admin/nodes/worker-3090-a/interrupt",
            headers=admin_headers,
            json={"reason": "operator interrupt test", "confirm": True},
        )
        assert rejected_node_interrupt.status_code == 409
        assert rejected_node_interrupt.json()["detail"]["code"] == "BATCH_CHILD_INTERRUPT_FORBIDDEN"
        async with app.state.db.session() as session:
            guarded_child = await session.get(Job, child_id)
            guarded_node = await session.get(Node, "worker-3090-a")
            assert guarded_child is not None and guarded_child.status == "RUNNING"
            assert guarded_child.cancel_requested is False
            assert guarded_node is not None and guarded_node.mode == "ACTIVE"
        rejected_admin = await client.post(
            f"/admin/jobs/{child_id}/cancel",
            headers=admin_headers,
            json={"reason": "must use parent batch", "confirm": True},
        )
        assert rejected_admin.status_code == 409
        assert rejected_admin.json()["detail"]["code"] == "BATCH_CHILD_CANCEL_FORBIDDEN"

        cancel_key = "assetclaw:cancel-contract:g1:cancel"
        missing_explicit_key = await client.post(
            f"/api/v1/batches/{batch_id}/cancel",
            headers={"Idempotency-Key": cancel_key},
        )
        assert missing_explicit_key.status_code == 401
        assert missing_explicit_key.json()["detail"]["code"] == "EXPLICIT_API_KEY_REQUIRED"

        cancel_headers = {
            **api_headers,
            "Idempotency-Key": cancel_key,
            "X-Request-ID": "assetclaw-cancel-01",
        }
        cancelled = await client.post(
            f"/api/v1/batches/{batch_id}/cancel",
            headers=cancel_headers,
            json={"reason": "user requested cancellation"},
        )
        assert cancelled.status_code == 200, cancelled.text
        cancel_payload = cancelled.json()
        assert cancel_payload["status"] == "CANCEL_REQUESTED"
        assert cancel_payload["cancel_status"] == "REQUESTED"
        assert cancel_payload["cancel_request_id"] == "assetclaw-cancel-01"
        assert cancel_payload["cancel_idempotency_key"] == cancel_key
        assert cancel_payload["cancel_requested_by"] == "tenant"
        assert cancel_payload["cancel_source"] == "public_api"
        assert cancel_payload["cancel_reason"] == "user requested cancellation"
        assert cancel_payload["cancel_counts"]["not_started"] == 1

        replayed = await client.post(
            f"/api/v1/batches/{batch_id}/cancel",
            headers={**cancel_headers, "X-Request-ID": "assetclaw-cancel-retry"},
            json={"reason": "this replay must not replace the original"},
        )
        assert replayed.status_code == 200
        assert replayed.json()["status"] == "CANCEL_REQUESTED"
        assert replayed.json()["cancel_operation_id"] == cancel_payload["cancel_operation_id"]
        assert replayed.json()["cancel_request_id"] == "assetclaw-cancel-01"
        assert replayed.json()["cancel_reason"] == "user requested cancellation"

        async with app.state.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            operation = await session.scalar(
                select(BatchCancelOperation).where(BatchCancelOperation.batch_id == batch_id)
            )
            assert batch is not None and operation is not None
            batch.status = "CANCELLED"
            batch.cancelled_items = 1
            batch.pending_items = 0
            batch.finished_at = datetime.now(UTC)
            operation.status = "COMPLETED"
            operation.finished_at = batch.finished_at
            operation.cancelled_items = 1
            await session.commit()
        terminal_replay = await client.post(
            f"/api/v1/batches/{batch_id}/cancel", headers=cancel_headers
        )
        assert terminal_replay.status_code == 200
        assert (
            terminal_replay.json()["cancel_operation_id"] == cancel_payload["cancel_operation_id"]
        )
        wrong_cancel_key = await client.post(
            f"/api/v1/batches/{batch_id}/cancel",
            headers={**api_headers, "Idempotency-Key": f"{cancel_key}:other"},
        )
        assert wrong_cancel_key.status_code == 409
        assert wrong_cancel_key.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

        illegal_batch_id = str(uuid.uuid4())
        illegal_external_id = "assetclaw:illegal-cancelled:g1"
        illegal_batch_dir = tmp_path / "jobs" / "batches" / illegal_batch_id
        illegal_batch_dir.mkdir(parents=True)
        async with app.state.db.session() as session:
            session.add(
                JobBatch(
                    id=illegal_batch_id,
                    tenant_id="tenant",
                    external_batch_id=illegal_external_id,
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    pipeline_commit="7" * 40,
                    pipeline_sha256="8" * 64,
                    output_node="SaveImage #9",
                    status="CANCELLED",
                    failure_policy="all_or_nothing",
                    output_naming="preserve_stem_png",
                    parameters={},
                    request_hash="illegal-cancelled-hash",
                    request_id="illegal-cancelled-request",
                    trace_id="illegal-cancelled-trace",
                    batch_dir=str(illegal_batch_dir),
                    manifest_sha256="3" * 64,
                    archive_sha256="4" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    cancelled_items=1,
                    created_at=now,
                    validated_at=now,
                    queued_at=now,
                    finished_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        illegal_terminal_cancel = await client.post(
            f"/api/v1/batches/{illegal_batch_id}/cancel",
            headers={
                **api_headers,
                "Idempotency-Key": f"{illegal_external_id}:cancel",
            },
        )
        assert illegal_terminal_cancel.status_code == 409
        assert illegal_terminal_cancel.json()["detail"]["code"] == "BATCH_NOT_CANCELLABLE"

        async with app.state.db.session() as session:
            assert int(await session.scalar(select(func.count(BatchCancelOperation.id))) or 0) == 1
            rejected_audits = int(
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "job.cancel.rejected_batch_child",
                        AuditLog.result == "REJECTED",
                    )
                )
                or 0
            )
            assert rejected_audits == 2
            node_interrupt_audits = int(
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "node.interrupt.rejected_batch_child",
                        AuditLog.result == "REJECTED",
                    )
                )
                or 0
            )
            assert node_interrupt_audits == 1
            cancel_audits = int(
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "batch.cancel",
                        AuditLog.target_id == batch_id,
                    )
                )
                or 0
            )
            assert cancel_audits == 1


async def test_admin_parent_cancel_uses_public_acknowledgement_state(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        batch_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with app.state.db.session() as session:
            session.add(
                JobBatch(
                    id=batch_id,
                    tenant_id="tenant",
                    external_batch_id="assetclaw:admin-cancel:g1",
                    workflow_key="imageclip-rgba",
                    workflow_version="test-1",
                    pipeline_commit="7" * 40,
                    pipeline_sha256="8" * 64,
                    output_node="SaveImage #9",
                    status=BatchStatus.QUEUED.value,
                    failure_policy="all_or_nothing",
                    output_naming="preserve_stem_png",
                    parameters={},
                    request_hash="admin-cancel-hash",
                    request_id="admin-cancel-create",
                    trace_id="admin-cancel-trace",
                    batch_dir=str(tmp_path / "jobs" / "batches" / batch_id),
                    manifest_sha256="1" * 64,
                    archive_sha256="2" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    pending_items=1,
                    validated_at=now,
                    queued_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        response = await client.post(
            f"/admin/batches/{batch_id}/cancel",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={"reason": "admin parent cancellation", "confirm": True},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "CANCEL_REQUESTED"
        assert response.json()["cancel_status"] == "REQUESTED"
        assert response.json()["cancel_source"] == "admin_api"

        async with app.state.db.session() as session:
            batch = await session.get(JobBatch, batch_id)
            operation = await session.scalar(
                select(BatchCancelOperation).where(BatchCancelOperation.batch_id == batch_id)
            )
            assert batch is not None and operation is not None
            assert batch.status == BatchStatus.CANCELLING.value
            assert operation.status == "REQUESTED"


async def test_uploaded_image_binding_uses_isolated_job_subfolder(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        from PIL import Image

        image_path = tmp_path / "source.png"
        Image.new("RGB", (2, 2), "white").save(image_path)
        headers = {"X-API-Key": "gpc_abcd1234_secret", "Idempotency-Key": "same-image"}
        response = await client.post(
            "/api/v1/jobs",
            headers=headers,
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
                "input_image": ("source.png", image_path.read_bytes(), "image/png"),
            },
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        repeated = await client.post(
            "/api/v1/jobs",
            headers=headers,
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
                "input_image": ("source.png", image_path.read_bytes(), "image/png"),
            },
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["job_id"] == job_id
        async with app.state.db.session() as session:
            job = await session.get(Job, job_id)
        rendered = Path(job.job_dir, "workflow", "rendered.api.json")
        payload = json.loads(rendered.read_text(encoding="utf-8"))
        assert payload["1"]["inputs"]["image"].startswith(f"{job_id}/image-source.png")


async def test_admin_login_and_destructive_confirmation(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):
        assert (
            await client.post(
                "/admin/auth/login", json={"username": "admin", "password": "wrong-password"}
            )
        ).status_code == 401
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        assert login.status_code == 200
        assert login.json()["refresh_token"]
        refreshed = await client.post(
            "/admin/auth/refresh",
            json={"refresh_token": login.json()["refresh_token"]},
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"] != login.json()["refresh_token"]
        refresh_cannot_authorize = await client.get(
            "/admin/dashboard",
            headers={"Authorization": f"Bearer {login.json()['refresh_token']}"},
        )
        assert refresh_cannot_authorize.status_code == 401
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        dashboard = await client.get("/admin/dashboard", headers=auth)
        assert dashboard.status_code == 200
        missing_confirmation = await client.put(
            "/admin/nodes/none/mode",
            headers=auth,
            json={"mode": "RESERVED", "reason": "test change", "confirm": False},
        )
        assert missing_confirmation.status_code == 409


async def test_operator_mode_change_takes_drain_ownership_without_dropping_gpu_fences(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.mode = "DRAINING"
            node.labels = {
                "substance_bake_drain_owner": "asset-api",
                "substance_bake_fence_job_ids": ["active-bake"],
                "substance_bake_pending_reservation": {
                    "job_ids": ["pending-bake"],
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                },
                "substance_bake_recovery_required": [{"job_id": "recovery-bake"}],
            }
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        changed = await client.put(
            "/admin/nodes/worker-3090-b/mode",
            headers=auth,
            json={"mode": "DRAINING", "reason": "operator maintenance", "confirm": True},
        )
        assert changed.status_code == 200, changed.text

        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert "substance_bake_drain_owner" not in node.labels
            assert node.labels["substance_bake_fence_job_ids"] == ["active-bake"]
            assert node.labels["substance_bake_pending_reservation"]["job_ids"] == [
                "pending-bake"
            ]
            assert node.labels["substance_bake_recovery_required"]


async def test_maintenance_action_is_blocked_by_substance_interlock_and_takes_drain_owner(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.mode = "DRAINING"
            node.labels = {
                "substance_bake_drain_owner": "asset-api",
                "substance_bake_fence_job_ids": ["active-bake"],
            }
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        restart = await client.post(
            "/admin/nodes/worker-3090-b/restart",
            headers=auth,
            json={"reason": "must wait for native baker", "confirm": True},
        )
        assert restart.status_code == 409, restart.text
        assert restart.json()["detail"]["code"] == "NODE_DRAINING"
        assert restart.json()["detail"]["substance_interlock"]["active"] is True

        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert "substance_bake_drain_owner" not in node.labels
            assert node.labels["substance_bake_fence_job_ids"] == ["active-bake"]


async def test_admin_nodes_selects_linux_codex_worker_not_newer_windows_baker(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        now = datetime.now(UTC)
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            labels = dict(node.labels or {})
            labels.update(
                {
                    "codex_cli_installed": True,
                    "codex_cli_version": "codex-cli 0.146.0-alpha.3.1",
                }
            )
            node.labels = labels
            db.add_all(
                [
                    AssetWorker(
                        id="asset-worker-3090-b",
                        display_name="3090-B CPU Worker",
                        node_id="worker-3090-b",
                        hostname="worker-3090-b-wsl",
                        status="ONLINE",
                        blender_version="5.1.2",
                        skill_version="asset-skills-2026.07.28",
                        max_concurrency=4,
                        current_jobs=0,
                        cpu_count=64,
                        codex_cli_version="codex-cli 0.146.0-alpha.3.1",
                        codex_auth_status="AUTHENTICATED",
                        codex_probe_status="HEALTHY",
                        codex_probe_latency_ms=12000,
                        codex_last_checked_at=now,
                        codex_last_success_at=now,
                        last_heartbeat_at=now,
                        updated_at=now,
                    ),
                    AssetWorker(
                        id="asset-worker-3090-b-windows-01",
                        display_name="3090-B Windows Substance Baker #01",
                        node_id="worker-3090-b",
                        hostname="LILITHGAMES3",
                        status="ONLINE",
                        blender_version="substance-15.1.0",
                        skill_version="substance-baker-2026.08.03-v6",
                        max_concurrency=1,
                        current_jobs=0,
                        cpu_count=64,
                        codex_auth_status="UNKNOWN",
                        codex_probe_status="NOT_RUN",
                        last_heartbeat_at=now + timedelta(seconds=1),
                        updated_at=now + timedelta(seconds=1),
                    ),
                ]
            )
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.get("/admin/nodes", headers=auth)
        assert response.status_code == 200, response.text
        worker_3090_b = next(item for item in response.json() if item["id"] == "worker-3090-b")
        runtime = worker_3090_b["codex_cli"]
        assert runtime["health"] == "HEALTHY"
        assert runtime["runtime_version"] == "codex-cli 0.146.0-alpha.3.1"
        assert runtime["auth_status"] == "AUTHENTICATED"
        assert runtime["probe_status"] == "HEALTHY"
        assert runtime["heartbeat_fresh"] is True
        assert runtime["probe_fresh"] is True
        assert runtime["scheduler_eligible"] is True
        assert runtime["eligibility_reason"] == "ELIGIBLE"


async def test_admin_nodes_exposes_optional_gpu_temperature_and_power(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-a")
            assert node is not None
            labels = dict(node.labels or {})
            labels.update({"gpu_temperature_c": 68.0, "gpu_power_w": 301.4})
            node.labels = labels
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.get("/admin/nodes", headers=auth)
        assert response.status_code == 200, response.text
        worker = next(item for item in response.json() if item["id"] == "worker-3090-a")
        assert worker["gpu_temperature_c"] == 68.0
        assert worker["gpu_power_w"] == 301.4


async def test_admin_nodes_marks_stale_codex_worker_or_probe_ineligible(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        now = datetime.now(UTC)
        settings = app.state.settings
        async with app.state.db.session() as db:
            control = await db.get(Node, "control-4090")
            worker_a = await db.get(Node, "worker-3090-a")
            assert control is not None
            assert worker_a is not None
            for node in (control, worker_a):
                labels = dict(node.labels or {})
                labels.update(
                    {
                        "codex_cli_installed": True,
                        "codex_cli_version": "codex-cli 0.146.0-alpha.3.1",
                    }
                )
                node.labels = labels

            db.add_all(
                [
                    AssetWorker(
                        id="asset-control-4090",
                        display_name="4090 CPU Worker",
                        node_id="control-4090",
                        hostname="control-4090",
                        status="ONLINE",
                        blender_version="5.1.2",
                        skill_version="asset-skills-2026.07.28-v3",
                        max_concurrency=2,
                        current_jobs=0,
                        cpu_count=24,
                        codex_cli_version="codex-cli 0.146.0-alpha.3.1",
                        codex_auth_status="AUTHENTICATED",
                        codex_probe_status="HEALTHY",
                        codex_probe_latency_ms=7000,
                        codex_last_checked_at=now,
                        codex_last_success_at=now,
                        last_heartbeat_at=now
                        - timedelta(seconds=settings.asset_worker_heartbeat_timeout_seconds + 60),
                        updated_at=now,
                    ),
                    AssetWorker(
                        id="asset-worker-3090-a",
                        display_name="3090-A CPU Worker",
                        node_id="worker-3090-a",
                        hostname="worker-3090-a",
                        status="ONLINE",
                        blender_version="5.1.2",
                        skill_version="asset-skills-2026.07.28-v3",
                        max_concurrency=3,
                        current_jobs=0,
                        cpu_count=32,
                        codex_cli_version="codex-cli 0.146.0-alpha.3.1",
                        codex_auth_status="AUTHENTICATED",
                        codex_probe_status="HEALTHY",
                        codex_probe_latency_ms=12000,
                        codex_last_checked_at=now
                        - timedelta(seconds=settings.asset_codex_probe_max_age_seconds + 60),
                        codex_last_success_at=now
                        - timedelta(seconds=settings.asset_codex_probe_max_age_seconds + 60),
                        last_heartbeat_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.get("/admin/nodes", headers=auth)
        assert response.status_code == 200, response.text
        runtimes = {item["id"]: item["codex_cli"] for item in response.json()}

        stale_worker = runtimes["control-4090"]
        assert stale_worker["health"] == "STALE"
        assert stale_worker["heartbeat_fresh"] is False
        assert stale_worker["probe_fresh"] is True
        assert stale_worker["scheduler_eligible"] is False
        assert stale_worker["eligibility_reason"] == "ASSET_WORKER_HEARTBEAT_STALE"

        stale_probe = runtimes["worker-3090-a"]
        assert stale_probe["health"] == "STALE"
        assert stale_probe["heartbeat_fresh"] is True
        assert stale_probe["probe_fresh"] is False
        assert stale_probe["scheduler_eligible"] is False
        assert stale_probe["eligibility_reason"] == "CODEX_PROBE_STALE"


async def test_admin_asset_processing_reports_real_workers_jobs_and_artifacts(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        now = datetime.now(UTC)
        job_id = str(uuid.uuid4())
        async with app.state.db.session() as db:
            db.add(
                AssetWorker(
                    id="asset-worker-3090-a",
                    display_name="3090-A Asset Worker",
                    node_id="worker-3090-a",
                    hostname="lilithgames1",
                    status="ONLINE",
                    blender_version="5.1.2",
                    skill_version="asset-skills-2026.07.28",
                    max_concurrency=4,
                    current_jobs=1,
                    cpu_count=32,
                    agent_instance_id="a" * 32,
                    agent_started_at=now - timedelta(minutes=1),
                    last_heartbeat_at=now,
                )
            )
            db.add(
                AssetJob(
                    id=job_id,
                    client_id="tenant",
                    external_asset_id="asset:chair:uv:v2",
                    job_type="UV_PROCESS_V2",
                    status="SUCCEEDED",
                    source_filename="chair.fbx",
                    input_path="/tmp/chair.fbx",
                    input_sha256="a" * 64,
                    input_size_bytes=123,
                    options={"resolution": 2048},
                    request_hash="b" * 64,
                    request_id="asset-admin-test",
                    worker_id="asset-worker-3090-a",
                    progress=100,
                    attempt_count=1,
                    started_at=now,
                    finished_at=now,
                    created_at=now,
                )
            )
            db.add(
                AssetArtifact(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    kind="fbx",
                    filename="chair_PBR_UV.fbx",
                    path="/tmp/chair_PBR_UV.fbx",
                    content_type="application/octet-stream",
                    size_bytes=456,
                    sha256="c" * 64,
                )
            )
            await db.commit()
        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.get("/admin/asset-processing", headers=auth)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["schema_version"] == "asset-admin.v4"
        assert payload["summary"]["online_workers"] == 1
        assert payload["summary"]["total_slots"] == 4
        assert payload["summary"]["used_slots"] == 1
        assert payload["workers"][0]["skill_version"] == "asset-skills-2026.07.28"
        assert payload["jobs"][0]["job_type"] == "UV_PROCESS_V2"
        assert payload["jobs"][0]["artifacts"][0]["filename"] == "chair_PBR_UV.fbx"
        assert payload["contracts"]["uv"]["artifact_count"] == 5
        assert payload["jobs_scope"]["active_only"] is False

        active = await client.get(
            "/admin/asset-processing?limit=500&active_only=true", headers=auth
        )
        assert active.status_code == 200
        assert active.json()["jobs"] == []
        assert active.json()["jobs_scope"] == {
            "active_only": True,
            "limit": 500,
            "returned": 0,
            "saturated": False,
        }

        async with app.state.db.session() as db:
            db.add(
                AssetJob(
                    id=str(uuid.uuid4()),
                    client_id="tenant",
                    external_asset_id="asset:future:state",
                    job_type="UV_PROCESS_V2",
                    status="FUTURE_NON_TERMINAL",
                    source_filename="future.fbx",
                    input_path="/tmp/future.fbx",
                    input_sha256="f" * 64,
                    input_size_bytes=1,
                    options={},
                    request_hash="e" * 64,
                    request_id="asset-admin-future-state-test",
                    created_at=now,
                )
            )
            await db.commit()

        unknown_active = await client.get(
            "/admin/asset-processing?limit=500&active_only=true", headers=auth
        )
        assert unknown_active.status_code == 200
        assert [job["status"] for job in unknown_active.json()["jobs"]] == ["FUTURE_NON_TERMINAL"]


async def test_admin_asset_processing_explains_substance_next_turn_reservation(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        now = datetime.now(UTC)
        job_id = str(uuid.uuid4())
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.mode = "DRAINING"
            node.current_jobs = 1
            node.labels = {
                **dict(node.labels or {}),
                "substance_bake_drain_owner": "asset-api",
                "substance_bake_pending_reservation": {
                    "job_ids": [job_id],
                    "worker_ids": ["asset-worker-3090-b-windows-01"],
                    "expires_at": (now + timedelta(minutes=5)).isoformat(),
                    "max_parallel": 4,
                },
            }
            db.add(
                AssetJob(
                    id=job_id,
                    client_id="tenant",
                    external_asset_id="asset:chair:bake:v1",
                    job_type="SUBSTANCE_BAKE_V1",
                    status="QUEUED",
                    source_filename="substance_bake_input.zip",
                    input_path="/tmp/substance_bake_input.zip",
                    input_sha256="d" * 64,
                    input_size_bytes=123,
                    options={"profile": "li3d-pbr-full-v2"},
                    request_hash="e" * 64,
                    request_id="asset-admin-substance-wait-test",
                    created_at=now,
                )
            )
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.get("/admin/asset-processing?active_only=true", headers=auth)
        assert response.status_code == 200, response.text
        payload = response.json()
        job = next(row for row in payload["jobs"] if row["job_id"] == job_id)

        assert job["resource_wait"] == {
            "code": "WAITING_FOR_COMFYUI_FRAME",
            "message": ("已获 3090-B 下一轮优先权，等待当前 ComfyUI 帧安全结束后切换烘焙"),
            "node_id": "worker-3090-b",
            "reservation_active": True,
            "fence_active": False,
            "comfyui_current_jobs": 1,
        }
        assert payload["substance_gpu"]["sharing_policy"] == ("exclusive_turn_with_comfyui")
        assert payload["substance_gpu"]["reserved_job_ids"] == [job_id]
        assert payload["substance_gpu"]["comfyui_current_jobs"] == 1


async def test_admin_asset_processing_reports_full_substance_capacity(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        now = datetime.now(UTC)
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.mode = "DRAINING"
            node.health = "ONLINE"
            node.current_jobs = 0
            node.labels = {
                "substance_bake_drain_owner": "asset-api",
                "substance_bake_fence_job_ids": [
                    "active-bake-01",
                    "active-bake-02",
                    "active-bake-03",
                    "active-bake-04",
                ],
            }
            for index in range(1, 5):
                db.add(
                    AssetWorker(
                        id=f"asset-worker-3090-b-windows-0{index}",
                        display_name=f"3090-B Substance Worker #{index}",
                        node_id="worker-3090-b",
                        hostname="3090-b-windows",
                        status="ONLINE",
                        blender_version="substance-15.1.0",
                        skill_version="substance-baker-2026.08.03-v6",
                        max_concurrency=1,
                        current_jobs=0,
                        cpu_count=64,
                        agent_instance_id=f"{index}" * 32,
                        agent_started_at=now - timedelta(minutes=1),
                        last_heartbeat_at=now,
                    )
                )
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = await client.get("/admin/asset-processing", headers=auth)
        assert response.status_code == 200, response.text
        summary = response.json()["summary"]
        assert summary["online_workers"] == 4
        assert summary["schedulable_workers"] == 4
        assert summary["reported_total_slots"] == 4
        assert summary["total_slots"] == 4
        assert summary["used_slots"] == 4
        assert summary["available_slots"] == 0
        assert summary["total_slots"] == summary["used_slots"] + summary["available_slots"]


async def test_admin_views_separate_production_and_test_traffic(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        now = datetime.now(UTC)
        async with app.state.db.session() as db:
            test_client = ApiClient(
                id="load-test-tenant",
                name="Load Test Tenant",
                role="client",
                client_kind="test",
                max_queued=200,
                max_running=1,
                daily_quota=1000,
            )
            db.add(test_client)
            db.add_all(
                [
                    Job(
                        id="production-job",
                        tenant_id="tenant",
                        workflow_key="fake",
                        workflow_version="1",
                        status="SUCCEEDED",
                        priority="normal",
                        parameters={},
                        request_hash="production",
                        request_id="production-request",
                        trace_id="production-trace",
                        job_dir=str(tmp_path / "production-job"),
                        progress=100,
                        created_at=now,
                        started_at=now,
                        finished_at=now,
                    ),
                    Job(
                        id="test-job",
                        tenant_id="load-test-tenant",
                        workflow_key="fake",
                        workflow_version="1",
                        status="SUCCEEDED",
                        priority="normal",
                        parameters={},
                        request_hash="test",
                        request_id="test-request",
                        trace_id="test-trace",
                        job_dir=str(tmp_path / "test-job"),
                        progress=100,
                        created_at=now,
                        started_at=now,
                        finished_at=now,
                    ),
                ]
            )
            db.add(
                IdempotencyKey(
                    client_id="load-test-tenant",
                    key="load:run-01:mvr:00000001",
                    request_hash="f" * 64,
                    job_id="test-job",
                    created_at=now,
                    expires_at=now + timedelta(days=1),
                )
            )
            await db.commit()

        production_jobs = await client.get("/admin/jobs", headers=auth)
        assert production_jobs.status_code == 200
        assert [row["job_id"] for row in production_jobs.json()] == ["production-job"]
        assert production_jobs.json()[0]["client_kind"] == "production"

        test_jobs = await client.get("/admin/jobs?client_kind=test", headers=auth)
        assert test_jobs.status_code == 200
        assert [row["job_id"] for row in test_jobs.json()] == ["test-job"]
        assert test_jobs.json()[0]["client_kind"] == "test"
        assert test_jobs.json()[0]["request_id"] == "test-request"
        assert test_jobs.json()[0]["idempotency_key"] == "load:run-01:mvr:00000001"

        all_jobs = await client.get("/admin/jobs?client_kind=all", headers=auth)
        assert {row["job_id"] for row in all_jobs.json()} == {
            "production-job",
            "test-job",
        }

        production_dashboard = await client.get("/admin/dashboard", headers=auth)
        test_dashboard = await client.get("/admin/dashboard?client_kind=test", headers=auth)
        assert production_dashboard.json()["client_kind"] == "production"
        assert test_dashboard.json()["client_kind"] == "test"
        assert production_dashboard.json()["jobs"]["SUCCEEDED"] == 1
        assert test_dashboard.json()["jobs"]["SUCCEEDED"] == 1


async def test_admin_load_session_collision_lookup_is_exact_and_uncapped(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        session_id = str(uuid.uuid4())
        other_session_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with app.state.db.session() as db:
            db.add(
                Job(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant",
                    workflow_key="modelview-roughness",
                    workflow_version="1",
                    status="SUCCEEDED",
                    priority="normal",
                    parameters={},
                    request_hash="load-session-gpu",
                    request_id=f"lt:{session_id}:mvr:00000001",
                    trace_id="load-session-gpu-trace",
                    job_dir=str(tmp_path / "load-session-gpu"),
                    progress=100,
                    created_at=now,
                    started_at=now,
                    finished_at=now,
                )
            )
            db.add(
                JobBatch(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant",
                    external_batch_id=(
                        f"loadtest:{session_id}:imageclip_batch:00000002"
                    ),
                    workflow_key="imageclip-rgba",
                    workflow_version="1",
                    status="SUCCEEDED",
                    parameters={},
                    request_hash="load-session-batch",
                    request_id="load-session-batch-request",
                    trace_id="load-session-batch-trace",
                    batch_dir=str(tmp_path / "load-session-batch"),
                    manifest_sha256="1" * 64,
                    archive_sha256="2" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    pending_items=0,
                    succeeded_items=1,
                    progress=100,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                AssetJob(
                    id=str(uuid.uuid4()),
                    client_id="tenant",
                    external_asset_id=f"loadtest:{session_id}:uv_process:00000003",
                    job_type="UV_PROCESS_V2",
                    status="SUCCEEDED",
                    source_filename="load-session.fbx",
                    input_path=str(tmp_path / "load-session.fbx"),
                    input_sha256="3" * 64,
                    input_size_bytes=1,
                    options={},
                    request_hash="load-session-asset",
                    request_id="load-session-asset-request",
                    progress=100,
                    created_at=now,
                    started_at=now,
                    finished_at=now,
                )
            )
            await db.commit()

        login = await client.post(
            "/admin/auth/login",
            json={"username": "admin", "password": "correct-password"},
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        collision = await client.get(
            f"/admin/load-sessions/{session_id}/collisions",
            headers=auth,
        )
        assert collision.status_code == 200, collision.text
        assert collision.json() == {
            "schema_version": "gpu-control-load-session-collision.v1",
            "session_id": session_id,
            "collision_free": False,
            "collision_count": 3,
            "counts": {"gpu_jobs": 1, "gpu_batches": 1, "asset_jobs": 1},
            "scope": "exact_global_session_namespace",
        }

        collision_free = await client.get(
            f"/admin/load-sessions/{other_session_id}/collisions",
            headers=auth,
        )
        assert collision_free.status_code == 200
        assert collision_free.json()["collision_free"] is True
        assert collision_free.json()["collision_count"] == 0

        invalid = await client.get(
            "/admin/load-sessions/not-a-uuid/collisions",
            headers=auth,
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "LOAD_SESSION_INVALID"


async def test_admin_retry_clears_previous_execution_and_keeps_error_audit(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        created = await client.post(
            "/api/v1/jobs",
            headers={"X-API-Key": "gpc_abcd1234_secret"},
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
            },
        )
        job_id = created.json()["job_id"]
        now = datetime.now(UTC)
        async with app.state.db.session() as db:
            job = await db.get(Job, job_id)
            assert job is not None
            job.status = "FAILED"
            job.node_id = "worker-3090-a"
            job.prompt_id = "old-prompt"
            job.submission_client_id = "gpu-control-old-attempt"
            job.submission_intent_at = now
            job.progress = 73
            job.attempt_count = 1
            job.cancel_requested = True
            job.error_code = "COMFY_EXECUTION_ERROR"
            job.error_message = "CUDA out of memory"
            job.claimed_at = now
            job.started_at = now
            job.finished_at = now
            job.not_before = now
            await db.commit()

        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        retried = await client.post(
            f"/admin/jobs/{job_id}/retry",
            headers=auth,
            json={"reason": "retry after memory cleanup", "confirm": True},
        )
        assert retried.status_code == 200, retried.text
        payload = retried.json()
        assert payload["status"] == "QUEUED"
        assert payload["node_id"] is None
        assert payload["prompt_id"] is None
        assert payload["progress"] == 0
        assert payload["started_at"] is None
        assert payload["finished_at"] is None
        assert payload["error"] is None

        async with app.state.db.session() as db:
            job = await db.get(Job, job_id)
            assert job is not None
            assert job.claimed_at is None and job.not_before is None
            assert job.submission_client_id is None and job.submission_intent_at is None
            assert job.cancel_requested is False
            event = await db.scalar(
                select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.event == "admin.retry")
            )
            assert event is not None
            assert event.details["previous_error"] == {
                "code": "COMFY_EXECUTION_ERROR",
                "message": "CUDA out of memory",
            }


async def test_source_ip_auto_enrolls_without_api_key(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        response = await client.get("/api/v1/workflows")
        assert response.status_code == 200
        async with app.state.db.session() as session:
            clients = list((await session.scalars(select(ApiClient))).all())
        discovered = next(row for row in clients if row.last_seen_ip == "127.0.0.1")
        assert discovered.allowed_ips == ["127.0.0.1"]


async def test_direct_image_service_reports_missing_workflow(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):
        for endpoint in (
            "/api/v1/services/imageclip-rgba",
            "/api/v1/services/modelview-inpaint",
            "/api/v1/services/modelview-roughness",
        ):
            response = await client.post(
                endpoint,
                files={"image": ("input.png", b"not-an-image", "image/png")},
            )
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "WORKFLOW_NOT_FOUND"


async def test_callback_url_allowlist_and_one_time_secret(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):
        headers = {"X-API-Key": "gpc_abcd1234_secret"}
        base_files = {
            "workflow_key": (None, "fake"),
            "workflow_version": (None, "1"),
            "parameters": (None, '{"steps":20}'),
        }
        rejected = await client.post(
            "/api/v1/jobs",
            headers=headers,
            files={**base_files, "callback_url": (None, "https://127.0.0.1/hook")},
        )
        assert rejected.status_code == 422
        accepted = await client.post(
            "/api/v1/jobs",
            headers=headers,
            files={**base_files, "callback_url": (None, "https://callback.example.com/hook")},
        )
        assert accepted.status_code == 202
        assert len(accepted.json()["callback_secret"]) == 64


async def test_one_hundred_concurrent_submissions_are_accepted(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):

        async def submit(index: int, api_client: httpx.AsyncClient = client) -> int:
            response = await api_client.post(
                "/api/v1/jobs",
                headers={"X-API-Key": "gpc_abcd1234_secret", "Idempotency-Key": f"load-{index}"},
                files={
                    "workflow_key": (None, "fake"),
                    "workflow_version": (None, "1"),
                    "parameters": (None, '{"steps":20}'),
                },
            )
            return response.status_code

        results = await asyncio.gather(*(submit(index) for index in range(100)))
        assert results.count(202) == 100, {code: results.count(code) for code in set(results)}


async def test_public_api_never_accepts_admin_key_or_cross_tenant_access(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):
        assert (
            await client.get(
                "/api/v1/workflows", headers={"X-API-Key": "gpc_adminkey_admin-secret"}
            )
        ).status_code == 403
        created = await client.post(
            "/api/v1/jobs",
            headers={"X-API-Key": "gpc_tenantb1_secret-b"},
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
            },
        )
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        tenant_a = {"X-API-Key": "gpc_abcd1234_secret"}
        assert (await client.get(f"/api/v1/jobs/{job_id}", headers=tenant_a)).status_code == 404
        assert (
            await client.get(f"/api/v1/jobs/{job_id}/events", headers=tenant_a)
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=tenant_a)
        ).status_code == 404
        assert (
            await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=tenant_a)
        ).status_code == 404


async def test_admin_cannot_create_api_key_for_admin_and_request_id_is_stable(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        rejected = await client.post(
            "/admin/clients/admin/keys",
            headers=auth,
            json={"reason": "should not be allowed", "confirm": True},
        )
        assert rejected.status_code == 409

        request_id = "deploy-smoke-001"
        created = await client.post(
            "/api/v1/jobs",
            headers={"X-API-Key": "gpc_abcd1234_secret", "X-Request-ID": request_id},
            files={
                "workflow_key": (None, "fake"),
                "workflow_version": (None, "1"),
                "parameters": (None, '{"steps":20}'),
            },
        )
        assert created.headers["x-request-id"] == request_id
        async with app.state.db.session() as db:
            job = await db.get(Job, created.json()["job_id"])
            assert job is not None and job.request_id == request_id


async def test_same_idempotency_key_concurrency_creates_one_job_and_directory(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):

        async def submit(api_client: httpx.AsyncClient = client) -> httpx.Response:
            return await api_client.post(
                "/api/v1/jobs",
                headers={
                    "X-API-Key": "gpc_abcd1234_secret",
                    "Idempotency-Key": "one-job-only",
                },
                files={
                    "workflow_key": (None, "fake"),
                    "workflow_version": (None, "1"),
                    "parameters": (None, '{"steps":20}'),
                },
            )

        responses = await asyncio.gather(*(submit() for _ in range(100)))
        assert sum(item.status_code == 202 for item in responses) == 1
        assert sum(item.status_code == 200 for item in responses) == 99
        assert len({item.json()["job_id"] for item in responses}) == 1
        async with app.state.db.session() as db:
            count = await db.scalar(select(func.count(Job.id)).where(Job.tenant_id == "tenant"))
            assert count == 1
        permanent = [path for path in (tmp_path / "jobs").glob("*/*/*/*") if path.is_dir()]
        assert len(permanent) == 1
        staging = tmp_path / "jobs" / ".staging"
        assert not staging.exists() or not any(staging.iterdir())


async def test_alertmanager_webhook_is_authenticated_and_deduplicated(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "GpuOffline", "severity": "warning"},
                    "annotations": {"summary": "worker unavailable"},
                    "startsAt": "2026-07-22T00:00:00Z",
                    "fingerprint": "alert-one",
                }
            ]
        }
        assert (await client.post("/internal/alerts/webhook", json=payload)).status_code == 401
        headers = {"Authorization": "Bearer development-only-change-me"}
        first = await client.post("/internal/alerts/webhook", headers=headers, json=payload)
        second = await client.post("/internal/alerts/webhook", headers=headers, json=payload)
        assert first.status_code == 200 and first.json()["queued_notifications"] == 1
        assert second.status_code == 200 and second.json()["queued_notifications"] == 0
        async with app.state.db.session() as db:
            alert = await db.get(Alert, "alert-one")
            assert alert is not None and alert.status == "firing"


async def test_workflow_import_builds_node_compatibility_and_can_be_enabled(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        bundle = {
            "workflow_key": "production-test",
            "version": "2026.07.22",
            "display_name": "生产工作流导入验证",
            "template": {"9": {"class_type": "SaveImage", "inputs": {}}},
            "parameter_schema": {"type": "object", "additionalProperties": False},
            "bindings": {},
            "allowed_class_types": ["SaveImage"],
            "required_models": [],
            "required_custom_nodes": [],
            "min_vram_mb": 20000,
            "timeout_seconds": 900,
            "node_labels": {},
            "output_nodes": ["9"],
        }
        imported = await client.post("/admin/workflows", headers=auth, json=bundle)
        assert imported.status_code == 200, imported.text
        compatibility = imported.json()["compatibility"]
        assert len(compatibility) == 3
        assert all(item["compatible"] for item in compatibility)

        version_id = imported.json()["id"]
        enabled = await client.put(
            f"/admin/workflows/{version_id}/enabled?enabled=true",
            headers=auth,
            json={"reason": "deploy validation", "confirm": True},
        )
        assert enabled.status_code == 200 and enabled.json()["enabled"] is True
        async with app.state.db.session() as db:
            rows = (
                await db.scalars(
                    select(WorkflowNodeCompatibility).where(
                        WorkflowNodeCompatibility.workflow_version_id == version_id
                    )
                )
            ).all()
            assert len(rows) == 3 and all(row.compatible for row in rows)


async def test_runtime_overflow_settings_are_validated_and_persisted(tmp_path: Path) -> None:
    async for _, client in prepared_app(tmp_path):
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        enabled = await client.put(
            "/admin/settings/overflow_4090_auto_enabled",
            headers=auth,
            json={"value": True, "reason": "enable overflow", "confirm": True},
        )
        assert enabled.status_code == 200 and enabled.json()["value"] is True
        window = await client.put(
            "/admin/settings/overflow_4090_allowed_windows",
            headers=auth,
            json={"value": "09:00-18:00,21:00-23:00", "reason": "business hours", "confirm": True},
        )
        assert window.status_code == 200
        settings = await client.get("/admin/settings", headers=auth)
        assert settings.json()["overflow_4090_auto_enabled"] is True
        assert settings.json()["overflow_4090_allowed_windows"] == "09:00-18:00,21:00-23:00"


async def test_admin_can_update_discovered_client_limits_and_access(tmp_path: Path) -> None:
    async for app, client in prepared_app(tmp_path):
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        updated = await client.put(
            "/admin/clients/tenant",
            headers=auth,
            json={
                "name": "局部重绘客户端",
                "client_kind": "test",
                "enabled": False,
                "max_queued": 12,
                "max_running": 1,
                "daily_quota": 240,
                "weight": 2,
                "allowed_ips": ["10.3.34.9"],
                "callback_hosts": ["callback.example.com"],
                "reason": "limit client traffic",
                "confirm": True,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["enabled"] is False
        assert updated.json()["allowed_ips"] == ["10.3.34.9"]
        async with app.state.db.session() as db:
            stored = await db.get(ApiClient, "tenant")
            assert stored is not None
            assert stored.name == "局部重绘客户端"
            assert stored.client_kind == "test"
            assert stored.max_queued == 12
            assert stored.daily_quota == 240
            assert stored.enabled is False

        conflict = await client.put(
            "/admin/clients/tenant-b",
            headers=auth,
            json={
                "name": "Tenant B",
                "client_kind": "production",
                "enabled": True,
                "max_queued": 20,
                "max_running": 1,
                "daily_quota": 1000,
                "weight": 1,
                "allowed_ips": ["10.3.34.9"],
                "callback_hosts": [],
                "reason": "bind duplicate ip",
                "confirm": True,
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "CLIENT_IP_CONFLICT"


async def test_admin_client_kind_update_uses_global_then_client_row_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for app, client in prepared_app(tmp_path):
        login = await client.post(
            "/admin/auth/login", json={"username": "admin", "password": "correct-password"}
        )
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        lock_order: list[str] = []
        original_get = AsyncSession.get

        async def record_global_lock(_: AsyncSession, events: list[str] = lock_order) -> None:
            events.append("global")

        async def record_get(
            session: AsyncSession,
            entity: Any,
            ident: Any,
            events: list[str] = lock_order,
            get: Any = original_get,
            **kwargs: Any,
        ) -> Any:
            if entity is ApiClient and ident == "tenant" and kwargs.get("with_for_update"):
                events.append("client-row")
            return await get(session, entity, ident, **kwargs)

        async def reject_tenant_lock(_: AsyncSession, __: str) -> None:
            raise AssertionError("admin client update must not acquire a tenant lock")

        monkeypatch.setattr(
            app.state.db,
            "acquire_global_admission_transaction_lock",
            record_global_lock,
        )
        monkeypatch.setattr(
            app.state.db,
            "acquire_tenant_transaction_lock",
            reject_tenant_lock,
        )
        monkeypatch.setattr(AsyncSession, "get", record_get)
        updated = await client.put(
            "/admin/clients/tenant",
            headers=auth,
            json={
                "name": "Tenant",
                "client_kind": "test",
                "enabled": True,
                "max_queued": 200,
                "max_running": 1,
                "daily_quota": 1000,
                "weight": 1,
                "allowed_ips": [],
                "callback_hosts": ["callback.example.com"],
                "reason": "atomic admission classification",
                "confirm": True,
            },
        )
        assert updated.status_code == 200, updated.text
        assert lock_order == ["global", "client-row"]
        async with app.state.db.session() as db:
            stored = await original_get(db, ApiClient, "tenant")
            assert stored is not None
            assert stored.client_kind == "test"


async def test_signed_node_heartbeat_updates_address_and_dynamic_monitoring(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        payload = {
            "gpu_uuid": "GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c",
            "gpu_model": "NVIDIA GeForce RTX 3090",
            "hostname": "gpu-worker-a",
            "node_agent_version": "1.5.5",
            "source_revision": "9" * 40,
            "imageclip_commit": "7" * 40,
            "imageclip_pipeline_sha256": "8" * 64,
            "ip": "10.0.0.99",
            "mac": "18:c0:4d:9f:13:13",
            "node_id": "worker-3090-a",
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        nonce = "heartbeat-once"
        headers = {
            "content-type": "application/json",
            "x-gpu-timestamp": timestamp,
            "x-gpu-nonce": nonce,
            "x-gpu-signature": sign_agent_request(
                "POST",
                "/api/v1/nodes/heartbeat",
                body,
                timestamp,
                nonce,
                app.state.settings.node_agent_secret("worker-3090-a"),
            ),
            "x-real-ip": "10.0.0.99",
        }
        response = await client.post("/api/v1/nodes/heartbeat", content=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["base_url"] == "http://10.0.0.99:8188"
        replay = await client.post("/api/v1/nodes/heartbeat", content=body, headers=headers)
        assert replay.status_code == 409
        legacy_payload = dict(payload)
        legacy_payload.pop("gpu_model")
        legacy_body = json.dumps(legacy_payload, separators=(",", ":"), sort_keys=True).encode()
        legacy_nonce = "heartbeat-legacy-agent"
        legacy_headers = {
            **headers,
            "x-gpu-nonce": legacy_nonce,
            "x-gpu-signature": sign_agent_request(
                "POST",
                "/api/v1/nodes/heartbeat",
                legacy_body,
                timestamp,
                legacy_nonce,
                app.state.settings.node_agent_secret("worker-3090-a"),
            ),
        }
        legacy = await client.post(
            "/api/v1/nodes/heartbeat", content=legacy_body, headers=legacy_headers
        )
        assert legacy.status_code == 200, legacy.text
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-a")
            assert node is not None
            assert node.agent_url == "http://10.0.0.99:9201"
            assert node.labels["mac"] == "18:c0:4d:9f:13:13"
            assert node.labels["gpu_uuid"] == payload["gpu_uuid"]
            assert node.labels["gpu_model"] == "NVIDIA GeForce RTX 3090"
            assert node.labels["imageclip_commit"] == "7" * 40
            assert node.labels["imageclip_pipeline_sha256"] == "8" * 64
            assert node.labels["node_agent_version"] == "1.5.5"
            assert node.labels["source_revision"] == "9" * 40
            assert node.custom_nodes_version == "imageclip:777777777777:888888888888"
        targets = await client.get("/internal/prometheus/workers")
        assert targets.status_code == 200
        assert any(group["targets"] == ["10.0.0.99:9400"] for group in targets.json())

        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-a")
            assert node is not None
            node.labels = {**dict(node.labels or {}), "wsl_runtime": "Ubuntu WSL2"}
            await db.commit()
        wsl_targets = await client.get("/internal/prometheus/workers")
        assert wsl_targets.status_code == 200
        assert not any(
            group["targets"] == ["10.0.0.99:9400"] for group in wsl_targets.json()
        )
        assert any(group["targets"] == ["10.0.0.99:9100"] for group in wsl_targets.json())

        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-a")
            assert node is not None
            node.labels = {**dict(node.labels or {}), "dcgm_exporter_enabled": True}
            await db.commit()
        explicit_dcgm_targets = await client.get("/internal/prometheus/workers")
        assert any(
            group["targets"] == ["10.0.0.99:9400"]
            for group in explicit_dcgm_targets.json()
        )
