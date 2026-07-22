from collections.abc import Mapping

from .enums import JobStatus

ALLOWED_TRANSITIONS: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.RECEIVED: frozenset({JobStatus.VALIDATING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.VALIDATING: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.QUEUED: frozenset({JobStatus.CLAIMED, JobStatus.CANCELLED}),
    JobStatus.CLAIMED: frozenset(
        {
            JobStatus.UPLOADING,
            JobStatus.RETRY_WAIT,
            JobStatus.CANCELLING,
            JobStatus.TIMED_OUT,
            JobStatus.FAILED,
        }
    ),
    JobStatus.UPLOADING: frozenset(
        {
            JobStatus.SUBMITTED,
            JobStatus.RETRY_WAIT,
            JobStatus.CANCELLING,
            JobStatus.TIMED_OUT,
            JobStatus.FAILED,
        }
    ),
    JobStatus.SUBMITTED: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.DOWNLOADING,
            JobStatus.CANCELLING,
            JobStatus.TIMED_OUT,
            JobStatus.RETRY_WAIT,
            JobStatus.FAILED,
        }
    ),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.DOWNLOADING,
            JobStatus.CANCELLING,
            JobStatus.TIMED_OUT,
            JobStatus.RETRY_WAIT,
            JobStatus.FAILED,
        }
    ),
    JobStatus.DOWNLOADING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.RETRY_WAIT, JobStatus.TIMED_OUT, JobStatus.FAILED}
    ),
    JobStatus.RETRY_WAIT: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.CANCELLING: frozenset({JobStatus.CANCELLED, JobStatus.TIMED_OUT, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.TIMED_OUT: frozenset({JobStatus.RETRY_WAIT, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.RETRY_WAIT}),
}


class InvalidTransition(ValueError):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        super().__init__(f"illegal job transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def require_transition(current: JobStatus | str, target: JobStatus | str) -> None:
    source = JobStatus(current)
    destination = JobStatus(target)
    if destination not in ALLOWED_TRANSITIONS[source]:
        raise InvalidTransition(source, destination)
