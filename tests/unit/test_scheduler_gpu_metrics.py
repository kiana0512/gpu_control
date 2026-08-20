from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import Scheduler, node_has_recent_telemetry
from packages.gpu_control_core.models import Node


def test_recent_node_telemetry_survives_transient_probe_stall() -> None:
    now = datetime.now(UTC)
    node = Node(id="worker-4070ti-animation-host-01")
    node.last_heartbeat_at = now - timedelta(seconds=90)

    assert node_has_recent_telemetry(node, now)


def test_stale_node_telemetry_does_not_mask_real_offline_node() -> None:
    now = datetime.now(UTC)
    node = Node(id="worker-4070ti-animation-host-01")
    node.last_heartbeat_at = now - timedelta(minutes=4)

    assert not node_has_recent_telemetry(node, now)


async def test_comfy_failure_keeps_independent_agent_evidence(monkeypatch) -> None:
    scheduler = Scheduler.__new__(Scheduler)
    node = Node(id="worker-4070ti-animation-host-01")
    metrics = {
        "gpu_util_percent": 91.0,
        "free_vram_mb": 5918,
        "total_vram_mb": 12282,
        "gpu_temperature_c": 73.0,
        "gpu_power_w": 209.0,
        "gpu_power_limit_w": 285.0,
    }

    async def identity(_node):
        return {"node_id": node.id}

    async def gpu_metrics(_node):
        return metrics

    monkeypatch.setattr(scheduler, "node_agent_identity", identity)
    monkeypatch.setattr(scheduler, "node_agent_gpu_metrics", gpu_metrics)

    reported_identity, reported_metrics, error = (
        await scheduler.node_agent_fallback_evidence(node)
    )

    assert reported_identity == {"node_id": node.id}
    assert reported_metrics == metrics
    assert error is None


async def test_comfy_failure_marks_agent_evidence_unavailable_on_agent_error(
    monkeypatch,
) -> None:
    scheduler = Scheduler.__new__(Scheduler)
    node = Node(id="worker-4070ti-animation-host-01")

    async def identity(_node):
        raise httpx.ReadTimeout("agent unavailable")

    monkeypatch.setattr(scheduler, "node_agent_identity", identity)

    reported_identity, reported_metrics, error = (
        await scheduler.node_agent_fallback_evidence(node)
    )

    assert reported_identity is None
    assert reported_metrics is None
    assert isinstance(error, httpx.ReadTimeout)


async def test_failed_optional_gpu_metrics_probe_uses_bounded_backoff(monkeypatch) -> None:
    calls = 0

    class FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("temporary WSL nvidia-smi contention")

    monkeypatch.setattr(scheduler_main.httpx, "AsyncClient", FailingClient)
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.settings = SimpleNamespace(node_agent_secret=lambda _node_id: "secret")
    scheduler.gpu_metrics_retry_at = {}
    node = Node(id="worker-3090-b", agent_url="http://10.3.34.14:9201")

    assert await scheduler.node_agent_gpu_metrics(node) is None
    assert await scheduler.node_agent_gpu_metrics(node) is None

    assert calls == 1
    assert scheduler.gpu_metrics_retry_at[node.id] > 0


async def test_gpu_metrics_probe_preserves_optional_temperature_and_power(monkeypatch) -> None:
    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, int | float]:
            return {
                "gpu_util_percent": 97,
                "free_vram_mb": 13900,
                "total_vram_mb": 24576,
                "gpu_temperature_c": 71.0,
                "gpu_power_w": 322.6,
                "gpu_power_limit_w": 370.0,
            }

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, *args, **kwargs) -> Response:
            return Response()

    monkeypatch.setattr(scheduler_main.httpx, "AsyncClient", Client)
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.settings = SimpleNamespace(node_agent_secret=lambda _node_id: "secret")
    scheduler.gpu_metrics_retry_at = {}
    node = Node(id="worker-3090-b", agent_url="http://10.3.34.14:9201")

    assert await scheduler.node_agent_gpu_metrics(node) == {
        "gpu_util_percent": 97.0,
        "free_vram_mb": 13900,
        "total_vram_mb": 24576,
        "gpu_temperature_c": 71.0,
        "gpu_power_w": 322.6,
        "gpu_power_limit_w": 370.0,
    }
