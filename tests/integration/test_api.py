import asyncio
import uuid
from pathlib import Path

import httpx
from gpu_control_api.main import create_app
from sqlalchemy import func, select

from packages.gpu_control_core.models import (
    Alert,
    ApiClient,
    ApiKey,
    Base,
    Job,
    Node,
    Workflow,
    WorkflowNodeCompatibility,
    WorkflowVersion,
)
from packages.gpu_control_core.security import hash_api_secret, hash_password
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
                        "3": {"class_type": "KSampler", "inputs": {"steps": 20}},
                        "9": {"class_type": "SaveImage", "inputs": {}},
                    },
                    parameter_schema={
                        "type": "object",
                        "properties": {"steps": {"type": "integer", "minimum": 1, "maximum": 100}},
                        "required": ["steps"],
                        "additionalProperties": False,
                    },
                    bindings={"steps": "3.inputs.steps"},
                    allowed_class_types=["KSampler", "SaveImage"],
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
        assert (await client.get("/api/v1/workflows")).status_code == 401
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
        auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
        dashboard = await client.get("/admin/dashboard", headers=auth)
        assert dashboard.status_code == 200
        missing_confirmation = await client.put(
            "/admin/nodes/none/mode",
            headers=auth,
            json={"mode": "RESERVED", "reason": "test change", "confirm": False},
        )
        assert missing_confirmation.status_code == 409


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
