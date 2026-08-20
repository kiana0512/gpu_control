from types import SimpleNamespace

import pytest

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import Scheduler
from packages.comfy_client import ComfyError


def stats(*, total_mb: int = 12282, free_mb: int) -> dict[str, object]:
    return {
        "devices": [
            {
                "vram_total": total_mb * 1024 * 1024,
                "vram_free": free_mb * 1024 * 1024,
            }
        ]
    }


class RecoveringClient:
    def __init__(self, free_values: list[int]) -> None:
        self.free_values = iter(free_values)
        self.free_calls = 0
        self.stats_calls = 0

    async def queue(self) -> dict[str, list[object]]:
        return {"queue_running": [], "queue_pending": []}

    async def free(self) -> dict[str, bool]:
        self.free_calls += 1
        return {"unloaded": True}

    async def system_stats(self) -> dict[str, object]:
        self.stats_calls += 1
        return stats(free_mb=next(self.free_values))


async def test_cache_drain_waits_for_asynchronous_vram_recovery(monkeypatch) -> None:
    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(scheduler_main.asyncio, "sleep", no_delay)
    client = RecoveringClient([3300, 7200, 10600])
    evidence = await Scheduler.drain_free_and_validate(
        Scheduler.__new__(Scheduler), client, interrupt=False
    )

    assert client.free_calls == 1
    assert client.stats_calls == 3
    assert evidence["queue_empty"] is True
    assert evidence["vram"]["free_vram_mb"] == 10600


async def test_cache_drain_fails_closed_after_recovery_deadline(monkeypatch) -> None:
    clock = iter([0.0, 30.0])
    loop = SimpleNamespace(time=lambda: next(clock))
    monkeypatch.setattr(scheduler_main.asyncio, "get_running_loop", lambda: loop)
    client = RecoveringClient([3300])

    with pytest.raises(ComfyError, match="显存恢复未达到安全阈值"):
        await Scheduler.drain_free_and_validate(
            Scheduler.__new__(Scheduler), client, interrupt=False
        )
