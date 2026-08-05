try:
    from enum import StrEnum
except ImportError:  # Python 3.10 compatibility for the Ubuntu 22.04 host Node Agent.
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        pass


class JobStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    UPLOADING = "UPLOADING"
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    DOWNLOADING = "DOWNLOADING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


class NodeMode(StrEnum):
    DISABLED = "DISABLED"
    RESERVED = "RESERVED"
    OVERFLOW = "OVERFLOW"
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"


class NodePool(StrEnum):
    PRIMARY = "PRIMARY"
    OVERFLOW = "OVERFLOW"


class NodeHealth(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class Priority(StrEnum):
    CRITICAL = "critical"
    NORMAL = "normal"
    BATCH = "batch"


class BatchStatus(StrEnum):
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    ASSEMBLING = "ASSEMBLING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class BatchItemStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.CANCELLED,
    JobStatus.TIMED_OUT,
    JobStatus.FAILED,
}

TERMINAL_BATCH_STATUSES = {
    BatchStatus.SUCCEEDED,
    BatchStatus.PARTIAL_SUCCESS,
    BatchStatus.CANCELLED,
    BatchStatus.FAILED,
}
