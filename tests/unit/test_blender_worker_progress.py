import pytest
from gpu_control_blender_worker.main import (
    DIRECT_V2_ESTIMATED_STAGE_SECONDS,
    stage_eta_for_elapsed,
    stage_progress_for_elapsed,
)


def test_direct_v2_eta_is_distinct_from_hard_timeout() -> None:
    assert DIRECT_V2_ESTIMATED_STAGE_SECONDS == 720


def test_stage_progress_does_not_reach_completion_boundary_while_running() -> None:
    assert stage_progress_for_elapsed(8, 92, 0, 600) == 8
    assert stage_progress_for_elapsed(8, 92, 300, 600) == pytest.approx(50)
    assert stage_progress_for_elapsed(8, 92, 3600, 600) == pytest.approx(87.8)


def test_stage_eta_becomes_unknown_after_normal_window() -> None:
    assert stage_eta_for_elapsed(600, 15) == 585
    assert stage_eta_for_elapsed(600, 600) is None
    assert stage_eta_for_elapsed(600, 3600) is None
