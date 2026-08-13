from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.gpu_control_core.scheduling import (
    GPU_CACHE_DRAIN_FAILED_LABEL,
    MODELVIEW_INPAINT_WORKFLOW_KEY,
    SUBSTANCE_DRAIN_OWNER,
    SUBSTANCE_DRAIN_OWNER_LABEL,
    SUBSTANCE_FENCE_LABEL,
    SUBSTANCE_PENDING_RESERVATION_LABEL,
    SUBSTANCE_RECOVERY_REQUIRED_LABEL,
    SUBSTANCE_SPECIALIZATION_KEY,
    OverflowGuard,
    QueueSnapshot,
    choose_node,
    gpu_specialization,
    linux_asset_claim_allowed,
    refresh_gpu_specialization,
)
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
    labels: dict[str, object] = field(default_factory=dict)


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


def test_gpu_specialization_refreshes_to_exact_hard_expiry() -> None:
    now = datetime.now(UTC)
    node = FakeNode(
        "worker-4070ti-animation-host-01",
        "PRIMARY",
        "ACTIVE",
        last_heartbeat_at=now,
    )
    first_expiry = refresh_gpu_specialization(
        node,
        MODELVIEW_INPAINT_WORKFLOW_KEY,
        now,
        owner="gpu-api",
    )
    assert first_expiry == now + timedelta(minutes=15)
    assert gpu_specialization(node.labels, now) == (
        MODELVIEW_INPAINT_WORKFLOW_KEY,
        first_expiry,
    )
    assert gpu_specialization(node.labels, first_expiry) == (None, first_expiry)

    refreshed_at = now + timedelta(minutes=10)
    second_expiry = refresh_gpu_specialization(
        node,
        MODELVIEW_INPAINT_WORKFLOW_KEY,
        refreshed_at,
        owner="gpu-api",
    )
    assert second_expiry == refreshed_at + timedelta(minutes=15)
    assert second_expiry > first_expiry


def test_substance_specialization_blocks_only_gpu_and_hard_expires(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    node = FakeNode(
        "worker-3090-b",
        "PRIMARY",
        "DRAINING",
        last_heartbeat_at=now,
        labels={SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER},
    )
    refresh_gpu_specialization(
        node,
        SUBSTANCE_SPECIALIZATION_KEY,
        now,
        owner=SUBSTANCE_DRAIN_OWNER,
    )
    chosen, excluded = choose_node(
        [node], QueueSnapshot(1, 0), guard(tmp_path), 20, now
    )
    assert chosen is None
    assert excluded[node.id] == "substance_specialization"
    assert linux_asset_claim_allowed(node, now)

    assert gpu_specialization(node.labels, now)[1] == now + timedelta(minutes=5)

    after_expiry = now + timedelta(minutes=5, seconds=1)
    node.last_heartbeat_at = after_expiry
    chosen, excluded = choose_node(
        [node],
        QueueSnapshot(1, 0),
        guard(tmp_path),
        20,
        after_expiry,
    )
    assert chosen is node
    assert excluded == {}


def test_legacy_substance_specialization_is_clamped_to_five_minutes() -> None:
    now = datetime.now(UTC)
    node = FakeNode("worker-3090-b", "PRIMARY", "ACTIVE", last_heartbeat_at=now)
    node.labels = {
        "gpu_specialization": {
            "key": "substance-bake",
            "owner": "asset-api",
            "started_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=15)).isoformat(),
        }
    }

    assert gpu_specialization(node.labels, now) == (
        SUBSTANCE_SPECIALIZATION_KEY,
        now + timedelta(minutes=5),
    )
    assert gpu_specialization(node.labels, now + timedelta(minutes=5)) == (
        None,
        now + timedelta(minutes=5),
    )


def test_cache_drain_failure_is_a_hard_gpu_interlock(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    node = FakeNode("4070", "PRIMARY", "ACTIVE", last_heartbeat_at=now)
    node.labels = {
        GPU_CACHE_DRAIN_FAILED_LABEL: {
            "observed_at": now.isoformat(),
            "message": "unsafe VRAM recovery",
        }
    }
    chosen, excluded = choose_node(
        [node], QueueSnapshot(1, 0), guard(tmp_path), 20, now
    )
    assert chosen is None
    assert excluded[node.id] == "gpu_cache_drain_failed"


def test_pending_substance_reservation_blocks_until_its_deadline(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    available = nodes(now)
    reserved = available[0]
    reserved.mode = "DRAINING"
    reserved.labels = {
        SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER,
        SUBSTANCE_PENDING_RESERVATION_LABEL: {
            "job_ids": ["production-bake"],
            "expires_at": (now + timedelta(seconds=30)).isoformat(),
        },
    }

    chosen, excluded = choose_node(
        available[:1], QueueSnapshot(1, 0), guard(tmp_path), 60, now
    )
    assert chosen is None
    assert excluded[reserved.id] == "substance_reserved"

    chosen, excluded = choose_node(
        available[:1],
        QueueSnapshot(1, 0),
        guard(tmp_path),
        60,
        now + timedelta(seconds=31),
    )
    assert chosen is reserved
    assert excluded == {}


def test_active_substance_fence_never_expires_with_pending_reservation(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    reserved = nodes(now)[0]
    reserved.mode = "DRAINING"
    reserved.labels = {
        SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER,
        SUBSTANCE_FENCE_LABEL: ["running-bake"],
        SUBSTANCE_PENDING_RESERVATION_LABEL: {
            "job_ids": ["expired-pending-bake"],
            "expires_at": (now - timedelta(seconds=1)).isoformat(),
        },
    }

    chosen, excluded = choose_node(
        [reserved], QueueSnapshot(1, 0), guard(tmp_path), 20, now
    )
    assert chosen is None
    assert excluded[reserved.id] == "substance_fenced"


def test_substance_lease_recovery_interlock_never_expires(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    reserved = nodes(now)[0]
    reserved.mode = "DRAINING"
    reserved.labels = {
        SUBSTANCE_DRAIN_OWNER_LABEL: SUBSTANCE_DRAIN_OWNER,
        SUBSTANCE_RECOVERY_REQUIRED_LABEL: [
            {
                "job_id": "ambiguous-bake",
                "worker_id": "asset-worker-3090-b-windows-01",
                "lease_expired_at": (now - timedelta(days=1)).isoformat(),
            }
        ],
    }

    chosen, excluded = choose_node(
        [reserved],
        QueueSnapshot(1, 0),
        guard(tmp_path),
        20,
        now + timedelta(days=30),
    )
    assert chosen is None
    assert excluded[reserved.id] == "substance_recovery_required"


def test_substance_interlocks_cannot_be_bypassed_by_active_mode(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    cases = (
        (
            {
                SUBSTANCE_PENDING_RESERVATION_LABEL: {
                    "job_ids": ["queued-bake"],
                    "expires_at": (now + timedelta(minutes=5)).isoformat(),
                }
            },
            "substance_reserved",
        ),
        ({SUBSTANCE_FENCE_LABEL: ["running-bake"]}, "substance_fenced"),
        (
            {
                SUBSTANCE_RECOVERY_REQUIRED_LABEL: [
                    {
                        "job_id": "ambiguous-bake",
                        "worker_id": "asset-worker-3090-b-windows-01",
                        "lease_expired_at": now.isoformat(),
                    }
                ]
            },
            "substance_recovery_required",
        ),
    )
    for labels, expected in cases:
        node = nodes(now)[0]
        node.mode = "ACTIVE"
        node.labels = labels
        chosen, excluded = choose_node(
            [node], QueueSnapshot(1, 0), guard(tmp_path), 60, now
        )
        assert chosen is None
        assert excluded[node.id] == expected


def test_overflow_allowed_windows_parse_overnight() -> None:
    settings = Settings(overflow_4090_allowed_windows="22:00-06:00,12:30-13:15")
    assert settings.overflow_windows[0][0].hour == 22
    assert settings.overflow_windows[0][1].hour == 6
    assert settings.overflow_windows[1][0].minute == 30


def test_production_queue_reserve_is_backward_compatible_with_small_limits() -> None:
    default = Settings()
    assert default.system_max_queued == 500
    assert default.system_production_queue_reserve == 50
    assert default.test_system_max_queued == 450

    existing_small_limit = Settings(system_max_queued=4)
    assert existing_small_limit.test_system_max_queued == 0

    explicitly_tuned = Settings(
        system_max_queued=4,
        system_production_queue_reserve=2,
    )
    assert explicitly_tuned.test_system_max_queued == 2
