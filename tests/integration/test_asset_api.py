import hashlib
import io
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


async def test_asset_api_auto_discovers_client_by_source_ip_without_api_key(
    tmp_path: Path,
) -> None:
    async for _, client in prepared_asset_app(tmp_path):
        first = await client.get("/api/v1/assets/capacity")
        assert first.status_code == 200, first.text

        # A repeated request from the same source IP must resolve to the same
        # automatically managed client instead of creating a conflict.
        repeated = await client.get("/api/v1/assets/capacity")
        assert repeated.status_code == 200, repeated.text


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
        assert created.json()["status_url"] == f"/api/v1/assets/jobs/{job_id}"
        assert created.json()["events_url"] == f"/api/v1/assets/jobs/{job_id}/events"
        assert created.json()["cancel_url"] == f"/api/v1/assets/jobs/{job_id}/cancel"
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
            json={
                "progress": 50,
                "stage": "UV_UNWRAPPING",
                "message": "正在展开 UV",
                "estimated_remaining_seconds": 90,
            },
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
        assert status.json()["stage"] == "SUCCEEDED"
        assert status.json()["timing"]["estimated_remaining_seconds"] == 0
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

        events = await client.get(
            f"/api/v1/assets/jobs/{job_id}/events",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert events.status_code == 200
        assert "event: asset-progress" in events.text
        assert '"stage": "UV_UNWRAPPING"' in events.text
        assert '"stage": "SUCCEEDED"' in events.text


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


async def register_asset_worker(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    response = await signed_post(
        client,
        settings,
        "/internal/v1/assets/workers/heartbeat",
        {
            "worker_id": "asset-worker-3090-a",
            "node_id": "worker-3090-a",
            "display_name": "3090-A Asset Worker",
            "hostname": "lilithgames1",
            "blender_version": "5.1.2",
            "skill_version": "asset-skills-2026.07.28",
            "cpu_count": 32,
            "max_concurrency": 4,
            "current_jobs": 0,
            "load_1m": 1.0,
            "available_memory_mb": 100000,
        },
    )
    assert response.status_code == 200, response.text


async def claim_asset_job(
    client: httpx.AsyncClient, settings: Settings
) -> dict[str, object]:
    response = await signed_post(
        client,
        settings,
        "/internal/v1/assets/jobs/claim",
        {
            "worker_id": "asset-worker-3090-a",
            "load_1m": 1.0,
            "available_memory_mb": 100000,
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    assert job is not None
    return job


async def test_uv_process_v2_preserves_stem_and_publishes_five_verified_artifacts(
    tmp_path: Path,
) -> None:
    metadata = {
        "external_asset_id": "asset:chair:uv:v2",
        "options": {
            "resolution": 2048,
            "padding_px": 10,
            "hard_edge_angle_degrees": 75,
            "hidden_axis": "y+",
            "texel_density_mode": "uniform",
            "qa_profile": "pbr-v1",
        },
    }
    async for settings, client in prepared_asset_app(tmp_path):
        created = await client.post(
            "/api/v1/assets/uv/process",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "asset:chair:uv:v2",
            },
            files={
                "asset": ("chair.source.fbx", b"fbx-v2", "application/octet-stream"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert created.json()["events_url"] == f"/api/v1/assets/jobs/{job_id}/events"
        await register_asset_worker(client, settings)
        job = await claim_asset_job(client, settings)
        assert job["job_id"] == job_id
        assert job["job_type"] == "UV_PROCESS_V2"
        lease_headers = {"X-Asset-Lease": str(job["lease_token"])}
        qa_payload = json.dumps(
            {"schema_version": "2", "passed": True, "hard_failures": []}
        ).encode()
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/uv-v2-complete",
            headers=lease_headers,
            files={
                "blend": (
                    "chair.source_PBR_UV.blend",
                    b"blend-v2",
                    "application/octet-stream",
                ),
                "fbx": (
                    "chair.source_PBR_UV.fbx",
                    b"fbx-result-v2",
                    "application/octet-stream",
                ),
                "report": (
                    "chair.source_PBR_UV_report.json",
                    json.dumps({"input": "chair.source.fbx"}).encode(),
                    "application/json",
                ),
                "qa": (
                    "chair.source_PBR_UV_QA.json",
                    qa_payload,
                    "application/json",
                ),
                "fbx_qa": (
                    "chair.source_PBR_UV_FBX_QA.json",
                    qa_payload,
                    "application/json",
                ),
            },
        )
        assert completed.status_code == 200, completed.text
        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "SUCCEEDED"
        assert body["progress"] == 100
        assert {item["filename"] for item in body["artifacts"]} == {
            "chair.source_PBR_UV.blend",
            "chair.source_PBR_UV.fbx",
            "chair.source_PBR_UV_report.json",
            "chair.source_PBR_UV_QA.json",
            "chair.source_PBR_UV_FBX_QA.json",
        }


async def test_retopology_audit_stops_at_review_gate_and_exposes_audit_artifacts(
    tmp_path: Path,
) -> None:
    metadata = {
        "external_asset_id": "asset:crate:retopo:audit:v1",
        "options": {
            "high_object": "crate_high",
            "reference_object": "crate_reference_low",
            "low_object": "crate_current_low",
            "require_closed": True,
        },
    }
    input_bytes = b"synthetic blend project"
    async for settings, client in prepared_asset_app(tmp_path):
        created = await client.post(
            "/api/v1/assets/retopology/audit",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "asset:crate:retopo:audit:v1",
            },
            files={
                "project": ("crate.blend", input_bytes, "application/octet-stream"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        await register_asset_worker(client, settings)
        job = await claim_asset_job(client, settings)
        assert job["job_id"] == job_id
        assert job["job_type"] == "RETOPOLOGY_AUDIT"
        assert job["options"] == metadata["options"]
        lease_headers = {"X-Asset-Lease": str(job["lease_token"])}
        audit = {
            "schema_version": 2,
            "audit_passed": True,
            "objects": {
                "high": {"object": "crate_high"},
                "reference": {"object": "crate_reference_low"},
                "low": {"object": "crate_current_low"},
            },
            "failures": [],
            "visual_review_required": ["front", "side", "top", "perspective"],
        }
        manifest = {
            "schema_version": "retopology_manifest.v1",
            "job_id": job_id,
            "job_type": "RETOPOLOGY_AUDIT",
            "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
            "visual_evidence": {
                "required": True,
                "views": ["front", "side", "top", "perspective"],
                "manual_review_required": False,
            },
        }
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/retopology-complete",
            headers=lease_headers,
            files={
                "audit": (
                    "retopology_audit.json",
                    json.dumps(audit).encode(),
                    "application/json",
                ),
                "manifest": (
                    "retopology_manifest.json",
                    json.dumps(manifest).encode(),
                    "application/json",
                ),
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json() == {
            "accepted": True,
            "status": "SUCCEEDED",
            "review_required": False,
            "audit_passed": True,
        }
        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.status_code == 200
        assert status.json()["status"] == "SUCCEEDED"
        assert status.json()["progress"] == 100
        assert status.json()["delivery_ready"] is True
        assert {item["kind"] for item in status.json()["artifacts"]} == {
            "audit",
            "manifest",
        }
        audit_artifact = next(
            item for item in status.json()["artifacts"] if item["kind"] == "audit"
        )
        downloaded = await client.get(
            audit_artifact["download_url"],
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert downloaded.status_code == 200
        assert downloaded.json()["audit_passed"] is True


async def test_retopology_audit_rejects_non_blend_input(tmp_path: Path) -> None:
    async for _, client in prepared_asset_app(tmp_path):
        response = await client.post(
            "/api/v1/assets/retopology/audit",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "asset:bad:retopo",
            },
            files={
                "project": ("crate.fbx", b"fbx", "application/octet-stream"),
                "metadata": (
                    None,
                    json.dumps(
                        {
                            "external_asset_id": "asset:bad:retopo",
                            "options": {
                                "high_object": "high",
                                "reference_object": "reference",
                                "low_object": "low",
                            },
                        }
                    ),
                ),
            },
        )
        assert response.status_code == 422


async def test_retopology_process_accepts_reference_views_and_publishes_review_set(
    tmp_path: Path,
) -> None:
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (32, 24), "purple").save(image, format="PNG")
    png = image.getvalue()
    metadata = {
        "external_asset_id": "asset:crate:retopo:process:v1",
        "options": {
            "high_object": "crate_high",
            "reference_object": "crate_reference_low",
            "low_object": "crate_current_low",
            "generated_low_object": "crate_generated_v001",
            "algorithm": "agent",
            "target_faces": 2400,
            "preserve_sharp": True,
            "preserve_boundary": True,
            "render_resolution": 256,
            "max_repair_rounds": 1,
            "require_closed": False,
        },
        "reference_views": [
            {"filename": "front.png", "view": "front", "label": "概念图正面"}
        ],
        "user_request": "保持箱体轮廓，扣件单独保留。",
    }
    async for settings, client in prepared_asset_app(tmp_path):
        created = await client.post(
            "/api/v1/assets/retopology/process",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "asset:crate:retopo:process:v1",
            },
            files=[
                ("project", ("crate.blend", b"real-blend-placeholder", "application/octet-stream")),
                ("metadata", (None, json.dumps(metadata), "application/json")),
                ("reference_images", ("front.png", png, "image/png")),
            ],
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        assert created.json()["timing"]["queue_position"] == 1
        await register_asset_worker(client, settings)
        job = await claim_asset_job(client, settings)
        assert job["job_id"] == job_id
        lease_headers = {"X-Asset-Lease": str(job["lease_token"])}
        objects = {
            "high": "crate_high",
            "reference": "crate_reference_low",
            "current": "crate_current_low",
            "generated": "crate_generated_v001",
        }
        audit = {
            "schema_version": 2,
            "audit_passed": True,
            "failures": [],
            "preservation": {"high": True, "reference": True},
        }
        agent_plan = {
            "recommended_algorithm": "quadriflow",
            "target_faces": 2400,
        }
        manifest = {
            "schema_version": "retopology_process_manifest.v1",
            "job_id": job_id,
            "job_type": "RETOPOLOGY_PROCESS_V1",
            "input_sha256": job["input_sha256"],
            "objects": objects,
            "source_preserved": True,
            "automatic_final_promotion_allowed": True,
            "visual_evidence": {
                "required": True,
                "views": ["front", "side", "top", "perspective"],
                "roles": ["high", "reference", "generated"],
                "manual_review_required": False,
            },
            "agent_plan": {
                "required": True,
                "recommended_algorithm": "quadriflow",
                "recommended_target_faces": 2400,
            },
        }
        files: dict[str, tuple[str, bytes, str]] = {
            "candidate_blend": ("retopology_candidate.blend", b"blend", "application/octet-stream"),
            "candidate_fbx": ("retopology_candidate.fbx", b"fbx", "application/octet-stream"),
            "process_report": (
                "retopology_process_report.json",
                json.dumps(
                    {
                        "schema_version": "retopology_process_report.v1",
                        "source_preserved": True,
                        "topology_goal_met": True,
                    }
                ).encode(),
                "application/json",
            ),
            "baseline_audit": ("retopology_baseline_audit.json", json.dumps(audit).encode(), "application/json"),
            "audit": ("retopology_final_audit.json", json.dumps(audit).encode(), "application/json"),
            "manifest": ("retopology_manifest.json", json.dumps(manifest).encode(), "application/json"),
            "comparison": ("retopology_comparison.png", png, "image/png"),
            "reference_images": ("reference_images.png", png, "image/png"),
            "agent_plan": ("retopology_agent_plan.json", json.dumps(agent_plan).encode(), "application/json"),
            "agent_prompt": ("retopology_agent_prompt.txt", b"planning prompt", "text/plain"),
            "agent_events": ("retopology_agent_events.jsonl", b"{}\n", "application/x-ndjson"),
        }
        for role in ("high", "reference", "generated"):
            for view in ("front", "side", "top", "perspective"):
                files[f"view_{role}_{view}"] = (
                    f"{role}_{view}.png",
                    png,
                    "image/png",
                )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/retopology-process-complete",
            headers=lease_headers,
            files=files,
        )
        assert completed.status_code == 200, completed.text
        body = completed.json()
        assert body["status"] == "SUCCEEDED"
        assert body["audit_passed"] is True
        assert body["review_required"] is False
        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.json()["stage"] == "SUCCEEDED"
        assert status.json()["delivery_ready"] is True
        assert len(status.json()["artifacts"]) == 23
