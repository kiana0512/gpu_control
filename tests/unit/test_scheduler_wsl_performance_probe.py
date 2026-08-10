from datetime import UTC, datetime, timedelta

import pytest

from apps.scheduler.src.gpu_control_scheduler.main import (
    WSL_PERFORMANCE_REFERENCE_NODE_ID,
    WSL_PERFORMANCE_TARGET_NODE_ID,
    wsl_imageclip_performance_snapshot,
)


def rows(node_id: str, seconds: list[float], *, width: int = 1080, height: int = 1440):
    now = datetime.now(UTC)
    return [
        (node_id, width, height, now - timedelta(seconds=index), duration)
        for index, duration in enumerate(seconds)
    ]


def test_detects_sustained_wsl_slowdown_against_same_resolution_native_3090() -> None:
    snapshot = wsl_imageclip_performance_snapshot(
        rows(WSL_PERFORMANCE_TARGET_NODE_ID, [120, 130, 140, 150, 160])
        + rows(WSL_PERFORMANCE_REFERENCE_NODE_ID, [29, 30, 31, 32, 33])
    )

    assert snapshot is not None
    assert snapshot["target_median_seconds"] == 140
    assert snapshot["reference_median_seconds"] == 31
    assert float(snapshot["slowdown_ratio"]) == pytest.approx(140 / 31)
    assert snapshot["anomaly"] == 1


def test_recent_samples_clear_old_degraded_wsl_history_after_recovery() -> None:
    now = datetime.now(UTC)
    stale_slow = [
        (
            WSL_PERFORMANCE_TARGET_NODE_ID,
            1080,
            1440,
            now - timedelta(minutes=10, seconds=index),
            duration,
        )
        for index, duration in enumerate([120, 130, 140, 150, 160])
    ]
    recovered = rows(WSL_PERFORMANCE_TARGET_NODE_ID, [32.9, 33.0, 33.1, 33.2, 33.3])
    snapshot = wsl_imageclip_performance_snapshot(
        stale_slow
        + recovered
        + rows(WSL_PERFORMANCE_REFERENCE_NODE_ID, [30.4, 30.5, 30.6, 30.7, 30.8])
    )

    assert snapshot is not None
    assert snapshot["target_median_seconds"] == pytest.approx(33.1)
    assert float(snapshot["slowdown_ratio"]) == pytest.approx(33.1 / 30.6)
    assert snapshot["anomaly"] == 0


def test_one_cold_start_does_not_trigger_and_three_samples_are_required() -> None:
    assert (
        wsl_imageclip_performance_snapshot(
            rows(WSL_PERFORMANCE_TARGET_NODE_ID, [90, 33])
            + rows(WSL_PERFORMANCE_REFERENCE_NODE_ID, [31, 30])
        )
        is None
    )

    snapshot = wsl_imageclip_performance_snapshot(
        rows(WSL_PERFORMANCE_TARGET_NODE_ID, [90, 33, 32])
        + rows(WSL_PERFORMANCE_REFERENCE_NODE_ID, [31, 30, 29])
    )
    assert snapshot is not None
    assert snapshot["target_median_seconds"] == 33
    assert snapshot["anomaly"] == 0


def test_does_not_compare_different_resolutions() -> None:
    snapshot = wsl_imageclip_performance_snapshot(
        rows(WSL_PERFORMANCE_TARGET_NODE_ID, [120, 130, 140], width=2048, height=2048)
        + rows(WSL_PERFORMANCE_REFERENCE_NODE_ID, [29, 30, 31], width=1080, height=1440)
    )
    assert snapshot is None
