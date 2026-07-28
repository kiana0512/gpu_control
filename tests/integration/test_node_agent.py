import time
from pathlib import Path

import gpu_node_agent.main as node_agent
import httpx
from gpu_node_agent.main import (
    NodeAgentSettings,
    _current_ip,
    _imageclip_pipeline_state,
    _mac_address,
    create_app,
)

from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings


def test_worker_agent_does_not_require_control_plane_secrets() -> None:
    settings = NodeAgentSettings(
        environment="production",
        node_agent_hmac_secret="separate-worker-agent-secret-at-least-32-characters",
    )
    assert settings.environment == "production"
    assert settings.node_agent_hmac_secret.endswith("32-characters")


def test_hybrid_node_uses_physical_host_identity_over_wsl_nat() -> None:
    assert _current_ip("10.3.34.11", "10.3.34.14") == "10.3.34.14"
    assert _mac_address("3C-7C-3F-A5-B0-4F") == "3c:7c:3f:a5:b0:4f"


def test_imageclip_pipeline_state_is_deterministic_and_tracks_content(tmp_path: Path) -> None:
    repository = tmp_path / "imageclip"
    (repository / ".git" / "refs" / "heads").mkdir(parents=True)
    (repository / "Cherry_lizi" / "nested").mkdir(parents=True)
    commit = "7" * 40
    (repository / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repository / ".git" / "refs" / "heads" / "main").write_text(
        f"{commit}\n", encoding="utf-8"
    )
    (repository / "ImageClip.json").write_text('{"workflow":"latest"}\n', encoding="utf-8")
    custom_node = repository / "Cherry_lizi" / "nested" / "node.py"
    custom_node.write_text("VALUE = 1\n", encoding="utf-8")
    cache = repository / "Cherry_lizi" / "__pycache__"
    cache.mkdir()
    (cache / "node.pyc").write_bytes(b"ignored")

    first = _imageclip_pipeline_state(repository)
    second = _imageclip_pipeline_state(repository)
    assert first == second
    assert first[0] == commit

    custom_node.write_text("VALUE = 2\n", encoding="utf-8")
    assert _imageclip_pipeline_state(repository)[1] != first[1]


async def test_node_agent_requires_signature_and_rejects_replay() -> None:
    secret = "node-agent-test-secret"
    app = create_app(Settings(environment="test", node_agent_hmac_secret=secret))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            assert (await client.get("/health/live")).status_code == 200
            assert (
                await client.post("/v1/operations", json={"action": "status"})
            ).status_code == 401

            body = b'{"action":"forbidden","lines":200}'
            timestamp = str(int(time.time()))
            nonce = "one-time-nonce"
            signature = sign_agent_request("POST", "/v1/operations", body, timestamp, nonce, secret)
            headers = {
                "content-type": "application/json",
                "x-gpu-timestamp": timestamp,
                "x-gpu-nonce": nonce,
                "x-gpu-signature": signature,
            }
            first = await client.post("/v1/operations", content=body, headers=headers)
            assert first.status_code == 400
            replay = await client.post("/v1/operations", content=body, headers=headers)
            assert replay.status_code == 409


async def test_gpu_metrics_are_signed_and_return_live_values(monkeypatch) -> None:
    secret = "node-agent-test-secret"

    async def fake_gpu_metrics() -> dict[str, int]:
        return {"gpu_util_percent": 73, "free_vram_mb": 12000, "total_vram_mb": 24576}

    monkeypatch.setattr(node_agent, "_gpu_metrics", fake_gpu_metrics)
    app = create_app(Settings(environment="test", node_agent_hmac_secret=secret))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            assert (await client.get("/v1/gpu-metrics")).status_code == 401
            timestamp = str(int(time.time()))
            nonce = "gpu-metrics-nonce"
            signature = sign_agent_request(
                "GET", "/v1/gpu-metrics", b"", timestamp, nonce, secret
            )
            response = await client.get(
                "/v1/gpu-metrics",
                headers={
                    "x-gpu-timestamp": timestamp,
                    "x-gpu-nonce": nonce,
                    "x-gpu-signature": signature,
                },
            )
            assert response.status_code == 200
            assert response.json() == {
                "gpu_util_percent": 73,
                "free_vram_mb": 12000,
                "total_vram_mb": 24576,
            }
