from apps.scheduler.src.gpu_control_scheduler.main import (
    monotonic_batch_progress,
    monotonic_job_progress,
)


def test_job_progress_does_not_regress_when_comfy_switches_nodes() -> None:
    assert monotonic_job_progress(70.0, 1.0, 10.0) == 70.0
    assert monotonic_job_progress(10.0, 7.0, 10.0) == 70.0
    assert monotonic_job_progress(0.0, 1000.0, 1.0) == 99.0


def test_batch_progress_does_not_regress_when_active_children_reset() -> None:
    assert monotonic_batch_progress(49.5, 6, 0, 2.0) == 49.5
    assert monotonic_batch_progress(49.5, 6, 3, 0.0) == 50.0
    assert monotonic_batch_progress(50.0, 6, 6, 0.0) == 100.0
