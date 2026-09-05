from types import SimpleNamespace

import httpx
import pytest

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import (
    Scheduler,
    wsl_system_state_snapshot,
)
from packages.gpu_control_core.models import Node


def valid_snapshot() -> dict[str, str | int | float | None]:
    return {
        "platform": "wsl2",
        "boot_id": "12345678-1234-4abc-8def-1234567890ab",
        "uptime_seconds": 125.5,
        "cpu_count": 8,
        "load_1m_per_cpu": 0.5,
        "memory_available_ratio": 0.25,
        "swap_used_ratio": 0.1,
        "cpu_pressure_some_avg10": 2.0,
        "memory_pressure_some_avg10": 1.0,
        "memory_pressure_full_avg10": 0.5,
        "io_pressure_some_avg10": 3.0,
        "io_pressure_full_avg10": 0.25,
    }


def scheduler_stub() -> Scheduler:
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.settings = SimpleNamespace(node_agent_secret=lambda _node_id: "secret")
    scheduler.wsl_system_metrics_retry_at = {}
    scheduler.wsl_system_metrics_checked_at = {}
    scheduler.wsl_system_metrics_cache = {}
    scheduler.wsl_boot_ids = {}
    return scheduler


def test_wsl_system_state_contract_rejects_untrusted_ranges() -> None:
    assert wsl_system_state_snapshot(valid_snapshot())["memory_available_ratio"] == 0.25
    invalid = valid_snapshot()
    invalid["memory_available_ratio"] = 1.5
    with pytest.raises(ValueError, match="metric range"):
        wsl_system_state_snapshot(invalid)


async def test_wsl_system_probe_is_signed_and_cached(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, str | int | float | None]:
            return valid_snapshot()

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> Response:
            calls.append((url, headers))
            return Response()

    monkeypatch.setattr(scheduler_main.httpx, "AsyncClient", Client)
    scheduler = scheduler_stub()
    node = Node(
        id="worker-3090-b",
        agent_url="http://10.3.34.14:9201",
        labels={"wsl_runtime": True},
    )

    first = await scheduler.node_agent_system_metrics(node)
    second = await scheduler.node_agent_system_metrics(node)

    assert first == second
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/system-metrics")
    assert calls[0][1]["X-GPU-Signature"]


async def test_wsl_system_probe_failure_is_advisory_and_backed_off(monkeypatch) -> None:
    calls = 0

    class FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("temporary WSL probe failure")

    monkeypatch.setattr(scheduler_main.httpx, "AsyncClient", FailingClient)
    scheduler = scheduler_stub()
    node = Node(
        id="worker-3090-b",
        agent_url="http://10.3.34.14:9201",
        labels={"wsl_runtime": True},
    )

    assert await scheduler.node_agent_system_metrics(node) is None
    assert await scheduler.node_agent_system_metrics(node) is None
    assert calls == 1
    assert scheduler.wsl_system_metrics_retry_at[node.id] > 0


async def test_wsl_system_probe_caches_each_wsl_node_independently(monkeypatch) -> None:
    calls: list[str] = []

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, str | int | float | None]:
            return valid_snapshot()

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> Response:
            calls.append(url)
            return Response()

    monkeypatch.setattr(scheduler_main.httpx, "AsyncClient", Client)
    scheduler = scheduler_stub()
    node_b = Node(
        id="worker-3090-b",
        agent_url="http://10.3.34.14:9201",
        labels={"wsl_runtime": True},
    )
    node_4070ti = Node(
        id="worker-4070ti-animation-host-01",
        agent_url="http://10.3.34.238:9201",
        labels={"wsl_runtime": True},
    )

    await scheduler.node_agent_system_metrics(node_b)
    await scheduler.node_agent_system_metrics(node_4070ti)
    await scheduler.node_agent_system_metrics(node_b)
    await scheduler.node_agent_system_metrics(node_4070ti)

    assert len(calls) == 2
    assert set(scheduler.wsl_system_metrics_cache) == {
        node_b.id,
        node_4070ti.id,
    }


async def test_wsl_system_probe_skips_native_nodes() -> None:
    scheduler = scheduler_stub()
    node = Node(
        id="worker-3090-a",
        agent_url="http://10.3.34.12:9201",
        labels={"wsl_runtime": False},
    )

    assert await scheduler.node_agent_system_metrics(node) is None
