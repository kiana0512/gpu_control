import httpx

from packages.comfy_client.client import ComfyClient
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


async def test_comfy_client_accepts_empty_free_response() -> None:
    async def empty_free(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = ComfyClient("http://fake", transport=httpx.MockTransport(empty_free))
    try:
        assert await client.free() == {}
    finally:
        await client.close()


async def test_prompt_submission_recovery_finds_queue_and_history_without_resubmit() -> None:
    state = State(behavior=Behavior(duration_seconds=60))
    client = ComfyClient(
        "http://fake",
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    try:
        client_id = "gpu-control-job-1-attempt-1"
        first = await client.submit({"1": {}}, client_id)
        assert await client.prompt_ids_for_client(client_id) == [first]
        assert len(state.prompts) == 1

        state.behavior.duration_seconds = 0
        assert await client.prompt_ids_for_client(client_id) == [first]
        assert await client.prompt_ids_for_client("gpu-control-missing-attempt-1") == []

        second = await client.submit({"1": {}}, client_id)
        assert await client.prompt_ids_for_client(client_id) == sorted([first, second])
        assert len(state.prompts) == 2
    finally:
        await client.close()


async def test_comfy_client_overwrites_and_repairs_zero_byte_upload(tmp_path) -> None:
    source = tmp_path / "frame-0034.png"
    expected = b"complete-png-payload"
    source.write_bytes(expected)
    uploads = 0

    async def flaky_upload(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.method == "POST" and request.url.path == "/upload/image":
            uploads += 1
            assert b'name="overwrite"' in request.content
            assert b"true" in request.content
            return httpx.Response(
                200,
                json={"name": source.name, "subfolder": "job-1", "type": "input"},
            )
        if request.method == "GET" and request.url.path == "/view":
            # Reproduce a dropped first upload which left an empty destination.
            return httpx.Response(200, content=b"" if uploads == 1 else expected)
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(flaky_upload))
    try:
        uploaded = await client.upload(source, subfolder="job-1", max_attempts=2)
        assert uploads == 2
        assert uploaded["verified"] is True
        assert uploaded["size_bytes"] == len(expected)
        assert uploaded["attempt"] == 2
    finally:
        await client.close()


def test_output_collection_is_limited_to_declared_nodes() -> None:
    history = {
        "prompt": {
            "outputs": {
                "8": {"images": [{"filename": "intermediate.png", "type": "output"}]},
                "25": {"images": [{"filename": "final-rgba.png", "type": "output"}]},
            }
        }
    }
    outputs = ComfyClient.outputs(history, "prompt", {"25"})
    assert [item.filename for item in outputs] == ["final-rgba.png"]
