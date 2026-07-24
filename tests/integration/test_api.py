import asyncio
import hashlib
import io
import json
import time
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from gpu_control_api.main import create_app
from sqlalchemy import func, select

from packages.gpu_control_core.models import (
    Alert,
    ApiClient,
    ApiKey,
    Base,
    BatchArtifact,
    Job,
    JobBatch,
    JobEvent,
    Node,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.security import hash_api_secret, hash_password, sign_agent_request
from packages.gpu_control_core.settings import Settings


async def prepared_app(tmp_path: Path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        redis_url="redis://127.0.0.1:6399/15",
        job_root=tmp_path / "jobs",
        jwt_secret="test-jwt",
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
                        labels={"gpu_family": "3090"},
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
                        labels={"gpu_family": "3090"},
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
                        labels={"gpu_family": "4090"},
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
                    node_labels={},
                    output_nodes=["9"],
                    enabled=True,
                    template_sha256="x",
                )
            )
            await session.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


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
                    node_labels={},
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
        assert own_status.json()["counts"] == {
            "total": 1,
            "pending": 1,
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
        }
        foreign_status = await client.get(
            f"/api/v1/batches/{batch_id}",
            headers={"X-API-Key": "gpc_tenantb1_secret-b"},
        )
        assert foreign_status.status_code == 404

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
        assert public_url.startswith(f"/api/v1/batches/{batch_id}/artifacts/")
        assert (
            await client.get(
                public_url, headers={"X-API-Key": "gpc_abcd1234_secret"}
            )
        ).content == b"result"
        assert (
            await client.get(
                public_url, headers={"X-API-Key": "gpc_tenantb1_secret-b"}
            )
        ).status_code == 404
        async with app.state.db.session() as session:
            assert await session.get(JobBatch, batch_id) is not None


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
            assert job.cancel_requested is False
            event = await db.scalar(
                select(JobEvent).where(
                    JobEvent.job_id == job_id, JobEvent.event == "admin.retry"
                )
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
        response = await client.post(
            "/api/v1/services/imageclip-rgba",
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
            assert stored.max_queued == 12
            assert stored.daily_quota == 240
            assert stored.enabled is False

        conflict = await client.put(
            "/admin/clients/tenant-b",
            headers=auth,
            json={
                "name": "Tenant B",
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


async def test_signed_node_heartbeat_updates_address_and_dynamic_monitoring(
    tmp_path: Path,
) -> None:
    async for app, client in prepared_app(tmp_path):
        payload = {
            "gpu_uuid": "GPU-9f116ee8-a845-c3a3-b10d-fdd6a9f8cc6c",
            "hostname": "gpu-worker-a",
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
        response = await client.post(
            "/api/v1/nodes/heartbeat", content=body, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["base_url"] == "http://10.0.0.99:8188"
        replay = await client.post(
            "/api/v1/nodes/heartbeat", content=body, headers=headers
        )
        assert replay.status_code == 409
        async with app.state.db.session() as db:
            node = await db.get(Node, "worker-3090-a")
            assert node is not None
            assert node.agent_url == "http://10.0.0.99:9201"
            assert node.labels["mac"] == "18:c0:4d:9f:13:13"
            assert node.labels["gpu_uuid"] == payload["gpu_uuid"]
        targets = await client.get("/internal/prometheus/workers")
        assert targets.status_code == 200
        assert any(
            group["targets"] == ["10.0.0.99:9400"] for group in targets.json()
        )
