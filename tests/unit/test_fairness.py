from datetime import UTC, datetime, timedelta

from packages.gpu_control_core.models import Job
from packages.gpu_control_core.repository import choose_fair_job, priority_rank


def make_job(
    job_id: str, tenant: str, priority: str, created: datetime, pinned: bool = False
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
