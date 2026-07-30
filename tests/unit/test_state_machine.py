import pytest

from packages.gpu_control_core.enums import JobStatus
from packages.gpu_control_core.state_machine import InvalidTransition, require_transition


@pytest.mark.parametrize(
    "source,target",
    [
        (JobStatus.RECEIVED, JobStatus.VALIDATING),
        (JobStatus.QUEUED, JobStatus.CLAIMED),
        (JobStatus.SUBMITTED, JobStatus.RUNNING),
        (JobStatus.DOWNLOADING, JobStatus.SUCCEEDED),
        (JobStatus.RUNNING, JobStatus.CANCELLING),
        (JobStatus.DOWNLOADING, JobStatus.CANCELLING),
        (JobStatus.CANCELLING, JobStatus.CANCELLED),
    ],
)
def test_legal_transitions(source: JobStatus, target: JobStatus) -> None:
    require_transition(source, target)


@pytest.mark.parametrize(
    "source,target",
    [
        (JobStatus.QUEUED, JobStatus.SUCCEEDED),
        (JobStatus.SUCCEEDED, JobStatus.QUEUED),
        (JobStatus.CANCELLED, JobStatus.RUNNING),
    ],
)
def test_illegal_transitions(source: JobStatus, target: JobStatus) -> None:
    with pytest.raises(InvalidTransition):
        require_transition(source, target)


@pytest.mark.parametrize(
    "source",
    [
        JobStatus.CLAIMED,
        JobStatus.UPLOADING,
        JobStatus.SUBMITTED,
        JobStatus.RUNNING,
        JobStatus.DOWNLOADING,
        JobStatus.CANCELLING,
    ],
)
def test_active_states_can_time_out(source: JobStatus) -> None:
    require_transition(source, JobStatus.TIMED_OUT)
