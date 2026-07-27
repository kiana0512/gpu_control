import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx
from gpu_control_asset_api.main import create_app

from packages.gpu_control_core.models import ApiClient, ApiKey, Base
from packages.gpu_control_core.security import hash_api_secret, sign_agent_request
from packages.gpu_control_core.settings import Settings


async def prepared_asset_app(tmp_path: Path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'asset.db').as_posix()}",
        redis_url="redis://127.0.0.1:6399/15",
        job_root=tmp_path / "jobs",
        asset_root=tmp_path / "assets",
        jwt_secret="test-jwt",
        api_key_pepper="test-pepper",
        node_agent_hmac_secret="test-node-agent",
        asset_worker_hmac_secret="asset-worker-secret-that-is-long-enough",
        alertmanager_webhook_token="test-alert-token",
        asset_worker_min_available_memory_mb=1024,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.db.session() as db:
            db.add(
                ApiClient(
                    id="asset-client",
                    name="Asset Client",
                    role="client",
                    client_kind="production",
                    max_queued=50,
                    max_running=10,
                )
            )
            db.add(
                ApiKey(
                    id=str(uuid.uuid4()),
                    client_id="asset-client",
                    prefix="assetkey",
                    secret_hash=hash_api_secret("secret", settings.api_key_pepper),
                )
            )
            await db.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield settings, client


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


async def signed_post(
    client: httpx.AsyncClient, settings: Settings, path: str, payload: dict[str, object]
) -> httpx.Response:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return await client.post(path, content=body, headers=worker_headers(settings, path, body))


async def test_uv_asset_job_contract_worker_concurrency_and_atomic_artifacts(
    tmp_path: Path,
) -> None:
    client_headers = {
        "X-API-Key": "gpc_assetkey_secret",
        "Idempotency-Key": "asset:chair:g1",
        "X-Request-ID": "asset-chair-create-1",
    }
    metadata = {
        "external_asset_id": "asset:chair:g1",
        "options": {
            "resolution": 2048,
            "padding_px": 10,
            "hard_edge_angle_degrees": 75,
            "hidden_axis": "y+",
            "texel_density_mode": "uniform",
            "qa_profile": "pbr-v1",
        },
    }
    input_bytes = b"synthetic fbx payload"
    async for settings, client in prepared_asset_app(tmp_path):
        created = await client.post(
            "/api/v1/assets/uv/unwrap",
            headers=client_headers,
            files={
                "asset": ("chair.fbx", input_bytes, "application/octet-stream"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert created.status_code == 202, created.text
        assert created.headers["X-Request-ID"] == "asset-chair-create-1"
        job_id = created.json()["job_id"]
        repeated = await client.post(
            "/api/v1/assets/uv/unwrap",
            headers=client_headers,
            files={
                "asset": ("chair.fbx", input_bytes, "application/octet-stream"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["job_id"] == job_id

        heartbeat_path = "/internal/v1/assets/workers/heartbeat"
        heartbeat = await signed_post(
            client,
            settings,
            heartbeat_path,
            {
                "worker_id": "asset-worker-3090-a",
                "node_id": "worker-3090-a",
                "display_name": "3090-A CPU Worker",
                "hostname": "lilithgames1",
                "blender_version": "5.1.2",
                "skill_version": "pbr-uv-v1.1",
                "cpu_count": 32,
                "max_concurrency": 4,
                "current_jobs": 0,
                "load_1m": 1.0,
                "available_memory_mb": 100000,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["status"] == "ONLINE"

        capacity = await client.get(
            "/api/v1/assets/capacity", headers={"X-API-Key": "gpc_assetkey_secret"}
        )
        assert capacity.status_code == 200
        assert capacity.json()["total_slots"] == 4
        assert capacity.json()["available_slots"] == 4

        claim = await signed_post(
            client,
            settings,
            "/internal/v1/assets/jobs/claim",
            {
                "worker_id": "asset-worker-3090-a",
                "load_1m": 1.0,
                "available_memory_mb": 100000,
            },
        )
        assert claim.status_code == 200, claim.text
        leased = claim.json()["job"]
        assert leased["job_id"] == job_id
        lease_headers = {"X-Asset-Lease": leased["lease_token"]}
        downloaded = await client.get(leased["input_url"], headers=lease_headers)
        assert downloaded.content == input_bytes
        assert hashlib.sha256(downloaded.content).hexdigest() == leased["input_sha256"]

        progress = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/progress",
            headers=lease_headers,
            json={"progress": 50},
        )
        assert progress.json() == {"cancel_requested": False}

        qa = json.dumps(
            {"schema_version": "1.0", "passed": True, "hard_failures": []}
        ).encode()
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/complete",
            headers=lease_headers,
            files={
                "blend": ("model_PBR_UV.blend", b"blend", "application/octet-stream"),
                "fbx": ("model_PBR_UV.fbx", b"fbx", "application/octet-stream"),
                "report": ("model_report.json", b"{}", "application/json"),
                "qa": ("model_QA.json", qa, "application/json"),
            },
        )
        assert completed.status_code == 200, completed.text

        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.status_code == 200
        assert status.json()["status"] == "SUCCEEDED"
        assert status.json()["progress"] == 100
        assert {artifact["kind"] for artifact in status.json()["artifacts"]} == {
            "blend",
            "fbx",
            "report",
            "qa",
        }
        qa_artifact = next(
            artifact for artifact in status.json()["artifacts"] if artifact["kind"] == "qa"
        )
        result = await client.get(
            qa_artifact["download_url"],
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert result.status_code == 200
        assert result.headers["X-Artifact-SHA256"] == hashlib.sha256(qa).hexdigest()


async def test_uv_asset_rejects_unsafe_filename_and_unknown_options(tmp_path: Path) -> None:
    async for _, client in prepared_asset_app(tmp_path):
        response = await client.post(
            "/api/v1/assets/uv/unwrap",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "bad-asset",
            },
            files={
                "asset": ("bad.exe", b"payload", "application/octet-stream"),
                "metadata": (
                    None,
                    json.dumps(
                        {
                            "external_asset_id": "bad-asset",
                            "options": {"resolution": 2048, "unknown": True},
                        }
                    ),
                ),
            },
        )
        assert response.status_code == 422
