import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from packages.comfy_client import client as comfy_client_module
from packages.comfy_client.client import ComfyClient, ComfyError, ComfyOutput
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


async def test_comfy_client_accepts_empty_or_text_interrupt_acknowledgement() -> None:
    responses = iter(
        [
            httpx.Response(200, content=b""),
            httpx.Response(200, text="Prompt interrupted"),
        ]
    )

    async def interrupt_ack(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/interrupt"
        return next(responses)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(interrupt_ack))
    try:
        assert await client.interrupt() == {}
        assert await client.interrupt() == {}
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


async def test_comfy_client_upload_many_parallelizes_verified_inputs_and_preserves_order(
    tmp_path,
) -> None:
    expected: dict[str, bytes] = {}
    inputs = []
    for index in range(4):
        path = tmp_path / f"input-{index}.png"
        content = f"verified-image-{index}".encode()
        path.write_bytes(content)
        expected[path.name] = content
        inputs.append((path, index == 2))

    active_posts = 0
    peak_posts = 0
    all_posts_started = asyncio.Event()
    get_names: list[str] = []

    async def concurrent_uploads(request: httpx.Request) -> httpx.Response:
        nonlocal active_posts, peak_posts
        if request.method == "POST" and request.url.path in {
            "/upload/image",
            "/upload/mask",
        }:
            body = await request.aread()
            match = re.search(rb'filename="([^"]+)"', body)
            assert match is not None
            filename = match.group(1).decode()
            active_posts += 1
            peak_posts = max(peak_posts, active_posts)
            if active_posts == len(inputs):
                all_posts_started.set()
            await asyncio.wait_for(all_posts_started.wait(), timeout=1)
            active_posts -= 1
            return httpx.Response(
                200,
                json={"name": filename, "subfolder": "job-1", "type": "input"},
            )
        if request.method == "GET" and request.url.path == "/view":
            filename = request.url.params["filename"]
            get_names.append(filename)
            return httpx.Response(200, content=expected[filename])
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(concurrent_uploads))
    try:
        uploaded = await client.upload_many(inputs, subfolder="job-1")
    finally:
        await client.close()

    assert peak_posts == 4
    assert [item["name"] for item in uploaded] == [path.name for path, _ in inputs]
    assert set(get_names) == set(expected)
    assert all(item["verified"] is True for item in uploaded)


async def test_comfy_client_upload_many_settles_verification_before_preserving_error(
    tmp_path,
) -> None:
    inputs = []
    expected: dict[str, bytes] = {}
    for index in range(4):
        path = tmp_path / f"input-{index}.png"
        content = f"verified-image-{index}".encode()
        path.write_bytes(content)
        expected[path.name] = content
        inputs.append((path, False))
    verified: set[str] = set()

    async def one_corrupt_readback(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = await request.aread()
            match = re.search(rb'filename="([^"]+)"', body)
            assert match is not None
            filename = match.group(1).decode()
            return httpx.Response(200, json={"name": filename, "subfolder": "job-1"})
        if request.method == "GET" and request.url.path == "/view":
            filename = request.url.params["filename"]
            verified.add(filename)
            content = b"corrupt" if filename == "input-1.png" else expected[filename]
            return httpx.Response(200, content=content)
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(one_corrupt_readback))
    try:
        with pytest.raises(ComfyError) as failure:
            await client.upload_many(inputs, subfolder="job-1", max_attempts=1)
    finally:
        await client.close()

    assert failure.value.code == "COMFY_UPLOAD_INTEGRITY_FAILED"
    assert verified == set(expected)


async def test_autodl_output_download_uses_verified_parallel_ranges(tmp_path) -> None:
    payload = bytes(range(251)) * 3000
    output = ComfyOutput("result.png", "", "output")
    destination = tmp_path / "result.png"
    range_requests: list[str] = []

    async def ranged_output(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(payload)),
                },
            )
        requested = request.headers.get("range")
        assert requested is not None
        range_requests.append(requested)
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", requested)
        assert match is not None
        start, end = (int(value) for value in match.groups())
        return httpx.Response(
            206,
            content=payload[start : end + 1],
            headers={"Content-Range": f"bytes {start}-{end}/{len(payload)}"},
        )

    client = ComfyClient(
        "https://instance.seetacloud.com:8443",
        transport=httpx.MockTransport(ranged_output),
    )
    try:
        size, digest = await client.download(output, destination)
    finally:
        await client.close()

    assert len(range_requests) == 8
    assert destination.read_bytes() == payload
    assert size == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()


async def test_invalid_autodl_range_contract_falls_back_to_stream(tmp_path) -> None:
    payload = bytes(range(251)) * 3000
    output = ComfyOutput("result.png", "", "output")
    destination = tmp_path / "result.png"
    full_downloads = 0

    async def invalid_ranges(request: httpx.Request) -> httpx.Response:
        nonlocal full_downloads
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(payload)),
                },
            )
        if request.headers.get("range"):
            return httpx.Response(200, content=payload)
        full_downloads += 1
        return httpx.Response(200, content=payload)

    client = ComfyClient(
        "https://instance.seetacloud.com:8443",
        transport=httpx.MockTransport(invalid_ranges),
    )
    try:
        size, digest = await client.download(output, destination)
    finally:
        await client.close()

    assert full_downloads == 1
    assert destination.read_bytes() == payload
    assert size == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()


async def test_comfy_client_verifies_upload_with_remote_digest_without_readback(
    tmp_path,
) -> None:
    source = tmp_path / "image.png"
    payload = b"remote-digest-verification"
    source.write_bytes(payload)
    view_requests = 0

    async def comfy(request: httpx.Request) -> httpx.Response:
        nonlocal view_requests
        if request.method == "POST" and request.url.path == "/upload/image":
            return httpx.Response(
                200,
                json={"name": source.name, "subfolder": "job-1", "type": "input"},
            )
        if request.url.path == "/view":
            view_requests += 1
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    async def integrity(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/comfy-input-digest"
        assert request.url.params["filename"] == source.name
        assert request.url.params["subfolder"] == "job-1"
        return httpx.Response(
            200,
            json={
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload(source, subfolder="job-1")
    finally:
        await client.close()

    assert uploaded["verified"] is True
    assert uploaded["verification_method"] == "remote_digest"
    assert view_requests == 0


async def test_comfy_client_remote_digest_failure_falls_back_to_full_readback(
    tmp_path,
) -> None:
    source = tmp_path / "image.png"
    payload = b"fallback-still-verifies-every-byte"
    source.write_bytes(payload)
    view_requests = 0

    async def comfy(request: httpx.Request) -> httpx.Response:
        nonlocal view_requests
        if request.method == "POST":
            return httpx.Response(200, json={"name": source.name, "subfolder": "job-1"})
        if request.url.path == "/view":
            view_requests += 1
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    try:
        uploaded = await client.upload(source, subfolder="job-1")
    finally:
        await client.close()

    assert uploaded["verified"] is True
    assert uploaded["verification_method"] == "readback_fallback"
    assert view_requests == 1


async def test_comfy_client_cache_hit_materializes_without_wan_upload(tmp_path) -> None:
    source = tmp_path / "image.png"
    payload = b"tenant-scoped-cache-hit"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    comfy_requests = 0
    cache_requests: list[str] = []

    async def comfy(_: httpx.Request) -> httpx.Response:
        nonlocal comfy_requests
        comfy_requests += 1
        return httpx.Response(500)

    async def integrity(request: httpx.Request) -> httpx.Response:
        cache_requests.append(request.url.path)
        if request.url.path.endswith("/materialize"):
            body = json.loads(request.content)
            assert body["namespace"] == "a" * 64
            assert body["sha256"] == digest
            assert body["size_bytes"] == len(payload)
            assert body["filename"] == source.name
            assert body["subfolder"] == "job-1"
            return httpx.Response(
                200,
                json={
                    "status": "hit",
                    "verified": True,
                    "size_bytes": len(payload),
                    "sha256": digest,
                    "verification_method": "atomic_target_sha256",
                    "receipt_version": 2,
                },
            )
        if request.url.path == "/internal/v1/comfy-input-digest":
            return httpx.Response(
                200,
                json={"size_bytes": len(payload), "sha256": digest},
            )
        raise AssertionError("cache hit must not be promoted")

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload_cached(
            source,
            cache_namespace="a" * 64,
            subfolder="job-1",
        )
    finally:
        await client.close()

    assert uploaded["cache_hit"] is True
    assert uploaded["attempt"] == 0
    assert uploaded["verification_method"] == "atomic_materialize_sha256"
    assert comfy_requests == 0
    assert cache_requests == ["/internal/v1/comfy-input-cache/materialize"]


async def test_unversioned_atomic_receipt_cannot_mask_a_missing_target(tmp_path) -> None:
    source = tmp_path / "image.png"
    payload = b"must-reupload-after-false-cache-hit"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    uploaded = False
    upload_count = 0

    async def comfy(request: httpx.Request) -> httpx.Response:
        nonlocal uploaded, upload_count
        if request.method == "POST" and request.url.path == "/upload/image":
            uploaded = True
            upload_count += 1
            return httpx.Response(
                200,
                json={"name": source.name, "subfolder": "job-1", "type": "input"},
            )
        if request.method == "GET" and request.url.path == "/view" and uploaded:
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    async def integrity(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/materialize"):
            # This is the faulty r3 shape: it claimed an atomic hit but did not
            # carry the fixed receipt contract.
            return httpx.Response(
                200,
                json={
                    "status": "hit",
                    "verified": True,
                    "size_bytes": len(payload),
                    "sha256": digest,
                    "verification_method": "atomic_target_sha256",
                },
            )
        if request.url.path == "/internal/v1/comfy-input-digest":
            if not uploaded:
                return httpx.Response(502)
            return httpx.Response(
                200,
                json={"size_bytes": len(payload), "sha256": digest},
            )
        if request.url.path.endswith("/promote"):
            return httpx.Response(200, json={"status": "promoted"})
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        result = await client.upload_cached(
            source,
            cache_namespace="a" * 64,
            subfolder="job-1",
        )
    finally:
        await client.close()

    assert upload_count == 1
    assert result["cache_hit"] is False
    assert result["verified"] is True


async def test_comfy_client_legacy_cache_hit_keeps_independent_digest_fence(
    tmp_path,
) -> None:
    source = tmp_path / "image.png"
    payload = b"legacy-tunnel-cache-hit"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    cache_requests: list[str] = []

    async def integrity(request: httpx.Request) -> httpx.Response:
        cache_requests.append(request.url.path)
        if request.url.path.endswith("/materialize"):
            return httpx.Response(200, json={"status": "hit"})
        if request.url.path == "/internal/v1/comfy-input-digest":
            return httpx.Response(
                200,
                json={"size_bytes": len(payload), "sha256": digest},
            )
        return httpx.Response(404)

    client = ComfyClient(
        "http://fake",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload_cached(
            source,
            cache_namespace="a" * 64,
            subfolder="job-1",
        )
    finally:
        await client.close()

    assert uploaded["cache_hit"] is True
    assert uploaded["verification_method"] == "remote_digest_cache_hit"
    assert cache_requests == [
        "/internal/v1/comfy-input-cache/materialize",
        "/internal/v1/comfy-input-digest",
    ]


async def test_comfy_client_cache_miss_uploads_verifies_and_promotes(tmp_path) -> None:
    source = tmp_path / "image.png"
    payload = b"cache-miss-after-remote-restart"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    comfy_uploads = 0
    promoted = False
    digest_checks = 0

    async def comfy(request: httpx.Request) -> httpx.Response:
        nonlocal comfy_uploads
        if request.method == "POST" and request.url.path == "/upload/image":
            comfy_uploads += 1
            return httpx.Response(
                200,
                json={"name": source.name, "subfolder": "job-1", "type": "input"},
            )
        return httpx.Response(404)

    async def integrity(request: httpx.Request) -> httpx.Response:
        nonlocal promoted, digest_checks
        if request.url.path.endswith("/materialize"):
            return httpx.Response(404, json={"status": "miss"})
        if request.url.path == "/internal/v1/comfy-input-digest":
            digest_checks += 1
            return httpx.Response(
                200,
                json={"size_bytes": len(payload), "sha256": digest},
            )
        if request.url.path.endswith("/promote"):
            promoted = True
            return httpx.Response(
                200,
                json={
                    "status": "promoted",
                    "verified": True,
                    "size_bytes": len(payload),
                    "sha256": digest,
                    "verification_method": "atomic_cache_sha256",
                    "receipt_version": 2,
                },
            )
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload_cached(
            source,
            cache_namespace="a" * 64,
            subfolder="job-1",
        )
    finally:
        await client.close()

    assert uploaded["cache_hit"] is False
    assert uploaded["cache_promoted"] is True
    assert uploaded["verified"] is True
    assert uploaded["verification_method"] == "atomic_promotion_sha256"
    assert comfy_uploads == 1
    assert promoted is True
    assert digest_checks == 0


async def test_comfy_client_corrupt_cache_falls_back_to_verified_upload(tmp_path) -> None:
    source = tmp_path / "image.png"
    payload = b"repair-corrupt-cache"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    digest_checks = 0
    comfy_uploads = 0

    async def comfy(request: httpx.Request) -> httpx.Response:
        nonlocal comfy_uploads
        if request.method == "POST":
            comfy_uploads += 1
            return httpx.Response(200, json={"name": source.name, "subfolder": "job-1"})
        return httpx.Response(404)

    async def integrity(request: httpx.Request) -> httpx.Response:
        nonlocal digest_checks
        if request.url.path.endswith("/materialize"):
            return httpx.Response(200, json={"status": "hit"})
        if request.url.path == "/internal/v1/comfy-input-digest":
            digest_checks += 1
            return httpx.Response(
                200,
                json={
                    "size_bytes": len(payload),
                    "sha256": "0" * 64 if digest_checks == 1 else digest,
                },
            )
        if request.url.path.endswith("/promote"):
            return httpx.Response(200, json={"status": "promoted"})
        return httpx.Response(404)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload_cached(
            source,
            cache_namespace="a" * 64,
            subfolder="job-1",
        )
    finally:
        await client.close()

    assert uploaded["cache_hit"] is False
    assert uploaded["cache_promoted"] is True
    assert comfy_uploads == 1
    assert digest_checks == 2


async def test_comfy_client_cache_control_failure_never_blocks_verified_upload(
    tmp_path,
) -> None:
    source = tmp_path / "image.png"
    payload = b"cache-control-unavailable"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    async def comfy(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"name": source.name, "subfolder": "job-1"})
        return httpx.Response(404)

    async def integrity(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/comfy-input-digest":
            return httpx.Response(
                200,
                json={"size_bytes": len(payload), "sha256": digest},
            )
        return httpx.Response(503)

    client = ComfyClient("http://fake", transport=httpx.MockTransport(comfy))
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload_cached(
            source,
            cache_namespace="a" * 64,
            subfolder="job-1",
        )
    finally:
        await client.close()

    assert uploaded["verified"] is True
    assert uploaded["cache_hit"] is False
    assert uploaded["cache_promoted"] is False


async def test_comfy_client_cache_materialization_remains_bounded_and_concurrent(
    tmp_path,
) -> None:
    inputs: list[tuple[Path, bool]] = []
    expected: dict[str, bytes] = {}
    for index in range(4):
        path = tmp_path / f"input-{index}.png"
        content = f"cached-image-{index}".encode()
        path.write_bytes(content)
        inputs.append((path, False))
        expected[path.name] = content
    active = 0
    peak = 0
    all_started = asyncio.Event()

    async def integrity(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        if request.url.path.endswith("/materialize"):
            active += 1
            peak = max(peak, active)
            if active == 4:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=1)
            active -= 1
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "hit",
                    "verified": True,
                    "size_bytes": body["size_bytes"],
                    "sha256": body["sha256"],
                    "verification_method": "atomic_target_sha256",
                    "receipt_version": 2,
                },
            )
        if request.url.path == "/internal/v1/comfy-input-digest":
            filename = request.url.params["filename"]
            content = expected[filename]
            return httpx.Response(
                200,
                json={
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            )
        return httpx.Response(404)

    client = ComfyClient(
        "http://fake",
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    client.enable_remote_input_integrity(
        "http://integrity",
        transport=httpx.MockTransport(integrity),
    )
    try:
        uploaded = await client.upload_many(
            inputs,
            subfolder="job-1",
            cache_namespace="a" * 64,
        )
    finally:
        await client.close()

    assert peak == 4
    assert [item["name"] for item in uploaded] == [path.name for path, _ in inputs]
    assert all(item["cache_hit"] is True for item in uploaded)


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


async def test_interrupted_execution_ends_event_stream_without_reconnect(
    monkeypatch,
) -> None:
    messages = iter(
        (
            {
                "type": "progress",
                "data": {"prompt_id": "another-prompt", "value": 1, "max": 10},
            },
            {
                "type": "execution_interrupted",
                "data": {"prompt_id": "target-prompt"},
            },
        )
    )

    class FakeSocket(AsyncIterator[str]):
        def __init__(self) -> None:
            self.read_count = 0

        def __aiter__(self) -> "FakeSocket":
            return self

        async def __anext__(self) -> str:
            self.read_count += 1
            try:
                return json.dumps(next(messages))
            except StopIteration as exc:
                raise AssertionError(
                    "the client read past execution_interrupted instead of returning"
                ) from exc

    socket = FakeSocket()
    connect_calls: list[tuple[str, dict[str, Any]]] = []

    class FakeConnection:
        async def __aenter__(self) -> FakeSocket:
            return socket

        async def __aexit__(self, *_: object) -> None:
            return None

    def fake_connect(url: str, **kwargs: Any) -> FakeConnection:
        connect_calls.append((url, kwargs))
        return FakeConnection()

    monkeypatch.setattr(comfy_client_module.websockets, "connect", fake_connect)
    client = ComfyClient("http://fake")
    try:
        events = [
            event
            async for event in client.events("target-prompt", "target-client", max_reconnects=3)
        ]
    finally:
        await client.close()

    assert events == [
        {
            "type": "execution_interrupted",
            "data": {"prompt_id": "target-prompt"},
        }
    ]
    assert socket.read_count == 2
    assert len(connect_calls) == 1
    assert connect_calls[0][0] == "ws://fake/ws?clientId=target-client"
    assert connect_calls[0][1]["close_timeout"] == 0.1


async def test_deadline_recovery_retries_and_recovers_from_prompt_history(
    monkeypatch,
) -> None:
    connect_calls = 0
    history_calls = 0

    class SilentSocket:
        async def recv(self) -> str:
            await asyncio.Future()
            raise AssertionError("unreachable")

    class FakeConnection:
        async def __aenter__(self) -> SilentSocket:
            if connect_calls <= 4:
                raise OSError("tunnel temporarily unavailable")
            return SilentSocket()

        async def __aexit__(self, *_: object) -> None:
            return None

    def fake_connect(_: str, **__: Any) -> FakeConnection:
        nonlocal connect_calls
        connect_calls += 1
        return FakeConnection()

    async def no_backoff(_: float) -> None:
        return None

    async def fake_history(prompt_id: str) -> dict[str, Any]:
        nonlocal history_calls
        history_calls += 1
        if history_calls < 5:
            return {}
        return {prompt_id: {"status": {"completed": True}}}

    monkeypatch.setattr(comfy_client_module.websockets, "connect", fake_connect)
    monkeypatch.setattr(comfy_client_module.asyncio, "sleep", no_backoff)
    client = ComfyClient("http://fake")
    client.history = fake_history  # type: ignore[method-assign]
    try:
        events = [
            event
            async for event in client.events(
                "persisted-prompt",
                "persisted-client",
                max_reconnects=1,
                reconnect_deadline=asyncio.get_running_loop().time() + 1,
                history_poll_interval=0.001,
            )
        ]
    finally:
        await client.close()

    assert events == [
        {
            "type": "history_recovered",
            "data": {
                "prompt_id": "persisted-prompt",
                "history": {"persisted-prompt": {"status": {"completed": True}}},
            },
        }
    ]
    assert connect_calls == 5
    assert history_calls == 5


async def test_deadline_recovery_fails_only_after_its_absolute_bound() -> None:
    client = ComfyClient("http://fake")
    try:
        with pytest.raises(ComfyError) as failure:
            events = client.events(
                "persisted-prompt",
                "persisted-client",
                reconnect_deadline=asyncio.get_running_loop().time(),
            )
            await anext(events)
        assert failure.value.code == "COMFY_WS_DISCONNECTED"
    finally:
        await client.close()
