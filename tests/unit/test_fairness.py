from datetime import UTC, datetime, timedelta

from packages.gpu_control_core.models import Job
from packages.gpu_control_core.repository import choose_fair_job, priority_rank


def make_job(
    job_id: str,
    tenant: str,
    priority: str,
    created: datetime,
    pinned: bool = False,
    batch_id: str | None = None,
) -> Job:
    return Job(
        id=job_id,
        tenant_id=tenant,
        workflow_key="fake",
        workflow_version="1",
        status="QUEUED",
        priority=priority,
        pinned=pinned,
        parameters={},
        request_hash="x",
        request_id="r",
        trace_id="t",
        job_dir="/tmp",
        batch_id=batch_id,
        created_at=created,
    )


def test_tenant_round_robin_prevents_starvation() -> None:
    now = datetime.now(UTC)
    jobs = [
        make_job("a1", "tenant-a", "normal", now - timedelta(seconds=3)),
        make_job("a2", "tenant-a", "normal", now - timedelta(seconds=2)),
        make_job("b1", "tenant-b", "normal", now - timedelta(seconds=1)),
    ]
    selected = choose_fair_job(
        jobs, {"tenant-a": now, "tenant-b": now - timedelta(hours=1)}, now, 300
    )
    assert selected is not None and selected.id == "b1"


def test_priority_aging_and_pin() -> None:
    now = datetime.now(UTC)
    assert priority_rank("batch", 600, 300) == 2
    jobs = [make_job("critical", "a", "critical", now), make_job("pinned", "b", "batch", now, True)]
    assert choose_fair_job(jobs, {}, now, 300).id == "pinned"


def test_three_different_sized_video_batches_receive_the_first_three_slots() -> None:
    now = datetime.now(UTC)
    jobs: list[Job] = []
    for batch_id, frame_count, age in (
        ("video-84", 84, timedelta(minutes=20)),
        ("video-97", 97, timedelta(minutes=2)),
        ("video-121", 121, timedelta(seconds=30)),
    ):
        jobs.extend(
            make_job(
                f"{batch_id}-{frame:04d}",
                "animation",
                "batch",
                now - age + timedelta(microseconds=frame),
                batch_id=batch_id,
            )
            for frame in range(frame_count)
        )
    batch_last: dict[str, datetime] = {}
    batch_active: dict[str, int] = {}
    selected_batches: list[str] = []

    for slot in range(3):
        selected = choose_fair_job(
            jobs,
            {"animation": now},
            now,
            300,
            batch_last,
            batch_active,
        )
        assert selected is not None and selected.batch_id is not None
        selected_batch = str(selected.batch_id)
        selected_batches.append(selected_batch)
        batch_active[selected_batch] = batch_active.get(selected_batch, 0) + 1
        batch_last[selected_batch] = now + timedelta(microseconds=slot)
        jobs.remove(selected)

    assert selected_batches == ["video-84", "video-97", "video-121"]


def test_batch_without_an_active_frame_gets_the_next_free_slot() -> None:
    now = datetime.now(UTC)
    jobs = [
        make_job("a", "animation", "batch", now - timedelta(minutes=10), batch_id="video-a"),
        make_job("b", "animation", "batch", now - timedelta(minutes=5), batch_id="video-b"),
        make_job("c", "animation", "batch", now - timedelta(minutes=1), batch_id="video-c"),
    ]
    selected = choose_fair_job(
        jobs,
        {"animation": now},
        now,
        300,
        {"video-a": now - timedelta(minutes=2), "video-b": now - timedelta(minutes=1)},
        {"video-a": 1, "video-b": 1, "video-c": 0},
    )
    assert selected is not None and selected.batch_id == "video-c"
