import asyncio
import time

import pytest

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from apps.scheduler.src.gpu_control_scheduler.main import event_loop_wake_delay


@pytest.mark.parametrize("woke_at", [100.0, 100.25, 100.5])
def test_event_loop_wake_delay_is_zero_before_or_at_deadline(woke_at: float) -> None:
    assert event_loop_wake_delay(100.0, 0.5, woke_at) == 0.0


def test_event_loop_wake_delay_reports_only_current_deadline_overshoot() -> None:
    assert event_loop_wake_delay(426.0, 0.5, 426.52) == pytest.approx(0.02)


def test_event_loop_wake_delay_does_not_include_prior_business_time() -> None:
    process_started_at = 100.0
    wait_started_at = 426.0

    delay = event_loop_wake_delay(wait_started_at, 0.5, 426.52)

    assert delay == pytest.approx(0.02)
    assert delay != pytest.approx(426.52 - (process_started_at + 0.5))


def test_event_loop_wake_delay_clamps_negative_timeout() -> None:
    assert event_loop_wake_delay(10.0, -1.0, 10.25) == pytest.approx(0.25)


async def test_event_loop_lag_monitor_observes_synchronous_business_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated = asyncio.Event()
    samples: list[float] = []

    class RecordingGauge:
        def set(self, value: float) -> None:
            samples.append(value)
            updated.set()

    monkeypatch.setattr(scheduler_main, "LOOP_LAG", RecordingGauge())
    scheduler = object.__new__(scheduler_main.Scheduler)
    scheduler.stop_event = asyncio.Event()
    monitor = asyncio.create_task(scheduler.monitor_event_loop_lag(0.01))

    try:
        await asyncio.sleep(0)
        time.sleep(0.05)  # noqa: ASYNC251 - deliberately simulate blocking business code
        await asyncio.wait_for(updated.wait(), timeout=1)

        assert samples[-1] >= 0.03
    finally:
        scheduler.stop_event.set()
        await monitor
