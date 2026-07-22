from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Protocol

from .enums import NodeHealth, NodeMode, NodePool


class NodeLike(Protocol):
    id: str
    pool: str
    mode: str
    health: str
    current_jobs: int
    max_concurrency: int
    manual_reserved: bool
    external_busy: bool
    foreign_queue_detected: bool
    gpu_util_percent: float
    free_vram_mb: int
    last_heartbeat_at: datetime | None
    last_assigned_at: datetime | None


@dataclass(frozen=True)
class QueueSnapshot:
    depth: int
    oldest_wait_seconds: float


@dataclass(frozen=True)
class OverflowGuard:
    queue_threshold: int
    wait_threshold_seconds: int
    max_gpu_util_percent: float
    min_free_vram_mb: int
    sentinel: Path
    auto_enabled: bool = False
    allowed_windows: tuple[tuple[time, time], ...] = ()


def _in_allowed_window(now: datetime, windows: tuple[tuple[time, time], ...]) -> bool:
    if not windows:
        return True
    current = now.timetz().replace(tzinfo=None)
    return any(
        start <= current <= end if start <= end else current >= start or current <= end
        for start, end in windows
    )


def base_exclusion(node: NodeLike, now: datetime, heartbeat_timeout_seconds: int) -> str | None:
    if node.mode in {NodeMode.DISABLED.value, NodeMode.RESERVED.value, NodeMode.DRAINING.value}:
        return f"mode_{node.mode.lower()}"
    if node.health != NodeHealth.ONLINE.value:
        return f"health_{node.health.lower()}"
    if node.last_heartbeat_at is None:
        return "no_heartbeat"
    heartbeat = (
        node.last_heartbeat_at
        if node.last_heartbeat_at.tzinfo
        else node.last_heartbeat_at.replace(tzinfo=UTC)
    )
    if (now - heartbeat).total_seconds() > heartbeat_timeout_seconds:
        return "heartbeat_expired"
    if node.current_jobs >= node.max_concurrency:
        return "no_slot"
    if node.foreign_queue_detected:
        return "foreign_comfy_queue"
    if node.external_busy:
        return "external_busy"
    if node.manual_reserved:
        return "manual_reserved"
    return None


def overflow_exclusion(
    node: NodeLike, queue: QueueSnapshot, guard: OverflowGuard, now: datetime
) -> str | None:
    if node.mode == NodeMode.ACTIVE.value:
        return None
    if node.mode != NodeMode.OVERFLOW.value:
        return f"mode_{node.mode.lower()}"
    if not guard.auto_enabled:
        return "overflow_auto_disabled"
    if guard.sentinel.exists():
        return "sentinel_reserved"
    if (
        queue.depth < guard.queue_threshold
        and queue.oldest_wait_seconds < guard.wait_threshold_seconds
    ):
        return "overflow_threshold_not_met"
    if node.gpu_util_percent >= guard.max_gpu_util_percent:
        return "gpu_util_guard"
    if node.free_vram_mb < guard.min_free_vram_mb:
        return "vram_guard"
    if not _in_allowed_window(now, guard.allowed_windows):
        return "outside_allowed_window"
    return None


def choose_node(
    nodes: Sequence[NodeLike],
    queue: QueueSnapshot,
    guard: OverflowGuard,
    heartbeat_timeout_seconds: int,
    now: datetime | None = None,
) -> tuple[NodeLike | None, dict[str, str]]:
    current = now or datetime.now(UTC)
    exclusions: dict[str, str] = {}
    primary: list[NodeLike] = []
    overflow: list[NodeLike] = []
    for node in nodes:
        reason = base_exclusion(node, current, heartbeat_timeout_seconds)
        if reason:
            exclusions[node.id] = reason
            continue
        if node.pool == NodePool.PRIMARY.value:
            primary.append(node)
        else:
            reason = overflow_exclusion(node, queue, guard, current)
            if reason:
                exclusions[node.id] = reason
            else:
                overflow.append(node)
    candidates = primary if primary else overflow
    candidates.sort(
        key=lambda node: (node.last_assigned_at or datetime.min.replace(tzinfo=UTC), node.id)
    )
    return (candidates[0] if candidates else None), exclusions
