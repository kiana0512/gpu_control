from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from gpu_control_blender_worker.main import post_completion_with_lease_keepalive


class ControlledCompletionClient:
    def __init__(self, *, renewal_status: int = 200, cancel_requested: bool = False):
        self.renewal_status = renewal_status
        self.cancel_requested = cancel_requested
        self.completion_started = asyncio.Event()
        self.completion_cancelled = asyncio.Event()
        self.release_completion = asyncio.Event()
        self.renewal_seen = asyncio.Event()
        self.renewals = 0

    async def post(self, path: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", f"http://asset-api{path}")
        if path.endswith("/progress"):
            self.renewals += 1
            self.renewal_seen.set()
            return httpx.Response(
                self.renewal_status,
                request=request,
                json={"cancel_requested": self.cancel_requested},
            )

        self.completion_started.set()
        try:
            await self.release_completion.wait()
        except asyncio.CancelledError:
            self.completion_cancelled.set()
            raise
        return httpx.Response(200, request=request, json={"accepted": True})


@pytest.mark.asyncio
async def test_slow_completion_upload_renews_lease_and_stops_after_response() -> None:
    client = ControlledCompletionClient()
    upload = asyncio.create_task(
        post_completion_with_lease_keepalive(
            client,  # type: ignore[arg-type]
            "job-slow",
            {"X-Asset-Lease": "lease-slow"},
            "/internal/v1/assets/jobs/job-slow/complete",
            {},
            keepalive_seconds=0.01,
            renewal_grace_seconds=0.01,
        )
    )

    await asyncio.wait_for(client.completion_started.wait(), timeout=1)
    await asyncio.wait_for(client.renewal_seen.wait(), timeout=1)
    assert client.renewals >= 1

    client.release_completion.set()
    response = await asyncio.wait_for(upload, timeout=1)
    assert response.status_code == 200

    renewals_after_completion = client.renewals
    await asyncio.sleep(0.04)
    assert client.renewals == renewals_after_completion
    assert not client.completion_cancelled.is_set()


@pytest.mark.asyncio
async def test_completion_upload_renewal_failure_cancels_inflight_upload() -> None:
    client = ControlledCompletionClient(renewal_status=503)

    with pytest.raises(
        RuntimeError, match="completion upload lease renewal failed before commit"
    ):
        await post_completion_with_lease_keepalive(
            client,  # type: ignore[arg-type]
            "job-expired",
            {"X-Asset-Lease": "lease-expired"},
            "/internal/v1/assets/jobs/job-expired/complete",
            {},
            keepalive_seconds=0.01,
            renewal_grace_seconds=0.01,
        )

    assert client.renewals == 1
    assert client.completion_cancelled.is_set()


@pytest.mark.asyncio
async def test_completion_upload_honors_cancel_requested_during_keepalive() -> None:
    client = ControlledCompletionClient(cancel_requested=True)

    with pytest.raises(RuntimeError, match="cancelled during completion upload"):
        await post_completion_with_lease_keepalive(
            client,  # type: ignore[arg-type]
            "job-cancelled",
            {"X-Asset-Lease": "lease-cancelled"},
            "/internal/v1/assets/jobs/job-cancelled/complete",
            {},
            keepalive_seconds=0.01,
            renewal_grace_seconds=0.01,
        )

    assert client.renewals == 1
    assert client.completion_cancelled.is_set()
