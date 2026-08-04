from types import SimpleNamespace

import httpx

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import Scheduler
from packages.gpu_control_core.models import Node


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
    }
