from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

from .enums import NodeHealth, NodeMode, NodePool

SUBSTANCE_FENCE_LABEL = "substance_bake_fence_job_ids"
SUBSTANCE_LEGACY_FENCE_LABEL = "substance_bake_fence_job_id"
SUBSTANCE_PENDING_RESERVATION_LABEL = "substance_bake_pending_reservation"
SUBSTANCE_RECOVERY_REQUIRED_LABEL = "substance_bake_recovery_required"
SUBSTANCE_DRAIN_OWNER_LABEL = "substance_bake_drain_owner"
SUBSTANCE_DRAIN_OWNER = "asset-api"
SUBSTANCE_GPU_NODE_ID = "worker-3090-b"
SUBSTANCE_WORKER_ID = "asset-worker-3090-b-windows"
SUBSTANCE_WORKER_ID_PREFIX = f"{SUBSTANCE_WORKER_ID}-"
SUBSTANCE_MAX_PARALLEL = 4
SPECIALIZED_GPU_HOLD_SECONDS = 15 * 60
GPU_SPECIALIZATION_LABEL = "gpu_specialization"
GPU_CACHE_DRAIN_FAILED_LABEL = "gpu_cache_drain_failed"
MODELVIEW_INPAINT_NODE_ID = "worker-4070ti-animation-host-01"
MODELVIEW_INPAINT_WORKFLOW_KEY = "modelview-inpaint"
IMAGECLIP_WORKFLOW_KEY = "imageclip-rgba"
IMAGECLIP_INPAINT_PREEMPTION_CODE = "IMAGECLIP_PREEMPTED_FOR_INPAINT"
SUBSTANCE_SPECIALIZATION_KEY = "substance-bake"


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
    labels: dict[str, Any]


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


def substance_fence_job_ids(labels: object) -> list[str]:
    """Return the durable active Baker fence IDs, including the legacy shape."""
    if not isinstance(labels, dict):
        return []
    raw = labels.get(SUBSTANCE_FENCE_LABEL, [])
    job_ids = [str(value) for value in raw] if isinstance(raw, list) else []
    legacy = labels.get(SUBSTANCE_LEGACY_FENCE_LABEL)
    if legacy and str(legacy) not in job_ids:
        job_ids.append(str(legacy))
    return list(dict.fromkeys(job_ids))


def substance_pending_reservation(
    labels: object, now: datetime
) -> tuple[list[str], datetime | None]:
    """Parse a live production Baker reservation from a node label.

    Invalid or expired data deliberately returns no job IDs. This prevents a
    corrupt JSON label from draining a production GPU indefinitely.
    """
    if not isinstance(labels, dict):
        return [], None
    raw = labels.get(SUBSTANCE_PENDING_RESERVATION_LABEL)
    if not isinstance(raw, dict):
        return [], None
    raw_ids = raw.get("job_ids")
    raw_expiry = raw.get("expires_at")
    if not isinstance(raw_ids, list) or not isinstance(raw_expiry, str):
        return [], None
    try:
        expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return [], None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    if expires_at <= current:
        return [], expires_at
    job_ids = list(dict.fromkeys(str(value) for value in raw_ids if value))
    return job_ids, expires_at


def gpu_specialization(
    labels: object, now: datetime
) -> tuple[str | None, datetime | None]:
    """Return one live GPU specialization and its UTC expiry.

    Invalid or expired labels are treated as inactive. Callers that already
    hold the Node row lock may remove the stale label, but read-only ranking
    never mutates ORM state.
    """

    if not isinstance(labels, dict):
        return None, None
    raw = labels.get(GPU_SPECIALIZATION_LABEL)
    if not isinstance(raw, dict):
        return None, None
    key = raw.get("key")
    raw_expiry = raw.get("expires_at")
    if not isinstance(key, str) or not key or not isinstance(raw_expiry, str):
        return None, None
    try:
        expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    expires_at = (
        expires_at.replace(tzinfo=UTC)
        if expires_at.tzinfo is None
        else expires_at.astimezone(UTC)
    )
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    if expires_at <= current:
        return None, expires_at
    return key, expires_at


def refresh_gpu_specialization(
    node: NodeLike,
    key: str,
    now: datetime,
    *,
    owner: str,
) -> datetime:
    """Start or refresh the fixed 15-minute specialized-GPU window."""

    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    expires_at = current + timedelta(seconds=SPECIALIZED_GPU_HOLD_SECONDS)
    labels = dict(getattr(node, "labels", {}) or {})
    labels[GPU_SPECIALIZATION_LABEL] = {
        "key": key,
        "owner": owner,
        "started_at": current.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    node.labels = labels
    return expires_at


def substance_owned_drain_is_expired(node: NodeLike, now: datetime) -> bool:
    """Treat only an expired Asset API-owned drain as schedulable again."""
    labels = getattr(node, "labels", {})
    if not isinstance(labels, dict) or labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) != SUBSTANCE_DRAIN_OWNER:
        return False
    # Lease expiry is ambiguous: the native Baker may still be executing and
    # ComfyUI may not yet have recovered. Such a drain is intentionally
    # unbounded until explicit worker/node health evidence clears it.
    if labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL):
        return False
    if substance_fence_job_ids(labels):
        return False
    pending_ids, _ = substance_pending_reservation(labels, now)
    specialization, _ = gpu_specialization(labels, now)
    return not pending_ids and specialization != SUBSTANCE_SPECIALIZATION_KEY


def linux_asset_claim_allowed(
    node: NodeLike,
    now: datetime,
    *,
    substance_node_id: str = SUBSTANCE_GPU_NODE_ID,
) -> bool:
    """Return whether an independent Linux CPU Worker may claim on ``node``.

    GPU/ComfyUI health and occupancy are intentionally not considered: the
    AssetWorker heartbeat is the CPU-runtime authority.  Explicit operator
    maintenance is still authoritative.  The sole DRAINING exception is an
    Asset API-owned native Substance interlock on 3090-B, where Linux CPU work
    can continue without using the fenced GPU.
    """

    if node.manual_reserved:
        return False
    if node.mode in {NodeMode.ACTIVE.value, NodeMode.OVERFLOW.value}:
        return True
    if node.id != substance_node_id or node.mode != NodeMode.DRAINING.value:
        return False
    labels = getattr(node, "labels", {})
    if not isinstance(labels, dict) or labels.get(SUBSTANCE_DRAIN_OWNER_LABEL) != (
        SUBSTANCE_DRAIN_OWNER
    ):
        return False
    pending_ids, _ = substance_pending_reservation(labels, now)
    specialization, _ = gpu_specialization(labels, now)
    return bool(
        pending_ids
        or substance_fence_job_ids(labels)
        or labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL)
        or specialization == SUBSTANCE_SPECIALIZATION_KEY
    )


def base_exclusion(node: NodeLike, now: datetime, heartbeat_timeout_seconds: int) -> str | None:
    labels = getattr(node, "labels", {})
    if isinstance(labels, dict):
        if labels.get(GPU_CACHE_DRAIN_FAILED_LABEL):
            return "gpu_cache_drain_failed"
        # Substance's physical-GPU interlocks are authoritative even if an
        # administrator (or another control-plane writer) changes the mode
        # back to ACTIVE.  Mode is presentation/administrative state; these
        # durable labels are the mutual-exclusion fence.
        if labels.get(SUBSTANCE_RECOVERY_REQUIRED_LABEL):
            return "substance_recovery_required"
        if substance_fence_job_ids(labels):
            return "substance_fenced"
        pending_ids, _ = substance_pending_reservation(labels, now)
        if pending_ids:
            return "substance_reserved"
        specialization, _ = gpu_specialization(labels, now)
        if specialization == SUBSTANCE_SPECIALIZATION_KEY:
            return "substance_specialization"
    effective_mode = node.mode
    if node.mode == NodeMode.DRAINING.value and substance_owned_drain_is_expired(node, now):
        effective_mode = NodeMode.ACTIVE.value
    if effective_mode in {
        NodeMode.DISABLED.value,
        NodeMode.RESERVED.value,
        NodeMode.DRAINING.value,
    }:
        return f"mode_{effective_mode.lower()}"
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


def rank_nodes(
    nodes: Sequence[NodeLike],
    queue: QueueSnapshot,
    guard: OverflowGuard,
    heartbeat_timeout_seconds: int,
    now: datetime | None = None,
    preferred_node_ids: set[str] | None = None,
) -> tuple[list[NodeLike], dict[str, str]]:
    """Return every currently eligible node in scheduling order.

    Compatibility is deliberately not handled here because it is specific to
    the queued job.  Callers must try the full ordered list: the first healthy
    node may have no compatible queued work while a later node does.
    """
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
    preferred = preferred_node_ids or set()

    def order(candidates: list[NodeLike]) -> None:
        candidates.sort(
            key=lambda node: (
                0 if node.id in preferred else 1,
                node.last_assigned_at or datetime.min.replace(tzinfo=UTC),
                node.id,
            )
        )

    order(primary)
    order(overflow)
    # Preserve the existing primary-before-overflow policy while still making
    # overflow a compatibility fallback when no primary can claim queued work.
    return [*primary, *overflow], exclusions


def choose_node(
    nodes: Sequence[NodeLike],
    queue: QueueSnapshot,
    guard: OverflowGuard,
    heartbeat_timeout_seconds: int,
    now: datetime | None = None,
    preferred_node_ids: set[str] | None = None,
) -> tuple[NodeLike | None, dict[str, str]]:
    candidates, exclusions = rank_nodes(
        nodes,
        queue,
        guard,
        heartbeat_timeout_seconds,
        now,
        preferred_node_ids,
    )
    return (candidates[0] if candidates else None), exclusions
