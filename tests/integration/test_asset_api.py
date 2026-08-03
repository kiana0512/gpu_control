import asyncio
import hashlib
import io
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import pytest
from gpu_control_asset_api import main as asset_api_main
from gpu_control_asset_api.main import as_utc, create_app
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from packages.gpu_control_core.models import (
    ApiClient,
    ApiKey,
    AssetJob,
    AssetWorker,
    Base,
    Job,
    JobBatch,
    Node,
)
from packages.gpu_control_core.scheduling import (
    OverflowGuard,
    QueueSnapshot,
    choose_node,
)
from packages.gpu_control_core.security import hash_api_secret, sign_agent_request
from packages.gpu_control_core.settings import Settings


async def test_asset_api_version_exposes_aligned_immutable_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GPU_CONTROL_BUILD_VERSION", "1.5.5")
    monkeypatch.setenv("GPU_CONTROL_BUILD_REVISION", "a" * 40)
    monkeypatch.setattr(
        "gpu_control_asset_api.main.importlib.metadata.version", lambda _: "1.5.5"
    )
    async for _, client in prepared_asset_app(tmp_path):
        response = await client.get("/api/v1/assets/version")
        assert response.status_code == 200
        assert response.json() == {
            "component": "asset-api",
            "package_version": "1.5.5",
            "build_version": "1.5.5",
            "source_revision": "a" * 40,
            "version_aligned": True,
            "provenance_complete": True,
        }


async def prepared_asset_app(
    tmp_path: Path,
    *,
    uv_qa_enforcement: Literal["strict", "advisory"] = "strict",
    retopology_qa_enforcement: Literal["strict", "advisory"] = "strict",
):
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
        uv_qa_enforcement=uv_qa_enforcement,
        retopology_qa_enforcement=retopology_qa_enforcement,
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
            db.add(
                Node(
                    id="worker-3090-b",
                    display_name="3090-B",
                    base_url="http://10.3.34.14:8188",
                    pool="PRIMARY",
                    mode="ACTIVE",
                    health="ONLINE",
                    current_jobs=0,
                    max_concurrency=1,
                    labels={},
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


async def test_asset_worker_signed_request_replay_is_rejected(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        path = "/internal/v1/assets/workers/heartbeat"
        payload = {
            "worker_id": "asset-worker-3090-a",
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
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        headers = worker_headers(settings, path, body)

        first = await client.post(path, content=body, headers=headers)
        assert first.status_code == 200, first.text
        replay = await client.post(path, content=body, headers=headers)
        assert replay.status_code == 409, replay.text
        assert replay.json()["detail"]["code"] == "ASSET_WORKER_REQUEST_REPLAY"

        concurrent_headers = worker_headers(settings, path, body)
        concurrent = await asyncio.gather(
            client.post(path, content=body, headers=concurrent_headers),
            client.post(path, content=body, headers=concurrent_headers),
        )
        assert sorted(response.status_code for response in concurrent) == [200, 409]


async def create_minimal_substance_job(
    client: httpx.AsyncClient,
    external_asset_id: str,
    *,
    api_key: str = "gpc_assetkey_secret",
) -> httpx.Response:
    return await client.post(
        "/api/v1/assets/bake/process",
        headers={
            "X-API-Key": api_key,
            "Idempotency-Key": external_asset_id,
        },
        files={
            "low_mesh": ("asset_low.fbx", b"low-fbx", "application/octet-stream"),
            "metadata": (
                None,
                json.dumps(
                    {
                        "external_asset_id": external_asset_id,
                        "options": {
                            "profile": "ao-self-v1",
                            "resolution": 256,
                            "texture_cache_mb": 8192,
                        },
                    }
                ),
            ),
        },
    )


async def register_substance_worker(
    client: httpx.AsyncClient,
    settings: Settings,
    worker_id: str,
    *,
    generation: str = "initial",
    current_jobs: int = 0,
    process_probe_status: str = "HEALTHY",
    active_baker_processes: int | None = 0,
    process_probe_checked_at: datetime | None = None,
    expected_status: str = "ONLINE",
) -> httpx.Response:
    checked_at = process_probe_checked_at or datetime.now(UTC)
    agent_instance_id = hashlib.sha256(
        f"{worker_id}:{generation}".encode()
    ).hexdigest()[:32]
    response = await signed_post(
        client,
        settings,
        "/internal/v1/assets/workers/heartbeat",
        {
            "worker_id": worker_id,
            "node_id": "worker-3090-b",
            "display_name": worker_id,
            "hostname": "LILITHGAMES3",
            "blender_version": "substance-15.1.0",
            "skill_version": "substance-baker-2026.08.03-v5",
            "cpu_count": 128,
            "max_concurrency": 1,
            "current_jobs": current_jobs,
            "load_1m": 0,
            "available_memory_mb": 100000,
            "agent_instance_id": agent_instance_id,
            "agent_started_at": (checked_at - timedelta(minutes=1)).isoformat(),
            "substance_process_probe_status": process_probe_status,
            "substance_process_probe_checked_at": checked_at.isoformat(),
            "substance_active_processes": active_baker_processes,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == expected_status
    return response


async def claim_substance_job(
    client: httpx.AsyncClient,
    settings: Settings,
    worker_id: str,
    *,
    generation: str = "initial",
) -> httpx.Response:
    agent_instance_id = hashlib.sha256(
        f"{worker_id}:{generation}".encode()
    ).hexdigest()[:32]
    return await signed_post(
        client,
        settings,
        "/internal/v1/assets/jobs/claim",
        {
            "worker_id": worker_id,
            "agent_instance_id": agent_instance_id,
            "load_1m": 0,
            "available_memory_mb": 100000,
        },
    )


def png_bytes(size: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (size, size), (128, 128, 255)).save(output, format="PNG")
    return output.getvalue()


async def test_legacy_substance_agent_cannot_claim_or_stop_comfyui(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        heartbeat = await signed_post(
            client,
            settings,
            "/internal/v1/assets/workers/heartbeat",
            {
                "worker_id": worker_id,
                "node_id": "worker-3090-b",
                "display_name": "legacy Windows Substance Baker",
                "hostname": "LILITHGAMES3",
                "blender_version": "substance-15.1.0",
                "skill_version": "substance-baker-2026.07.29-v2",
                "cpu_count": 128,
                "max_concurrency": 1,
                "current_jobs": 0,
                "load_1m": 0,
                "available_memory_mb": 100000,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["status"] == "DRAINING"
        created = await create_minimal_substance_job(client, "legacy-agent-blocked")
        assert created.status_code == 202, created.text
        claimed = await claim_substance_job(client, settings, worker_id)
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["job"] is None


async def test_substance_v4_without_host_process_evidence_is_drained(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        heartbeat = await signed_post(
            client,
            settings,
            "/internal/v1/assets/workers/heartbeat",
            {
                "worker_id": worker_id,
                "node_id": "worker-3090-b",
                "display_name": "incomplete v4 Windows Substance Baker",
                "hostname": "LILITHGAMES3",
                "blender_version": "substance-15.1.0",
                "skill_version": "substance-baker-2026.08.03-v5",
                "cpu_count": 128,
                "max_concurrency": 1,
                "current_jobs": 0,
                "load_1m": 0,
                "available_memory_mb": 100000,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["status"] == "DRAINING"

        created = await create_minimal_substance_job(
            client, "missing-host-process-evidence"
        )
        assert created.status_code == 202, created.text
        claimed = await claim_substance_job(client, settings, worker_id)
        assert claimed.json()["job"] is None


async def test_substance_claim_is_bound_to_heartbeat_agent_generation(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        await register_substance_worker(
            client, settings, worker_id, generation="current"
        )
        created = await create_minimal_substance_job(
            client, "claim-generation-binding"
        )
        assert created.status_code == 202, created.text

        stale_instance = await claim_substance_job(
            client, settings, worker_id, generation="previous"
        )
        assert stale_instance.status_code == 200, stale_instance.text
        assert stale_instance.json()["job"] is None

        current_instance = await claim_substance_job(
            client, settings, worker_id, generation="current"
        )
        assert current_instance.status_code == 200, current_instance.text
        assert current_instance.json()["job"]["job_id"] == created.json()["job_id"]
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            job = await db.get(AssetJob, created.json()["job_id"])
            assert job is not None
            assert job.worker_instance_id == hashlib.sha256(
                f"{worker_id}:current".encode()
            ).hexdigest()[:32]


async def test_restarted_substance_instance_cannot_claim_over_live_assignment(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        await register_substance_worker(
            client, settings, worker_id, generation="original"
        )
        first = await create_minimal_substance_job(client, "generation-live-first")
        first_claim = await claim_substance_job(
            client, settings, worker_id, generation="original"
        )
        assert first_claim.json()["job"]["job_id"] == first.json()["job_id"]
        second = await create_minimal_substance_job(
            client, "generation-live-second"
        )

        # The scheduled task restarts while the original instance still owns
        # a durable lease.  Its empty in-memory counter must not erase that
        # assignment even though the stable worker_id is unchanged.
        await register_substance_worker(
            client,
            settings,
            worker_id,
            generation="restarted",
            current_jobs=0,
            active_baker_processes=1,
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            worker = await db.get(AssetWorker, worker_id)
            assert worker is not None
            assert worker.current_jobs == 1
            # Even if a counter repair or concurrent writer later regresses
            # this field, claim has its own durable-job interlock.
            worker.current_jobs = 0
            await db.commit()

        duplicate = await claim_substance_job(
            client, settings, worker_id, generation="restarted"
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["job"] is None
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            first_job = await db.get(AssetJob, first.json()["job_id"])
            second_job = await db.get(AssetJob, second.json()["job_id"])
            assert first_job is not None and second_job is not None
            assert first_job.status == "CLAIMED"
            assert second_job.status == "QUEUED"


async def test_substance_baker_full_pbr_is_windows_only_fenced_and_atomically_published(
    tmp_path: Path,
) -> None:
    metadata = {
        "external_asset_id": "bake:chair:normal:g1",
        "options": {
            "profile": "li3d-pbr-full-v2",
            "resolution": 256,
            "texture_cache_mb": 32768,
        },
    }
    async for settings, client in prepared_asset_app(tmp_path):
        created = await client.post(
            "/api/v1/assets/bake/process",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "bake:chair:normal:g1",
            },
            files={
                "low_mesh": ("chair_low.fbx", b"low-fbx", "application/octet-stream"),
                "high_mesh": ("chair_high.fbx", b"high-fbx", "application/octet-stream"),
                "base_color_texture": ("chair_base.png", png_bytes(8), "image/png"),
                "roughness_texture": ("chair_roughness.png", png_bytes(8), "image/png"),
                "metallic_texture": ("chair_metallic.png", png_bytes(8), "image/png"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]

        # A Linux Blender worker must never claim native Windows Baker work.
        await signed_post(
            client,
            settings,
            "/internal/v1/assets/workers/heartbeat",
            {
                "worker_id": "asset-worker-3090-a",
                "node_id": "worker-3090-a",
                "display_name": "3090-A CPU Worker",
                "hostname": "lilithgames1",
                "blender_version": "5.1.2",
                "skill_version": "asset-skills-v3",
                "cpu_count": 32,
                "max_concurrency": 4,
                "current_jobs": 0,
                "load_1m": 0.1,
                "available_memory_mb": 100000,
            },
        )
        linux_claim = await signed_post(
            client,
            settings,
            "/internal/v1/assets/jobs/claim",
            {
                "worker_id": "asset-worker-3090-a",
                "load_1m": 0.1,
                "available_memory_mb": 100000,
            },
        )
        assert linux_claim.json()["job"] is None

        worker_id = "asset-worker-3090-b-windows"
        heartbeat = await register_substance_worker(
            client, settings, worker_id
        )
        assert heartbeat.json()["status"] == "ONLINE"
        claim = await claim_substance_job(client, settings, worker_id)
        leased = claim.json()["job"]
        assert leased["job_id"] == job_id

        # Claiming Baker work drains the same physical 3090 from ComfyUI.
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert node.labels["substance_bake_fence_job_ids"] == [job_id]

        baked = png_bytes(256)
        baked_sha = hashlib.sha256(baked).hexdigest()
        output_kinds = {
            "base_color",
            "roughness",
            "metallic",
            "ao",
            "normal_dx",
            "normal_gl",
            "world_normal",
            "curvature",
            "thickness",
            "position",
        }
        log = b"SAL,SoRa\n" + b"Bake finished successfully\n" * 10
        result = json.dumps(
            {
                "schema_version": 2,
                "job_id": job_id,
                "status": "SUCCEEDED",
                "profile": "li3d-pbr-full-v2",
                "tool": {
                    "version": "15.1.0",
                    "exe_sha256": "7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D",
                },
                "execution": {
                    "exit_code": None,
                    "exit_code_observed": False,
                    "success_marker_verified": True,
                    "command_count": 10,
                    "commands": [
                        {
                            "baker": "Synthetic.Test",
                            "output_name": f"asset_{kind}",
                            "exit_code_observed": False,
                            "exit_code": None,
                            "success_marker_present": True,
                        }
                        for kind in sorted(output_kinds)
                    ],
                    "comfyui_cache_policy": "no_explicit_eviction_process_preserved",
                    "comfyui_container_restarted": False,
                    "comfyui_process_continuity_verified": True,
                },
                "output_sha256": {kind: baked_sha for kind in output_kinds},
            }
        ).encode()

        def completion_files(
            result_bytes: bytes,
            baked_bytes: bytes,
            kinds: set[str],
            log_bytes: bytes,
        ) -> dict[str, tuple[str, bytes, str]]:
            return {
                **{
                    kind: (f"asset_{kind}.png", baked_bytes, "image/png")
                    for kind in kinds
                },
                "result": ("baker_result.json", result_bytes, "application/json"),
                "log": ("baker.log", log_bytes, "text/plain"),
            }

        valid_payload = json.loads(result)
        for invalid_execution in (
            {"exit_code": 0},
            {
                **valid_payload["execution"],
                "comfyui_process_continuity_verified": False,
            },
        ):
            invalid_payload = {**valid_payload, "execution": invalid_execution}
            rejected = await client.post(
                f"/internal/v1/assets/jobs/{job_id}/substance-complete",
                headers={"X-Asset-Lease": leased["lease_token"]},
                files=completion_files(
                    json.dumps(invalid_payload).encode(), baked, output_kinds, log
                ),
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["detail"]["code"] == "SUBSTANCE_RESULT_INVALID"

        completed = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/substance-complete",
            headers={"X-Asset-Lease": leased["lease_token"]},
            files=completion_files(result, baked, output_kinds, log),
        )
        assert completed.status_code == 200, completed.text
        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.json()["status"] == "SUCCEEDED"
        assert {item["kind"] for item in status.json()["artifacts"]} == (
            output_kinds | {"result", "log"}
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "ACTIVE"
            assert "substance_bake_fence_job_ids" not in node.labels


async def test_substance_completion_honors_cancel_before_artifact_publication(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        await register_substance_worker(client, settings, worker_id)
        created = await create_minimal_substance_job(
            client, "cancel-at-substance-publish-safe-point"
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        claimed = await claim_substance_job(client, settings, worker_id)
        assert claimed.json()["job"]["job_id"] == job_id
        lease = claimed.json()["job"]["lease_token"]

        cancelled = await client.post(
            f"/api/v1/assets/jobs/{job_id}/cancel",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLING"

        baked = png_bytes(256)
        baked_sha = hashlib.sha256(baked).hexdigest()
        result = json.dumps(
            {
                "schema_version": 1,
                "job_id": job_id,
                "status": "SUCCEEDED",
                "profile": "ao-self-v1",
                "tool": {
                    "version": "15.1.0",
                    "exe_sha256": (
                        "7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D"
                    ),
                },
                "execution": {
                    "exit_code": 0,
                    "comfyui_cache_policy": "no_explicit_eviction_process_preserved",
                    "comfyui_container_restarted": False,
                    "comfyui_process_continuity_verified": True,
                },
                "output_sha256": {"ao": baked_sha},
            }
        ).encode()
        completion = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/substance-complete",
            headers={"X-Asset-Lease": lease},
            files={
                "ao": ("asset_ao.png", baked, "image/png"),
                "result": ("baker_result.json", result, "application/json"),
                "log": (
                    "baker.log",
                    b"Bake finished successfully\n",
                    "text/plain",
                ),
            },
        )
        assert completion.status_code == 200, completion.text
        assert completion.json() == {
            "accepted": False,
            "status": "CANCELLED",
            "cancel_requested": True,
        }
        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.json()["status"] == "CANCELLED"
        assert status.json()["artifacts"] == []
        assert not (settings.asset_root / job_id / "output").exists()
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            job = await db.get(AssetJob, job_id)
            node = await db.get(Node, "worker-3090-b")
            assert job is not None and node is not None
            assert job.lease_token_hash is None
            assert job.lease_expires_at is None
            assert node.mode == "ACTIVE"
            assert "substance_bake_fence_job_ids" not in node.labels


async def test_production_substance_queue_reserves_next_gpu_turn_and_cancel_releases(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        await register_substance_worker(client, settings, worker_id)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.current_jobs = 1
            await db.commit()

        created = await create_minimal_substance_job(client, "production-bake-reservation")
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert node.labels["substance_bake_pending_reservation"]["job_ids"] == [
                job_id
            ]
            assert "substance_bake_fence_job_ids" not in node.labels

        blocked = await claim_substance_job(client, settings, worker_id)
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["job"] is None

        # Reproduce a poll after the original 60-second reservation expired
        # while a long ComfyUI frame was still running.  The claim response is
        # still empty, but reconciliation must durably renew the reservation;
        # otherwise Scheduler can reactivate 3090-B and starve the bake.
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            labels = dict(node.labels or {})
            pending = dict(labels["substance_bake_pending_reservation"])
            pending["expires_at"] = expired_at.isoformat()
            labels["substance_bake_pending_reservation"] = pending
            node.labels = labels
            await db.commit()

        renewed = await claim_substance_job(client, settings, worker_id)
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["job"] is None
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            pending = node.labels["substance_bake_pending_reservation"]
            assert pending["job_ids"] == [job_id]
            scheduler_now = datetime.now(UTC)
            assert datetime.fromisoformat(pending["expires_at"]) > scheduler_now

            # This is the exact selection path used by Scheduler.  Even
            # though the current ComfyUI frame is still running, the durable
            # reservation must win over the generic no-slot reason and keep
            # 3090-B unavailable for another GPU frame after it finishes.
            running_jobs = node.current_jobs
            node.current_jobs = 0
            chosen, excluded = choose_node(
                [node],
                QueueSnapshot(depth=1, oldest_wait_seconds=0),
                OverflowGuard(
                    queue_threshold=20,
                    wait_threshold_seconds=120,
                    max_gpu_util_percent=20,
                    min_free_vram_mb=20_000,
                    sentinel=tmp_path / "reserved",
                ),
                settings.node_heartbeat_timeout_seconds,
                scheduler_now,
            )
            node.current_jobs = running_jobs
            assert chosen is None
            assert excluded == {"worker-3090-b": "substance_reserved"}

            # The native Baker owns only the physical 3090-B GPU turn.  CPU
            # UV/retopology work remains in an independent worker pool and
            # must continue to claim while that GPU reservation is active.
            db.add(
                queued_asset_job(
                    "cpu-independent-during-substance-drain",
                    client_id="asset-client",
                    job_type="UV_PROCESS_V2",
                    created_at=scheduler_now,
                )
            )
            await db.commit()

        await register_asset_worker(client, settings)
        cpu_claim = await claim_asset_job(client, settings)
        assert cpu_claim["job_id"] == "cpu-independent-during-substance-drain"
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert node.labels["substance_bake_pending_reservation"]["job_ids"] == [
                job_id
            ]

        cancelled = await client.post(
            f"/api/v1/assets/jobs/{job_id}/cancel",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "ACTIVE"
            assert "substance_bake_pending_reservation" not in node.labels
            assert "substance_bake_drain_owner" not in node.labels


async def test_substance_pending_reservation_requires_fresh_available_baker(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        first = await create_minimal_substance_job(client, "no-baker-no-drain")
        assert first.status_code == 202, first.text
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "ACTIVE"
            assert "substance_bake_pending_reservation" not in node.labels

        await register_substance_worker(
            client, settings, "asset-worker-3090-b-windows-01"
        )
        for index in range(3):
            created = await create_minimal_substance_job(
                client, f"one-baker-capacity-{index}"
            )
            assert created.status_code == 202, created.text
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            pending = node.labels["substance_bake_pending_reservation"]
            assert len(pending["job_ids"]) == 1
            assert pending["worker_ids"] == [
                "asset-worker-3090-b-windows-01"
            ]

            worker = await db.get(
                AssetWorker,
                "asset-worker-3090-b-windows-01",
                with_for_update=True,
            )
            assert worker is not None
            worker.last_heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
            await db.commit()

        created = await create_minimal_substance_job(
            client,
            "stale-baker-releases-drain",
        )
        assert created.status_code == 202, created.text
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "ACTIVE"
            assert "substance_bake_pending_reservation" not in node.labels
            assert "substance_bake_drain_owner" not in node.labels


@pytest.mark.parametrize(
    ("mode", "existing_owner"),
    [
        ("DISABLED", None),
        ("RESERVED", None),
        ("DRAINING", "operator-maintenance"),
    ],
)
async def test_substance_reservation_never_steals_administrative_mode_ownership(
    tmp_path: Path,
    mode: str,
    existing_owner: str | None,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        await register_substance_worker(
            client, settings, "asset-worker-3090-b-windows-01"
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.mode = mode
            node.labels = {"maintenance_marker": "preserve-me"}
            if existing_owner is not None:
                node.labels = {
                    **node.labels,
                    "substance_bake_drain_owner": existing_owner,
                }
            await db.commit()

        created = await create_minimal_substance_job(
            client, f"mode-ownership-{mode.lower()}"
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == mode
            assert node.labels["maintenance_marker"] == "preserve-me"
            assert "substance_bake_pending_reservation" not in node.labels
            assert node.labels.get("substance_bake_drain_owner") == existing_owner

        cancelled = await client.post(
            f"/api/v1/assets/jobs/{job_id}/cancel",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert cancelled.status_code == 200, cancelled.text
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == mode
            assert node.labels["maintenance_marker"] == "preserve-me"
            assert node.labels.get("substance_bake_drain_owner") == existing_owner


async def test_test_substance_submission_never_installs_pending_gpu_reservation(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add(
                ApiClient(
                    id="load-test-client",
                    name="Load Test Client",
                    role="client",
                    client_kind="test",
                    max_queued=50,
                    max_running=10,
                )
            )
            db.add(
                ApiKey(
                    id=str(uuid.uuid4()),
                    client_id="load-test-client",
                    prefix="loadtest",
                    secret_hash=hash_api_secret("secret", settings.api_key_pepper),
                )
            )
            await db.commit()

        created = await create_minimal_substance_job(
            client,
            "test-bake-no-reservation",
            api_key="gpc_loadtest_secret",
        )
        assert created.status_code == 202, created.text
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "ACTIVE"
            assert "substance_bake_pending_reservation" not in node.labels


async def test_test_substance_claim_waits_for_production_gpu_jobs_and_batches(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add(
                ApiClient(
                    id="load-test-client",
                    name="Load Test Client",
                    role="client",
                    client_kind="test",
                    max_queued=50,
                    max_running=10,
                )
            )
            db.add(
                ApiKey(
                    id=str(uuid.uuid4()),
                    client_id="load-test-client",
                    prefix="loadtest",
                    secret_hash=hash_api_secret("secret", settings.api_key_pepper),
                )
            )
            db.add(
                Job(
                    id="production-gpu-job",
                    tenant_id="asset-client",
                    workflow_key="imageclip-rgba",
                    workflow_version="test",
                    status="QUEUED",
                    parameters={},
                    request_hash="production-gpu-job",
                    request_id="production-gpu-job",
                    trace_id="production-gpu-job",
                    job_dir=str(tmp_path / "production-gpu-job"),
                )
            )
            await db.commit()

        await register_substance_worker(
            client, settings, "asset-worker-3090-b-windows-01"
        )
        created = await create_minimal_substance_job(
            client,
            "test-bake-yields-to-gpu",
            api_key="gpc_loadtest_secret",
        )
        assert created.status_code == 202, created.text
        blocked_by_job = await claim_substance_job(
            client, settings, "asset-worker-3090-b-windows-01"
        )
        assert blocked_by_job.json()["job"] is None

        now = datetime.now(UTC)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            gpu_job = await db.get(Job, "production-gpu-job")
            assert gpu_job is not None
            gpu_job.status = "SUCCEEDED"
            db.add(
                JobBatch(
                    id="production-gpu-batch",
                    tenant_id="asset-client",
                    external_batch_id="production-gpu-batch",
                    workflow_key="imageclip-rgba",
                    workflow_version="test",
                    status="RUNNING",
                    parameters={},
                    request_hash="production-gpu-batch",
                    request_id="production-gpu-batch",
                    trace_id="production-gpu-batch",
                    batch_dir=str(tmp_path / "production-gpu-batch"),
                    manifest_sha256="a" * 64,
                    archive_sha256="b" * 64,
                    archive_size_bytes=1,
                    total_items=1,
                    started_at=now,
                )
            )
            await db.commit()

        blocked_by_batch = await claim_substance_job(
            client, settings, "asset-worker-3090-b-windows-01"
        )
        assert blocked_by_batch.json()["job"] is None

        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            batch = await db.get(JobBatch, "production-gpu-batch")
            assert batch is not None
            batch.status = "SUCCEEDED"
            batch.finished_at = datetime.now(UTC)
            await db.commit()
        claimed = await claim_substance_job(
            client, settings, "asset-worker-3090-b-windows-01"
        )
        assert claimed.json()["job"]["job_id"] == created.json()["job_id"]


async def test_substance_comfyui_continuity_failure_keeps_gpu_recovery_fence(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-01"
        await register_substance_worker(client, settings, worker_id)
        created = await create_minimal_substance_job(
            client, "comfyui-continuity-fail-closed"
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        claimed = await claim_substance_job(client, settings, worker_id)
        assert claimed.status_code == 200, claimed.text
        lease = claimed.json()["job"]["lease_token"]

        failed = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/fail",
            headers={"X-Asset-Lease": lease},
            json={
                "code": "SUBSTANCE_COMFYUI_CONTINUITY_FAILED",
                "message": "ComfyUI container identity changed during native bake",
                "retryable": False,
            },
        )
        assert failed.status_code == 200, failed.text
        assert failed.json() == {"accepted": True, "status": "FAILED"}

        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            job = await db.get(AssetJob, job_id)
            node = await db.get(Node, "worker-3090-b")
            assert job is not None and node is not None
            assert job.status == "FAILED"
            assert job.error_code == "SUBSTANCE_COMFYUI_CONTINUITY_FAILED"
            assert node.mode == "DRAINING"
            assert "substance_bake_fence_job_ids" not in node.labels
            recovery = node.labels["substance_bake_recovery_required"]
            assert len(recovery) == 1
            assert recovery[0]["job_id"] == job_id
            assert recovery[0]["worker_id"] == worker_id
            assert recovery[0]["lease_expired_at"]


async def test_expired_substance_lease_blocks_reclaim_until_explicit_health_evidence(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        created = await create_minimal_substance_job(client, "lease-expiry-release")
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        first_worker = "asset-worker-3090-b-windows-01"
        second_worker = "asset-worker-3090-b-windows-02"
        await register_substance_worker(client, settings, first_worker)
        claimed = await claim_substance_job(client, settings, first_worker)
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["job"]["job_id"] == job_id
        queued_after = await create_minimal_substance_job(
            client, "lease-expiry-follow-up"
        )
        assert queued_after.status_code == 202, queued_after.text
        follow_up_job_id = queued_after.json()["job_id"]

        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            job = await db.get(AssetJob, job_id)
            assert job is not None
            assert job.attempt_count < settings.asset_job_max_attempts
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        await register_substance_worker(client, settings, second_worker)
        swept = await claim_substance_job(client, settings, second_worker)
        assert swept.status_code == 200, swept.text
        assert swept.json()["job"] is None
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            job = await db.get(AssetJob, job_id)
            follow_up = await db.get(AssetJob, follow_up_job_id)
            node = await db.get(Node, "worker-3090-b")
            assert job is not None and follow_up is not None and node is not None
            assert job.status == "FAILED"
            assert job.stage == "RECOVERY_REQUIRED"
            assert job.error_code == "SUBSTANCE_LEASE_EXPIRED_RECOVERY_REQUIRED"
            assert node.mode == "DRAINING"
            assert "substance_bake_fence_job_ids" not in node.labels
            recovery_entries = node.labels["substance_bake_recovery_required"]
            assert len(recovery_entries) == 1
            assert recovery_entries[0]["job_id"] == job_id
            assert recovery_entries[0]["worker_id"] == first_worker
            assert recovery_entries[0]["worker_instance_id"] == hashlib.sha256(
                f"{first_worker}:initial".encode()
            ).hexdigest()[:32]
            assert recovery_entries[0]["lease_expired_at"]
            assert follow_up.status == "QUEUED"
            assert follow_up.attempt_count == 0

        still_blocked = await claim_substance_job(client, settings, second_worker)
        assert still_blocked.status_code == 200, still_blocked.text
        assert still_blocked.json()["job"] is None

        # A fresh ComfyUI heartbeat that arrived before the first host-wide
        # zero-process observation must not be reusable for recovery.
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            node.last_heartbeat_at = datetime.now(UTC)
            await db.commit()

        # The exact Worker now establishes the first zero-process barrier, but
        # the pre-existing ComfyUI heartbeat cannot release the fence.
        await register_substance_worker(client, settings, first_worker)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            recovery_entry = node.labels["substance_bake_recovery_required"][0]
            assert recovery_entry["idle_observed_at"]
            first_idle_observation = datetime.fromisoformat(
                recovery_entry["idle_observed_at"]
            )
            assert node.last_heartbeat_at is not None
            assert as_utc(node.last_heartbeat_at) < as_utc(first_idle_observation)

        # Repeated zero-process probes preserve the first barrier instead of
        # moving it forward, but still cannot unlock against the older node
        # observation.
        await register_substance_worker(client, settings, first_worker)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert "substance_bake_recovery_required" in node.labels
            recovery_entry = node.labels["substance_bake_recovery_required"][0]
            assert datetime.fromisoformat(
                recovery_entry["idle_observed_at"]
            ) == first_idle_observation

            # Scheduler publishes a new ComfyUI observation only after the
            # persisted zero-process barrier.
            node.last_heartbeat_at = datetime.now(UTC)
            assert as_utc(node.last_heartbeat_at) >= as_utc(first_idle_observation)
            await db.commit()

        # A current zero-process probe now brackets the later ComfyUI evidence
        # and releases the recovery interlock.
        await register_substance_worker(client, settings, first_worker)
        recovered = await claim_substance_job(client, settings, second_worker)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["job"]["job_id"] == follow_up_job_id
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert "substance_bake_recovery_required" not in node.labels
            assert node.labels["substance_bake_fence_job_ids"] == [
                follow_up_job_id
            ]


async def test_substance_recovery_restores_asset_owned_drain_to_active(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        first_worker = "asset-worker-3090-b-windows-01"
        second_worker = "asset-worker-3090-b-windows-02"
        created = await create_minimal_substance_job(
            client, "lease-expiry-restores-active"
        )
        job_id = created.json()["job_id"]
        await register_substance_worker(client, settings, first_worker)
        claimed = await claim_substance_job(client, settings, first_worker)
        assert claimed.json()["job"]["job_id"] == job_id

        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            job = await db.get(AssetJob, job_id)
            assert job is not None
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

        await register_substance_worker(client, settings, second_worker)
        swept = await claim_substance_job(client, settings, second_worker)
        assert swept.json()["job"] is None
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert node.labels["substance_bake_drain_owner"] == "asset-api"
            node.last_heartbeat_at = datetime.now(UTC)
            recovery_entry = node.labels["substance_bake_recovery_required"][0]
            lease_expired_at = datetime.fromisoformat(
                recovery_entry["lease_expired_at"]
            )
            await db.commit()

        # A restarted Agent has reset its in-memory current_jobs to zero, but
        # the host-wide process probe still sees the orphaned native Baker.
        # Recovery must remain fail-closed.
        await register_substance_worker(
            client,
            settings,
            first_worker,
            generation="restarted",
            active_baker_processes=1,
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            assert "substance_bake_recovery_required" in node.labels

        # A failed host process provider is unknown, never an asserted zero.
        await register_substance_worker(
            client,
            settings,
            first_worker,
            generation="restarted",
            process_probe_status="FAILED",
            active_baker_processes=None,
            expected_status="DRAINING",
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert "substance_bake_recovery_required" in node.labels

        # A zero-process observation made before the ambiguous lease expiry is
        # also insufficient, even if delivered by the current Agent instance.
        await register_substance_worker(
            client,
            settings,
            first_worker,
            generation="restarted",
            process_probe_checked_at=lease_expired_at - timedelta(seconds=1),
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert "substance_bake_recovery_required" in node.labels

        # The first fresh, healthy host-wide zero after expiry establishes the
        # recovery barrier.  The older ComfyUI observation cannot release it.
        await register_substance_worker(
            client,
            settings,
            first_worker,
            generation="restarted",
            active_baker_processes=0,
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "DRAINING"
            recovery_entry = node.labels["substance_bake_recovery_required"][0]
            first_idle_observation = datetime.fromisoformat(
                recovery_entry["idle_observed_at"]
            )
            node.last_heartbeat_at = datetime.now(UTC)
            assert as_utc(node.last_heartbeat_at) >= as_utc(first_idle_observation)
            await db.commit()

        # A later empty ComfyUI heartbeat followed by a current zero-process
        # probe completes the two-phase recovery and restores ACTIVE.
        await register_substance_worker(
            client,
            settings,
            first_worker,
            generation="restarted",
            active_baker_processes=0,
        )
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert node.mode == "ACTIVE"
            assert "substance_bake_recovery_required" not in node.labels
            assert "substance_bake_drain_owner" not in node.labels


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
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    codex_auth_status: str = "AUTHENTICATED",
    codex_probe_status: str = "HEALTHY",
    codex_last_checked_at: datetime | None = None,
    codex_error_code: str | None = None,
) -> None:
    checked_at = codex_last_checked_at or datetime.now(UTC)
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
            "codex_cli_version": "codex-cli 0.146.0-alpha.3.1",
            "codex_auth_status": codex_auth_status,
            "codex_probe_status": codex_probe_status,
            "codex_probe_latency_ms": 12000,
            "codex_last_checked_at": checked_at.isoformat(),
            "codex_last_success_at": (
                checked_at.isoformat() if codex_probe_status == "HEALTHY" else None
            ),
            "codex_error_code": codex_error_code,
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


def queued_asset_job(
    job_id: str,
    *,
    client_id: str,
    job_type: str,
    created_at: datetime,
) -> AssetJob:
    return AssetJob(
        id=job_id,
        client_id=client_id,
        external_asset_id=f"queue-priority:{job_id}",
        job_type=job_type,
        status="QUEUED",
        source_filename=f"{job_id}.fbx",
        input_path=f"/tmp/{job_id}.fbx",
        input_sha256="a" * 64,
        input_size_bytes=1,
        options={},
        request_hash=hashlib.sha256(job_id.encode()).hexdigest(),
        request_id=f"priority-{job_id}",
        created_at=created_at,
    )


async def test_substance_queue_timing_excludes_stale_online_workers(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        now = datetime.now(UTC)
        timeout = settings.asset_worker_heartbeat_timeout_seconds
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add_all(
                [
                    queued_asset_job(
                        f"substance-queue-{position}",
                        client_id="asset-client",
                        job_type="SUBSTANCE_BAKE_V1",
                        created_at=now - timedelta(seconds=4 - position),
                    )
                    for position in range(1, 4)
                ]
                + [
                    AssetWorker(
                        id="asset-worker-3090-b-windows-01",
                        display_name="Fresh Substance Worker",
                        node_id="worker-3090-b",
                        hostname="LILITHGAMES3",
                        status="ONLINE",
                        blender_version="substance-15.1.0",
                        skill_version="substance-baker-v1",
                        max_concurrency=1,
                        current_jobs=0,
                        last_heartbeat_at=now,
                    ),
                    AssetWorker(
                        id="asset-worker-3090-b-windows-02",
                        display_name="Stale Substance Worker",
                        node_id="worker-3090-b",
                        hostname="LILITHGAMES3",
                        status="ONLINE",
                        blender_version="substance-15.1.0",
                        skill_version="substance-baker-v1",
                        max_concurrency=1,
                        current_jobs=0,
                        last_heartbeat_at=now - timedelta(seconds=timeout + 1),
                    ),
                ]
            )
            await db.commit()

        stale_excluded = await client.get(
            "/api/v1/assets/jobs/substance-queue-3",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert stale_excluded.status_code == 200, stale_excluded.text
        assert stale_excluded.json()["timing"] == {
            "queue_position": 3,
            "estimated_start_seconds": 1200,
            "elapsed_seconds": 0,
            "estimated_remaining_seconds": None,
            "last_progress_at": None,
        }

        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            stale_worker = await db.get(AssetWorker, "asset-worker-3090-b-windows-02")
            assert stale_worker is not None
            stale_worker.last_heartbeat_at = datetime.now(UTC)
            await db.commit()

        fresh_included = await client.get(
            "/api/v1/assets/jobs/substance-queue-3",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert fresh_included.status_code == 200, fresh_included.text
        assert fresh_included.json()["timing"]["estimated_start_seconds"] == 600


async def test_cpu_asset_claim_prioritizes_production_and_keeps_pool_fifo(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        now = datetime.now(UTC)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add(
                ApiClient(
                    id="load-test-client",
                    name="Load Test Client",
                    role="client",
                    client_kind="test",
                    max_queued=50,
                    max_running=10,
                )
            )
            db.add_all(
                [
                    queued_asset_job(
                        "cpu-test-older",
                        client_id="load-test-client",
                        job_type="UV_PROCESS_V2",
                        created_at=now - timedelta(minutes=4),
                    ),
                    queued_asset_job(
                        "cpu-test-newer",
                        client_id="load-test-client",
                        job_type="RETOPOLOGY_AUDIT",
                        created_at=now - timedelta(minutes=3),
                    ),
                    queued_asset_job(
                        "cpu-production-older",
                        client_id="asset-client",
                        job_type="UV_PROCESS_V2",
                        created_at=now - timedelta(minutes=2),
                    ),
                    queued_asset_job(
                        "cpu-production-newer",
                        client_id="asset-client",
                        job_type="RETOPOLOGY_PROCESS_V1",
                        created_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            await db.commit()

        await register_asset_worker(client, settings)
        claimed = [str((await claim_asset_job(client, settings))["job_id"]) for _ in range(4)]
        assert claimed == [
            "cpu-production-older",
            "cpu-production-newer",
            "cpu-test-older",
            "cpu-test-newer",
        ]


async def test_codex_unhealthy_worker_skips_process_but_keeps_cpu_queue_moving(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        now = datetime.now(UTC)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add_all(
                [
                    queued_asset_job(
                        "codex-process-blocked",
                        client_id="asset-client",
                        job_type="RETOPOLOGY_PROCESS_V1",
                        created_at=now - timedelta(minutes=3),
                    ),
                    queued_asset_job(
                        "uv-still-runs",
                        client_id="asset-client",
                        job_type="UV_PROCESS_V2",
                        created_at=now - timedelta(minutes=2),
                    ),
                    queued_asset_job(
                        "audit-still-runs",
                        client_id="asset-client",
                        job_type="RETOPOLOGY_AUDIT",
                        created_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            await db.commit()

        await register_asset_worker(
            client,
            settings,
            codex_probe_status="FAILED",
            codex_error_code="AUTH_REFRESH_REUSED",
        )
        claimed = [str((await claim_asset_job(client, settings))["job_id"]) for _ in range(2)]
        assert claimed == ["uv-still-runs", "audit-still-runs"]

        no_more_eligible_work = await signed_post(
            client,
            settings,
            "/internal/v1/assets/jobs/claim",
            {
                "worker_id": "asset-worker-3090-a",
                "load_1m": 1.0,
                "available_memory_mb": 100000,
            },
        )
        assert no_more_eligible_work.json()["job"] is None
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            blocked = await db.get(AssetJob, "codex-process-blocked")
            assert blocked is not None
            assert blocked.status == "QUEUED"
            assert blocked.worker_id is None


async def test_codex_process_claim_requires_a_fresh_healthy_probe(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        now = datetime.now(UTC)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add(
                queued_asset_job(
                    "codex-process-freshness",
                    client_id="asset-client",
                    job_type="RETOPOLOGY_PROCESS_V1",
                    created_at=now,
                )
            )
            await db.commit()

        await register_asset_worker(
            client,
            settings,
            codex_last_checked_at=now
            - timedelta(seconds=settings.asset_codex_probe_max_age_seconds + 1),
        )
        stale = await signed_post(
            client,
            settings,
            "/internal/v1/assets/jobs/claim",
            {
                "worker_id": "asset-worker-3090-a",
                "load_1m": 1.0,
                "available_memory_mb": 100000,
            },
        )
        assert stale.json()["job"] is None

        await register_asset_worker(client, settings, codex_last_checked_at=datetime.now(UTC))
        claimed = await claim_asset_job(client, settings)
        assert claimed["job_id"] == "codex-process-freshness"


async def test_failed_heartbeat_preserves_historical_codex_success(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        successful_at = datetime.now(UTC) - timedelta(minutes=1)
        await register_asset_worker(
            client,
            settings,
            codex_last_checked_at=successful_at,
        )
        failed = await signed_post(
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
                "codex_cli_version": "codex-cli 0.146.0-alpha.3.1",
                "codex_auth_status": "AUTHENTICATED",
                "codex_probe_status": "FAILED",
                "codex_probe_latency_ms": 8000,
                "codex_last_checked_at": datetime.now(UTC).isoformat(),
                "codex_error_code": "AUTH_REFRESH_REUSED",
            },
        )
        assert failed.status_code == 200, failed.text

        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            worker = await db.get(AssetWorker, "asset-worker-3090-a")
            assert worker is not None
            assert worker.codex_probe_status == "FAILED"
            assert as_utc(worker.codex_last_success_at) == successful_at


async def test_substance_claim_prioritizes_production_and_keeps_pool_fifo(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        now = datetime.now(UTC)
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            db.add(
                ApiClient(
                    id="load-test-client",
                    name="Load Test Client",
                    role="client",
                    client_kind="test",
                    max_queued=50,
                    max_running=10,
                )
            )
            db.add_all(
                [
                    queued_asset_job(
                        "bake-test-older",
                        client_id="load-test-client",
                        job_type="SUBSTANCE_BAKE_V1",
                        created_at=now - timedelta(minutes=4),
                    ),
                    queued_asset_job(
                        "bake-test-newer",
                        client_id="load-test-client",
                        job_type="SUBSTANCE_BAKE_V1",
                        created_at=now - timedelta(minutes=3),
                    ),
                    queued_asset_job(
                        "bake-production-older",
                        client_id="asset-client",
                        job_type="SUBSTANCE_BAKE_V1",
                        created_at=now - timedelta(minutes=2),
                    ),
                    queued_asset_job(
                        "bake-production-newer",
                        client_id="asset-client",
                        job_type="SUBSTANCE_BAKE_V1",
                        created_at=now - timedelta(minutes=1),
                    ),
                    queued_asset_job(
                        "bake-test-fifth",
                        client_id="load-test-client",
                        job_type="SUBSTANCE_BAKE_V1",
                        created_at=now,
                    ),
                ]
            )
            await db.commit()

        claimed: list[str] = []
        for index in range(4):
            worker_id = f"asset-worker-3090-b-windows-{index:02d}"
            heartbeat = await register_substance_worker(
                client, settings, worker_id
            )
            assert heartbeat.json()["status"] == "ONLINE"
            response = await claim_substance_job(client, settings, worker_id)
            claimed.append(str(response.json()["job"]["job_id"]))

        assert claimed == [
            "bake-production-older",
            "bake-production-newer",
            "bake-test-older",
            "bake-test-newer",
        ]
        fifth_worker = "asset-worker-3090-b-windows-04"
        await register_substance_worker(client, settings, fifth_worker)
        fifth = await claim_substance_job(client, settings, fifth_worker)
        assert fifth.status_code == 200, fifth.text
        assert fifth.json()["job"] is None
        async with client._transport.app.state.db.session() as db:  # type: ignore[attr-defined]
            node = await db.get(Node, "worker-3090-b")
            assert node is not None
            assert len(node.labels["substance_bake_fence_job_ids"]) == 4


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


async def create_and_claim_uv_process_v2(
    client: httpx.AsyncClient,
    settings: Settings,
    external_asset_id: str,
) -> dict[str, object]:
    metadata = {
        "external_asset_id": external_asset_id,
        "options": {
            "resolution": 2048,
            "padding_px": 10,
            "hard_edge_angle_degrees": 75,
            "hidden_axis": "y+",
            "texel_density_mode": "uniform",
            "qa_profile": "pbr-v1",
        },
    }
    created = await client.post(
        "/api/v1/assets/uv/process",
        headers={
            "X-API-Key": "gpc_assetkey_secret",
            "Idempotency-Key": external_asset_id,
        },
        files={
            "asset": ("chair.source.fbx", b"fbx-v2", "application/octet-stream"),
            "metadata": (None, json.dumps(metadata)),
        },
    )
    assert created.status_code == 202, created.text
    await register_asset_worker(client, settings)
    job = await claim_asset_job(client, settings)
    assert job["job_id"] == created.json()["job_id"]
    assert job["job_type"] == "UV_PROCESS_V2"
    return job


def uv_process_v2_completion_files(
    job: dict[str, object],
    *,
    blend_failures: list[object] | None = None,
    fbx_failures: list[object] | None = None,
    report_input: str = "chair.source.fbx",
    empty_artifact: str | None = None,
) -> dict[str, tuple[str, bytes, str]]:
    stem = Path(str(job["source_filename"])).stem
    blend_failures = blend_failures or []
    fbx_failures = fbx_failures or []
    blend_qa = {
        "schema_version": "2",
        "passed": not blend_failures,
        "hard_failures": blend_failures,
    }
    fbx_qa = {
        "schema_version": "2",
        "passed": not fbx_failures,
        "hard_failures": fbx_failures,
    }
    payloads: dict[str, tuple[str, bytes, str]] = {
        "blend": (
            f"{stem}_PBR_UV.blend",
            b"blend-v2",
            "application/octet-stream",
        ),
        "fbx": (
            f"{stem}_PBR_UV.fbx",
            b"fbx-result-v2",
            "application/octet-stream",
        ),
        "report": (
            f"{stem}_PBR_UV_report.json",
            json.dumps({"input": report_input}).encode(),
            "application/json",
        ),
        "qa": (
            f"{stem}_PBR_UV_QA.json",
            json.dumps(blend_qa).encode(),
            "application/json",
        ),
        "fbx_qa": (
            f"{stem}_PBR_UV_FBX_QA.json",
            json.dumps(fbx_qa).encode(),
            "application/json",
        ),
    }
    if empty_artifact is not None:
        filename, _, content_type = payloads[empty_artifact]
        payloads[empty_artifact] = (filename, b"", content_type)
    return payloads


async def test_uv_process_v2_advisory_delivers_five_artifacts_with_warning(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(
        tmp_path, uv_qa_enforcement="advisory"
    ):
        job = await create_and_claim_uv_process_v2(
            client, settings, "asset:chair:uv:v2:advisory"
        )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/uv-v2-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=uv_process_v2_completion_files(
                job,
                blend_failures=["overlap_triangle_pairs=2"],
                fbx_failures=[{"code": "FLIPPED_UV", "faces": 1}],
            ),
        )
        assert completed.status_code == 200, completed.text
        assert completed.json() == {
            "accepted": True,
            "status": "SUCCEEDED",
            "quality_gate_passed": False,
            "qa_enforcement": "advisory",
            "delivered_with_warnings": True,
        }

        status = await client.get(
            f"/api/v1/assets/jobs/{job['job_id']}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        payload = status.json()
        assert payload["status"] == "SUCCEEDED"
        assert payload["delivery_ready"] is True
        assert payload["artifacts_role"] == "delivery"
        assert payload["error"] is None
        assert {artifact["kind"] for artifact in payload["artifacts"]} == {
            "blend",
            "fbx",
            "report",
            "qa",
            "fbx_qa",
        }
        warning = payload["options"]["qa_warning"]
        assert warning == {
            "code": "UV_QUALITY_GATE_WARNING",
            "enforcement": "advisory",
            "failed_qa": ["blend", "fbx_readback"],
            "failures": [
                "blend: overlap_triangle_pairs=2",
                'fbx_readback: {"code": "FLIPPED_UV", "faces": 1}',
            ],
        }
        events = await client.get(
            f"/api/v1/assets/jobs/{job['job_id']}/events",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert events.status_code == 200
        assert "asset.succeeded_with_warnings" in events.text
        assert "UV_QUALITY_GATE_WARNING" in events.text
        assert "overlap_triangle_pairs=2" in events.text


async def test_uv_process_v2_strict_still_rejects_geometry_qa_failure(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path):
        job = await create_and_claim_uv_process_v2(
            client, settings, "asset:chair:uv:v2:strict"
        )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/uv-v2-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=uv_process_v2_completion_files(
                job, blend_failures=["overlap_triangle_pairs=2"]
            ),
        )
        assert completed.status_code == 422, completed.text
        assert completed.json()["detail"] == {
            "code": "ASSET_QA_FAILED",
            "qa": "blend",
        }
        assert list((settings.asset_root / str(job["job_id"])).glob(".outputs-*")) == []


@pytest.mark.parametrize(
    ("completion_overrides", "expected_detail"),
    [
        (
            {"report_input": "different-source.fbx"},
            {"code": "ASSET_REPORT_INPUT_MISMATCH"},
        ),
        (
            {"empty_artifact": "blend"},
            {"code": "ASSET_ARTIFACT_EMPTY", "kind": "blend"},
        ),
    ],
)
async def test_uv_process_v2_advisory_keeps_integrity_failures_hard(
    tmp_path: Path,
    completion_overrides: dict[str, str],
    expected_detail: dict[str, str],
) -> None:
    async for settings, client in prepared_asset_app(
        tmp_path, uv_qa_enforcement="advisory"
    ):
        job = await create_and_claim_uv_process_v2(
            client,
            settings,
            f"asset:chair:uv:v2:integrity:{next(iter(completion_overrides))}",
        )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/uv-v2-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=uv_process_v2_completion_files(job, **completion_overrides),
        )
        assert completed.status_code == 422, completed.text
        assert completed.json()["detail"] == expected_detail
        assert list((settings.asset_root / str(job["job_id"])).glob(".outputs-*")) == []


async def test_uv_process_v2_rejects_non_object_qa_json_as_422(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(
        tmp_path, uv_qa_enforcement="advisory"
    ):
        job = await create_and_claim_uv_process_v2(
            client, settings, "asset:chair:uv:v2:scalar-qa"
        )
        files = uv_process_v2_completion_files(job)
        qa_filename, _, qa_content_type = files["qa"]
        files["qa"] = (qa_filename, b"[]", qa_content_type)
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/uv-v2-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=files,
        )
        assert completed.status_code == 422, completed.text
        assert completed.json()["detail"] == {"code": "ASSET_QA_INVALID"}
        assert list((settings.asset_root / str(job["job_id"])).glob(".outputs-*")) == []


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
            "objects": {
                "low": {
                    "topology": {
                        "faces": 2400,
                        "triangles": 0,
                        "quads": 2400,
                        "ngons": 0,
                        "nonmanifold_edges": 0,
                        "loose_edges": 0,
                        "loose_vertices": 0,
                        "duplicate_vertices": 0,
                        "duplicate_faces": 0,
                        "zero_area_faces": 0,
                        "inconsistent_orientation_edges": 0,
                    }
                }
            },
            "comparison": {
                "dimension_relative_error": [0.01, 0.01, 0.01],
                "normalized_center_offset": 0.002,
            },
        }
        agent_plan = {
            "recommended_algorithm": "quadriflow",
            "target_faces": 2400,
        }
        quality_gate = {
            "schema_version": "retopology_quality_gate.v2",
            "passed": True,
            "failures": [],
            "limits": {},
            "measurements": {},
        }
        manifest = {
            "schema_version": "retopology_process_manifest.v1",
            "job_id": job_id,
            "job_type": "RETOPOLOGY_PROCESS_V1",
            "input_sha256": job["input_sha256"],
            "objects": objects,
            "source_preserved": True,
            "automatic_final_promotion_allowed": True,
            "quality_gate": quality_gate,
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
                        "quality_gate": quality_gate,
                        "source_topology": {
                            "high": {"face_components": 1},
                            "reference": {"face_components": 1},
                            "current": {"face_components": 1}
                        },
                        "candidate_topology": {
                            "faces": 2400,
                            "triangles": 0,
                            "quads": 2400,
                            "ngons": 0,
                            "quad_ratio": 1.0,
                            "face_components": 1,
                        },
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
        assert {
            artifact["kind"]: artifact["filename"]
            for artifact in status.json()["artifacts"]
            if artifact["kind"] in {"blend", "fbx"}
        } == {
            "blend": "retopology_final.blend",
            "fbx": "retopology_final.fbx",
        }


def retopology_process_metadata(external_asset_id: str) -> dict[str, object]:
    return {
        "external_asset_id": external_asset_id,
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
        "reference_views": [],
        "user_request": "保持箱体轮廓，先生成可用候选。",
    }


def retopology_process_completion_files(
    job: dict[str, object],
    png: bytes,
    *,
    quality_passed: bool,
    source_preserved: bool = True,
) -> dict[str, tuple[str, bytes, str]]:
    job_id = str(job["job_id"])
    objects = {
        "high": "crate_high",
        "reference": "crate_reference_low",
        "current": "crate_current_low",
        "generated": "crate_generated_v001",
    }
    failures = [] if quality_passed else ["low contains N-gons"]
    audit = {
        "schema_version": 2,
        "audit_passed": quality_passed and source_preserved,
        "failures": failures,
        "preservation": {
            "high": source_preserved,
            "reference": source_preserved,
        },
        "objects": {
            "low": {
                "topology": {
                    "faces": 2400,
                    "triangles": 0,
                    "quads": 2399 if not quality_passed else 2400,
                    "ngons": 1 if not quality_passed else 0,
                    "nonmanifold_edges": 0,
                    "loose_edges": 0,
                    "loose_vertices": 0,
                    "duplicate_vertices": 0,
                    "duplicate_faces": 0,
                    "zero_area_faces": 0,
                    "inconsistent_orientation_edges": 0,
                }
            }
        },
        "comparison": {
            "dimension_relative_error": [0.01, 0.01, 0.01],
            "normalized_center_offset": 0.002,
        },
    }
    quality_gate = {
        "schema_version": "retopology_quality_gate.v2",
        "passed": quality_passed,
        "failures": [] if quality_passed else ["SIGNED_AUDIT_FAILED", "NGONS=1"],
        "limits": {},
        "measurements": {},
    }
    agent_plan = {"recommended_algorithm": "quadriflow", "target_faces": 2400}
    manifest = {
        "schema_version": "retopology_process_manifest.v1",
        "job_id": job_id,
        "job_type": "RETOPOLOGY_PROCESS_V1",
        "input_sha256": job["input_sha256"],
        "objects": objects,
        "source_preserved": source_preserved,
        "automatic_final_promotion_allowed": quality_passed and source_preserved,
        "quality_gate": quality_gate,
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
        "candidate_blend": (
            "retopology_candidate.blend",
            b"blend",
            "application/octet-stream",
        ),
        "candidate_fbx": (
            "retopology_candidate.fbx",
            b"fbx",
            "application/octet-stream",
        ),
        "process_report": (
            "retopology_process_report.json",
            json.dumps(
                {
                    "schema_version": "retopology_process_report.v1",
                    "source_preserved": source_preserved,
                    "topology_goal_met": quality_passed,
                    "quality_gate": quality_gate,
                    "source_topology": {
                        "high": {"face_components": 1},
                        "reference": {"face_components": 1},
                        "current": {"face_components": 1},
                    },
                    "candidate_topology": {
                        "faces": 2400,
                        "triangles": 0,
                        "quads": 2399 if not quality_passed else 2400,
                        "ngons": 1 if not quality_passed else 0,
                        "quad_ratio": 0.999583 if not quality_passed else 1.0,
                        "face_components": 1,
                    },
                }
            ).encode(),
            "application/json",
        ),
        "baseline_audit": (
            "retopology_baseline_audit.json",
            json.dumps({**audit, "audit_passed": True, "failures": []}).encode(),
            "application/json",
        ),
        "audit": (
            "retopology_final_audit.json",
            json.dumps(audit).encode(),
            "application/json",
        ),
        "manifest": (
            "retopology_manifest.json",
            json.dumps(manifest).encode(),
            "application/json",
        ),
        "comparison": ("retopology_comparison.png", png, "image/png"),
        "agent_plan": (
            "retopology_agent_plan.json",
            json.dumps(agent_plan).encode(),
            "application/json",
        ),
        "agent_prompt": (
            "retopology_agent_prompt.txt",
            b"planning prompt",
            "text/plain",
        ),
        "agent_events": (
            "retopology_agent_events.jsonl",
            b"{}\n",
            "application/x-ndjson",
        ),
    }
    for role in ("high", "reference", "generated"):
        for view in ("front", "side", "top", "perspective"):
            files[f"view_{role}_{view}"] = (
                f"{role}_{view}.png",
                png,
                "image/png",
            )
    return files


async def create_and_claim_retopology_process(
    client: httpx.AsyncClient,
    settings: Settings,
    external_asset_id: str,
) -> dict[str, object]:
    created = await client.post(
        "/api/v1/assets/retopology/process",
        headers={
            "X-API-Key": "gpc_assetkey_secret",
            "Idempotency-Key": external_asset_id,
        },
        files={
            "project": (
                "crate.blend",
                b"real-blend-placeholder",
                "application/octet-stream",
            ),
            "metadata": (
                None,
                json.dumps(retopology_process_metadata(external_asset_id)),
                "application/json",
            ),
        },
    )
    assert created.status_code == 202, created.text
    await register_asset_worker(client, settings)
    claimed = await claim_asset_job(client, settings)
    assert claimed["job_id"] == created.json()["job_id"]
    return claimed


def pause_first_completion_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[asyncio.Event, asyncio.Event]:
    """Pause after private staging proves completion no longer holds DB locks."""

    staged = asyncio.Event()
    resume = asyncio.Event()
    original = asset_api_main.persist_completion_upload
    first = True

    async def paused(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal first
        result = await original(*args, **kwargs)
        if first:
            first = False
            staged.set()
            await resume.wait()
        return result

    monkeypatch.setattr(asset_api_main, "persist_completion_upload", paused)
    return staged, resume


async def assert_cancelled_without_publication(
    client: httpx.AsyncClient,
    settings: Settings,
    job_id: str,
) -> None:
    status = await client.get(
        f"/api/v1/assets/jobs/{job_id}",
        headers={"X-API-Key": "gpc_assetkey_secret"},
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "CANCELLED"
    assert status.json()["artifacts"] == []
    assert not (settings.asset_root / job_id / "output").exists()


async def test_uv_completion_upload_does_not_block_cancel_or_publish_after_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged, resume = pause_first_completion_upload(monkeypatch)
    metadata = {
        "external_asset_id": "asset:uv:completion-cancel-race",
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
            "/api/v1/assets/uv/unwrap",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "asset:uv:completion-cancel-race",
            },
            files={
                "asset": ("race.fbx", b"fbx", "application/octet-stream"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert created.status_code == 202, created.text
        await register_asset_worker(client, settings)
        job = await claim_asset_job(client, settings)
        job_id = str(job["job_id"])
        completion = asyncio.create_task(
            client.post(
                f"/internal/v1/assets/jobs/{job_id}/complete",
                headers={"X-Asset-Lease": str(job["lease_token"])},
                files={
                    "blend": ("model_PBR_UV.blend", b"blend", "application/octet-stream"),
                    "fbx": ("model_PBR_UV.fbx", b"fbx", "application/octet-stream"),
                    "report": ("model_report.json", b"{}", "application/json"),
                    "qa": (
                        "model_QA.json",
                        json.dumps({"hard_failures": []}).encode(),
                        "application/json",
                    ),
                },
            )
        )
        await asyncio.wait_for(staged.wait(), 2)
        cancelled = await asyncio.wait_for(
            client.post(
                f"/api/v1/assets/jobs/{job_id}/cancel",
                headers={"X-API-Key": "gpc_assetkey_secret"},
            ),
            2,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLING"
        resume.set()
        completed = await completion
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "CANCELLED"
        await assert_cancelled_without_publication(client, settings, job_id)


async def test_completion_commit_failure_leaves_no_blocking_final_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "external_asset_id": "asset:uv:completion-commit-retry",
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
            "/api/v1/assets/uv/unwrap",
            headers={
                "X-API-Key": "gpc_assetkey_secret",
                "Idempotency-Key": "asset:uv:completion-commit-retry",
            },
            files={
                "asset": ("retry.fbx", b"fbx", "application/octet-stream"),
                "metadata": (None, json.dumps(metadata)),
            },
        )
        assert created.status_code == 202, created.text
        await register_asset_worker(client, settings)
        job = await claim_asset_job(client, settings)
        job_id = str(job["job_id"])
        lease_headers = {"X-Asset-Lease": str(job["lease_token"])}
        files = {
            "blend": ("model_PBR_UV.blend", b"blend", "application/octet-stream"),
            "fbx": ("model_PBR_UV.fbx", b"fbx", "application/octet-stream"),
            "report": ("model_report.json", b"{}", "application/json"),
            "qa": (
                "model_QA.json",
                json.dumps({"hard_failures": []}).encode(),
                "application/json",
            ),
        }
        original_commit = AsyncSession.commit
        commit_calls = 0

        async def fail_final_commit(
            session: AsyncSession,
            original=original_commit,
        ) -> None:
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 2:
                raise RuntimeError("injected final commit failure")
            await original(session)

        monkeypatch.setattr(AsyncSession, "commit", fail_final_commit)
        with pytest.raises(RuntimeError, match="injected final commit failure"):
            await client.post(
                f"/internal/v1/assets/jobs/{job_id}/complete",
                headers=lease_headers,
                files=files,
            )
        monkeypatch.setattr(AsyncSession, "commit", original_commit)
        assert not (settings.asset_root / job_id / "output").exists()
        assert list((settings.asset_root / job_id).glob(".outputs-*")) == []

        retried = await client.post(
            f"/internal/v1/assets/jobs/{job_id}/complete",
            headers=lease_headers,
            files=files,
        )
        assert retried.status_code == 200, retried.text
        status = await client.get(
            f"/api/v1/assets/jobs/{job_id}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.json()["status"] == "SUCCEEDED"
        assert {artifact["kind"] for artifact in status.json()["artifacts"]} == {
            "blend",
            "fbx",
            "report",
            "qa",
        }
        assert len(list((settings.asset_root / job_id).glob(".outputs-*"))) == 1


async def test_retopology_advisory_completion_cancel_wins_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged, resume = pause_first_completion_upload(monkeypatch)
    async for settings, client in prepared_asset_app(
        tmp_path, retopology_qa_enforcement="advisory"
    ):
        job = await create_and_claim_retopology_process(
            client, settings, "asset:retopo:completion-cancel-race"
        )
        job_id = str(job["job_id"])
        completion = asyncio.create_task(
            client.post(
                f"/internal/v1/assets/jobs/{job_id}/retopology-process-complete",
                headers={"X-Asset-Lease": str(job["lease_token"])},
                files=retopology_process_completion_files(
                    job, png_bytes(16), quality_passed=False
                ),
            )
        )
        await asyncio.wait_for(staged.wait(), 2)
        cancelled = await asyncio.wait_for(
            client.post(
                f"/api/v1/assets/jobs/{job_id}/cancel",
                headers={"X-API-Key": "gpc_assetkey_secret"},
            ),
            2,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLING"
        resume.set()
        completed = await completion
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "CANCELLED"
        await assert_cancelled_without_publication(client, settings, job_id)


async def test_substance_completion_staging_does_not_hold_gpu_node_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged, resume = pause_first_completion_upload(monkeypatch)
    async for settings, client in prepared_asset_app(tmp_path):
        worker_id = "asset-worker-3090-b-windows-race"
        await register_substance_worker(client, settings, worker_id)
        created = await create_minimal_substance_job(
            client, "substance-completion-cancel-race"
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["job_id"]
        claimed = await claim_substance_job(client, settings, worker_id)
        lease = claimed.json()["job"]["lease_token"]
        baked = png_bytes(256)
        baked_sha = hashlib.sha256(baked).hexdigest()
        result = json.dumps(
            {
                "schema_version": 1,
                "job_id": job_id,
                "status": "SUCCEEDED",
                "profile": "ao-self-v1",
                "tool": {
                    "version": "15.1.0",
                    "exe_sha256": (
                        "7B920FC6EE6005FAAB072C9280B1772F03D694FF04AA91C5A4DB516F7C9FEC6D"
                    ),
                },
                "execution": {
                    "exit_code": 0,
                    "comfyui_cache_policy": "no_explicit_eviction_process_preserved",
                    "comfyui_container_restarted": False,
                    "comfyui_process_continuity_verified": True,
                },
                "output_sha256": {"ao": baked_sha},
            }
        ).encode()
        completion = asyncio.create_task(
            client.post(
                f"/internal/v1/assets/jobs/{job_id}/substance-complete",
                headers={"X-Asset-Lease": lease},
                files={
                    "ao": ("asset_ao.png", baked, "image/png"),
                    "result": ("baker_result.json", result, "application/json"),
                    "log": (
                        "baker.log",
                        b"Bake finished successfully\n",
                        "text/plain",
                    ),
                },
            )
        )
        await asyncio.wait_for(staged.wait(), 2)
        cancelled = await asyncio.wait_for(
            client.post(
                f"/api/v1/assets/jobs/{job_id}/cancel",
                headers={"X-API-Key": "gpc_assetkey_secret"},
            ),
            2,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLING"
        resume.set()
        completed = await completion
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "CANCELLED"
        await assert_cancelled_without_publication(client, settings, job_id)


async def test_retopology_strict_qa_failure_keeps_diagnostics_downloadable(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(tmp_path, retopology_qa_enforcement="strict"):
        job = await create_and_claim_retopology_process(
            client, settings, "asset:crate:retopo:strict-failure"
        )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/retopology-process-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=retopology_process_completion_files(job, png_bytes(16), quality_passed=False),
        )
        assert completed.status_code == 200, completed.text
        assert completed.json() == {
            "accepted": True,
            "status": "FAILED",
            "review_required": False,
            "audit_passed": False,
            "quality_gate_passed": False,
            "qa_enforcement": "strict",
            "delivered_with_warnings": False,
        }
        status = await client.get(
            f"/api/v1/assets/jobs/{job['job_id']}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        payload = status.json()
        assert payload["error"]["code"] == "RETOPOLOGY_QUALITY_GATE_FAILED"
        assert payload["delivery_ready"] is False
        assert payload["artifacts_role"] == "diagnostic"
        assert len(payload["artifacts"]) == 22
        assert {artifact["kind"] for artifact in payload["artifacts"]} >= {
            "candidate_blend",
            "candidate_fbx",
        }
        artifact = payload["artifacts"][0]
        downloaded = await client.get(
            artifact["download_url"],
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert downloaded.status_code == 200
        assert downloaded.headers["X-Artifact-SHA256"] == artifact["sha256"]


async def test_retopology_advisory_qa_delivers_with_persisted_warning(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(
        tmp_path, retopology_qa_enforcement="advisory"
    ):
        job = await create_and_claim_retopology_process(
            client, settings, "asset:crate:retopo:advisory-warning"
        )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/retopology-process-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=retopology_process_completion_files(job, png_bytes(16), quality_passed=False),
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "SUCCEEDED"
        assert completed.json()["quality_gate_passed"] is False
        assert completed.json()["qa_enforcement"] == "advisory"
        assert completed.json()["delivered_with_warnings"] is True

        status = await client.get(
            f"/api/v1/assets/jobs/{job['job_id']}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        payload = status.json()
        assert payload["status"] == "SUCCEEDED"
        assert payload["delivery_ready"] is True
        assert payload["artifacts_role"] == "delivery"
        assert payload["error"] is None
        assert len(payload["artifacts"]) == 22
        final_models = {
            artifact["kind"]: artifact
            for artifact in payload["artifacts"]
            if artifact["kind"] in {"blend", "fbx"}
        }
        assert {
            kind: artifact["filename"] for kind, artifact in final_models.items()
        } == {
            "blend": "retopology_final.blend",
            "fbx": "retopology_final.fbx",
        }
        assert not {
            artifact["kind"]
            for artifact in payload["artifacts"]
            if artifact["kind"] in {"candidate_blend", "candidate_fbx"}
        }
        for artifact in final_models.values():
            downloaded = await client.get(
                artifact["download_url"],
                headers={"X-API-Key": "gpc_assetkey_secret"},
            )
            assert downloaded.status_code == 200
            assert downloaded.headers["X-Artifact-SHA256"] == artifact["sha256"]
        warning = payload["options"]["qa_warning"]
        assert warning["code"] == "RETOPOLOGY_QUALITY_GATE_WARNING"
        assert warning["enforcement"] == "advisory"
        assert warning["failures"] == [
            "low contains N-gons",
            "SIGNED_AUDIT_FAILED",
            "NGONS=1",
            "topology_goal_met=false: target face/topology requirement was not met",
            "automatic_final_promotion_allowed=false: candidate is not deliverable",
        ]
        events = await client.get(
            f"/api/v1/assets/jobs/{job['job_id']}/events",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert events.status_code == 200
        assert "asset.succeeded_with_warnings" in events.text
        assert "RETOPOLOGY_QUALITY_GATE_WARNING" in events.text
        assert "NGONS=1" in events.text


async def test_retopology_advisory_keeps_source_protection_hard(
    tmp_path: Path,
) -> None:
    async for settings, client in prepared_asset_app(
        tmp_path, retopology_qa_enforcement="advisory"
    ):
        job = await create_and_claim_retopology_process(
            client, settings, "asset:crate:retopo:source-protection"
        )
        completed = await client.post(
            f"/internal/v1/assets/jobs/{job['job_id']}/retopology-process-complete",
            headers={"X-Asset-Lease": str(job["lease_token"])},
            files=retopology_process_completion_files(
                job,
                png_bytes(16),
                quality_passed=False,
                source_preserved=False,
            ),
        )
        assert completed.status_code == 422, completed.text
        assert completed.json()["detail"]["code"] == ("RETOPOLOGY_SOURCE_PROTECTION_FAILED")
        status = await client.get(
            f"/api/v1/assets/jobs/{job['job_id']}",
            headers={"X-API-Key": "gpc_assetkey_secret"},
        )
        assert status.json()["delivery_ready"] is False
        assert status.json()["artifacts"] == []
