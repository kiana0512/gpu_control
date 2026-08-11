import httpx
import pytest
from gpu_control_blender_worker.main import (
    DIRECT_V2_ESTIMATED_STAGE_SECONDS,
    report_subprocess_progress,
    stage_eta_for_elapsed,
    stage_progress_for_elapsed,
)


def test_direct_v2_eta_is_distinct_from_hard_timeout() -> None:
    assert DIRECT_V2_ESTIMATED_STAGE_SECONDS == 720


def test_stage_progress_does_not_reach_completion_boundary_while_running() -> None:
    assert stage_progress_for_elapsed(8, 92, 0, 600) == 8
    assert stage_progress_for_elapsed(8, 92, 300, 600) == pytest.approx(50)
    assert stage_progress_for_elapsed(8, 92, 3600, 600) == pytest.approx(87.8)


def test_stage_eta_becomes_unknown_after_normal_window() -> None:
    assert stage_eta_for_elapsed(600, 15) == 585
    assert stage_eta_for_elapsed(600, 600) is None
    assert stage_eta_for_elapsed(600, 3600) is None


@pytest.mark.asyncio
async def test_live_subprocess_survives_transient_progress_outage() -> None:
    responses = [502, 429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = responses.pop(0)
        return httpx.Response(status, json={"cancel_requested": False})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://asset-api",
    ) as client:
        for _ in range(3):
            assert not await report_subprocess_progress(
                client,
                "job-1",
                {"X-Asset-Lease": "lease"},
                {"progress": 10, "stage": "BUILD", "message": "running"},
            )


@pytest.mark.asyncio
async def test_live_subprocess_survives_progress_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("control plane restarting", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://asset-api",
    ) as client:
        assert not await report_subprocess_progress(
            client,
            "job-1",
            {"X-Asset-Lease": "lease"},
            {"progress": 10, "stage": "BUILD", "message": "running"},
        )


@pytest.mark.asyncio
async def test_expired_lease_still_stops_live_subprocess() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "lease expired"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://asset-api",
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await report_subprocess_progress(
                client,
                "job-1",
                {"X-Asset-Lease": "lease"},
                {"progress": 10, "stage": "BUILD", "message": "running"},
            )
