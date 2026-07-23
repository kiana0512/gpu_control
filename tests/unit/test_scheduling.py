from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.gpu_control_core.scheduling import OverflowGuard, QueueSnapshot, choose_node
from packages.gpu_control_core.settings import Settings


@dataclass
class FakeNode:
    id: str
    pool: str
    mode: str
    health: str = "ONLINE"
    current_jobs: int = 0
    max_concurrency: int = 1
    manual_reserved: bool = False
    external_busy: bool = False
    foreign_queue_detected: bool = False
    gpu_util_percent: float = 0
    free_vram_mb: int = 24000
    last_heartbeat_at: datetime | None = None
    last_assigned_at: datetime | None = None


def guard(tmp_path: Path, enabled: bool = True) -> OverflowGuard:
    return OverflowGuard(20, 120, 20, 20000, tmp_path / "reserved", enabled)


def nodes(now: datetime) -> list[FakeNode]:
    return [
        FakeNode("3090-a", "PRIMARY", "ACTIVE", last_heartbeat_at=now),
        FakeNode("3090-b", "PRIMARY", "ACTIVE", last_heartbeat_at=now),
        FakeNode("4090", "OVERFLOW", "RESERVED", last_heartbeat_at=now),
    ]


def test_primary_3090_is_always_preferred(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    available[2].mode = "ACTIVE"
    chosen, _ = choose_node(available, QueueSnapshot(100, 1000), guard(tmp_path), 20, now)
    assert chosen is not None and chosen.id.startswith("3090")


def test_warm_cache_affinity_is_preferred_but_never_blocks_fallback(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    available[0].last_assigned_at = now - timedelta(seconds=60)
    available[1].last_assigned_at = now
    chosen, _ = choose_node(
        available,
        QueueSnapshot(1, 0),
        guard(tmp_path),
        20,
        now,
        preferred_node_ids={"3090-b"},
    )
    assert chosen is not None and chosen.id == "3090-b"

    available[1].current_jobs = 1
    chosen, excluded = choose_node(
        available,
        QueueSnapshot(1, 0),
        guard(tmp_path),
        20,
        now,
        preferred_node_ids={"3090-b"},
    )
    assert chosen is not None and chosen.id == "3090-a"
    assert excluded["3090-b"] == "no_slot"


def test_4090_reserved_never_schedules(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    available[0].current_jobs = available[1].current_jobs = 1
    chosen, excluded = choose_node(available, QueueSnapshot(100, 1000), guard(tmp_path), 20, now)
    assert chosen is None and excluded["4090"] == "mode_reserved"


def test_overflow_requires_threshold_and_all_guards(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    available[0].current_jobs = available[1].current_jobs = 1
    available[2].mode = "OVERFLOW"
    chosen, _ = choose_node(available, QueueSnapshot(20, 0), guard(tmp_path), 20, now)
    assert chosen is not None and chosen.id == "4090"
    available[2].gpu_util_percent = 20
    chosen, excluded = choose_node(available, QueueSnapshot(20, 0), guard(tmp_path), 20, now)
    assert chosen is None and excluded["4090"] == "gpu_util_guard"


def test_manual_reserve_and_sentinel_have_priority(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    available[0].current_jobs = available[1].current_jobs = 1
    available[2].mode = "OVERFLOW"
    available[2].manual_reserved = True
    chosen, excluded = choose_node(available, QueueSnapshot(100, 1000), guard(tmp_path), 20, now)
    assert chosen is None and excluded["4090"] == "manual_reserved"
    available[2].manual_reserved = False
    (tmp_path / "reserved").write_text("1")
    chosen, excluded = choose_node(available, QueueSnapshot(100, 1000), guard(tmp_path), 20, now)
    assert chosen is None and excluded["4090"] == "sentinel_reserved"


def test_drain_foreign_queue_and_expired_heartbeat_block_node(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    available[0].mode = "DRAINING"
    available[1].foreign_queue_detected = True
    available[2].mode = "ACTIVE"
    available[2].last_heartbeat_at = now - timedelta(seconds=21)
    chosen, excluded = choose_node(available, QueueSnapshot(100, 1000), guard(tmp_path), 20, now)
    assert chosen is None
    assert excluded == {
        "3090-a": "mode_draining",
        "3090-b": "foreign_comfy_queue",
        "4090": "heartbeat_expired",
    }


def test_overflow_allowed_windows_parse_overnight() -> None:
    settings = Settings(overflow_4090_allowed_windows="22:00-06:00,12:30-13:15")
    assert settings.overflow_windows[0][0].hour == 22
    assert settings.overflow_windows[0][1].hour == 6
    assert settings.overflow_windows[1][0].minute == 30
