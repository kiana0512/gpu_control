import time

import httpx
from gpu_node_agent.main import NodeAgentSettings, create_app

from packages.gpu_control_core.security import sign_agent_request
from packages.gpu_control_core.settings import Settings


def test_worker_agent_does_not_require_control_plane_secrets() -> None:
    settings = NodeAgentSettings(
        environment="production",
        node_agent_hmac_secret="separate-worker-agent-secret-at-least-32-characters",
    )
    assert settings.environment == "production"
    assert settings.node_agent_hmac_secret.endswith("32-characters")


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
