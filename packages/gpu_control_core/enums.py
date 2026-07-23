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


TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.CANCELLED,
    JobStatus.TIMED_OUT,
    JobStatus.FAILED,
}
