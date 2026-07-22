import httpx

from tests.fake_comfyui.app import Behavior, State, create_app


async def test_fake_comfyui_success_multi_output_and_free() -> None:
    state = State(behavior=Behavior(duration_seconds=0, multiple_outputs=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)), base_url="http://fake"
    ) as client:
        assert (await client.get("/system_stats")).status_code == 200
        uploaded = await client.post(
            "/upload/image", files={"image": ("a.png", b"image", "image/png")}
        )
        assert uploaded.json()["name"] == "a.png"
        prompt_id = (
            await client.post(
                "/prompt", json={"prompt": {"1": {}}, "client_id": "gpu-control-test"}
            )
        ).json()["prompt_id"]
        history = (await client.get(f"/history/{prompt_id}")).json()[prompt_id]
        assert len(history["outputs"]["9"]["images"]) == 2
        assert (await client.post("/free", json={})).json()["models_unloaded"] is True


async def test_fake_comfyui_failures_and_external_queue() -> None:
    state = State(
        behavior=Behavior(
            upload_failure=True,
            validation_failure=True,
            external_queue=True,
            interrupt_success=False,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)), base_url="http://fake"
    ) as client:
        assert (
            await client.post("/upload/mask", files={"image": ("a.png", b"x")})
        ).status_code == 500
        assert "error" in (await client.post("/prompt", json={})).json()
        queue = (await client.get("/queue")).json()
        assert (queue["queue_running"] + queue["queue_pending"])[0][1] == "external"
        assert (await client.post("/interrupt")).status_code == 500
