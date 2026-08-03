"""Cross-plane admission rules shared by the GPU and Asset APIs.

The caller must hold ``Database.acquire_global_admission_transaction_lock``
for the complete check-and-insert transaction.  Keeping the read here and the
lock acquisition at each API boundary makes idempotent replays possible before
the new-work gate is evaluated while preserving one lock order everywhere:
global admission, tenant, then any resource-specific row lock.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .enums import TERMINAL_BATCH_STATUSES, TERMINAL_JOB_STATUSES
from .models import ApiClient, AssetJob, Job, JobBatch

TERMINAL_ASSET_WORK_STATUSES = frozenset(
    {"SUCCEEDED", "WAITING_REVIEW", "REVIEW_REJECTED", "FAILED", "CANCELLED"}
)


async def client_is_load_test(session: AsyncSession, client_id: str) -> bool:
    """Read the current classification inside the admission transaction.

    Authentication can precede a large upload by minutes.  Re-reading here
    prevents an administrator's concurrent client-kind change from being
    evaluated using the stale identity object.  Missing identities are not
    load-test clients and therefore retain production treatment.
    """

    client_kind = await session.scalar(
        select(ApiClient.client_kind).where(ApiClient.id == client_id)
    )
    return client_kind == "test"


def _production_or_unknown_client() -> ColumnElement[bool]:
    """Treat missing/future client classifications as production, fail closed."""

    return or_(ApiClient.id.is_(None), ApiClient.client_kind != "test")


async def active_production_work_exists(session: AsyncSession) -> bool:
    """Return whether either control plane has non-terminal production work.

    Unknown job states are intentionally active because every query excludes
    only the explicit terminal set.  Jobs whose tenant/client row is missing
    are likewise production so a damaged identity join cannot admit load-test
    traffic into potentially occupied capacity.
    """

    active_job = await session.scalar(
        select(Job.id)
        .outerjoin(ApiClient, ApiClient.id == Job.tenant_id)
        .where(
            _production_or_unknown_client(),
            Job.status.not_in([status.value for status in TERMINAL_JOB_STATUSES]),
        )
        .limit(1)
    )
    if active_job is not None:
        return True

    active_batch = await session.scalar(
        select(JobBatch.id)
        .outerjoin(ApiClient, ApiClient.id == JobBatch.tenant_id)
        .where(
            _production_or_unknown_client(),
            JobBatch.status.not_in([status.value for status in TERMINAL_BATCH_STATUSES]),
        )
        .limit(1)
    )
    if active_batch is not None:
        return True

    active_asset_job = await session.scalar(
        select(AssetJob.id)
        .outerjoin(ApiClient, ApiClient.id == AssetJob.client_id)
        .where(
            _production_or_unknown_client(),
            AssetJob.status.not_in(TERMINAL_ASSET_WORK_STATUSES),
        )
        .limit(1)
    )
    return active_asset_job is not None
