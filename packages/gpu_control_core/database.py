import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import SystemSetting
from .settings import Settings

SCHEDULER_LOCK_ID = 0x47504354
ADMISSION_LOCK_ID = 0x47504341444D4954
SCHEDULER_EPOCH_KEY = "scheduler_leader_epoch"
SCHEDULER_LOCK_CLASS_ID = (SCHEDULER_LOCK_ID >> 32) & 0xFFFFFFFF
SCHEDULER_LOCK_OBJECT_ID = SCHEDULER_LOCK_ID & 0xFFFFFFFF


class SchedulerLockUnavailable(RuntimeError):
    """Raised when another scheduler owns the singleton lock."""


class SchedulerLockReleaseError(RuntimeError):
    """Raised when PostgreSQL cannot prove that the singleton lock was released."""


class SchedulerLockLost(RuntimeError):
    """Raised when the scheduler's dedicated PostgreSQL session no longer owns its lock."""


@dataclass(frozen=True, slots=True)
class SchedulerLockHandle:
    """Identity of the physical connection that owns the scheduler lock."""

    connection: AsyncConnection
    backend_pid: int | None
    postgresql: bool
    query_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        compare=False,
        repr=False,
    )


class Database:
    def __init__(self, settings: Settings) -> None:
        kwargs: dict[str, object] = {"pool_pre_ping": True}
        if settings.database_url.startswith("postgresql"):
            kwargs.update(pool_size=10, max_overflow=20, pool_recycle=1800)
        self.engine: AsyncEngine = create_async_engine(settings.database_url, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def acquire_tenant_transaction_lock(self, session: AsyncSession, tenant_id: str) -> None:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
                {"tenant_id": tenant_id},
            )

    async def acquire_global_admission_transaction_lock(self, session: AsyncSession) -> None:
        """Serialize global admission before tenant locks with a distinct 64-bit key."""

        if session.bind is not None and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": ADMISSION_LOCK_ID},
            )

    async def acquire_scheduler_lock(self, connection: AsyncConnection) -> bool:
        if connection.dialect.name == "postgresql":
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": SCHEDULER_LOCK_ID}
            )
            return bool(result.scalar_one())
        return True

    async def release_scheduler_lock(self, connection: AsyncConnection) -> bool:
        if connection.dialect.name == "postgresql":
            result = await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": SCHEDULER_LOCK_ID}
            )
            return bool(result.scalar_one())
        return True

    async def assert_scheduler_lock(self, handle: SchedulerLockHandle) -> None:
        """Prove that the original backend still owns the exact advisory lock.

        This query deliberately runs on the same dedicated connection used to
        acquire the session lock.  Checking both ``pg_backend_pid()`` and the
        matching ``pg_locks`` row detects a severed/reconnected session as well
        as an advisory lock that disappeared while the connection stayed usable.
        """

        if not handle.postgresql:
            return
        if handle.backend_pid is None:
            raise SchedulerLockLost("PostgreSQL scheduler lock has no backend identity")
        try:
            # The monitor and pre-claim checks share one dedicated physical
            # connection. AsyncConnection does not permit concurrent execute;
            # serialize every liveness query on the handle itself.
            async with handle.query_lock:
                result = await handle.connection.execute(
                    text(
                        "SELECT ("
                        "pg_backend_pid() = :backend_pid AND EXISTS ("
                        "SELECT 1 FROM pg_locks "
                        "WHERE locktype = 'advisory' "
                        "AND pid = :backend_pid "
                        "AND granted "
                        "AND classid::bigint = :lock_class_id "
                        "AND objid::bigint = :lock_object_id "
                        "AND objsubid = 1"
                        ")"
                        ")"
                    ),
                    {
                        "backend_pid": handle.backend_pid,
                        "lock_class_id": SCHEDULER_LOCK_CLASS_ID,
                        "lock_object_id": SCHEDULER_LOCK_OBJECT_ID,
                    },
                )
                owned = bool(result.scalar_one())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise SchedulerLockLost(
                "scheduler advisory lock liveness query failed"
            ) from exc
        if not owned:
            raise SchedulerLockLost(
                "scheduler PostgreSQL backend no longer owns the advisory lock"
            )

    async def advance_scheduler_epoch(
        self,
        session: AsyncSession,
        *,
        backend_pid: int | None,
    ) -> int:
        """Fence a new leader behind every claim made by the previous epoch.

        ``FOR UPDATE`` conflicts with the ``FOR KEY SHARE`` held by each claim
        transaction. A takeover therefore cannot advance the epoch or run
        reconciliation until every old in-flight claim has committed/rolled
        back. Claims that begin afterwards observe the new epoch and fail.
        """

        setting = await session.scalar(
            select(SystemSetting)
            .where(SystemSetting.key == SCHEDULER_EPOCH_KEY)
            .with_for_update()
        )
        if setting is None:
            setting = SystemSetting(
                key=SCHEDULER_EPOCH_KEY,
                value={"epoch": 0},
                version=0,
                updated_by="scheduler",
            )
            session.add(setting)
            await session.flush()
        if not isinstance(setting.value, dict):
            raise SchedulerLockLost("scheduler epoch row is invalid")
        raw_epoch = setting.value.get("epoch", 0)
        try:
            previous_epoch = int(raw_epoch)
        except (TypeError, ValueError) as exc:
            raise SchedulerLockLost("scheduler epoch row is invalid") from exc
        epoch = previous_epoch + 1
        setting.value = {
            "epoch": epoch,
            "backend_pid": backend_pid,
        }
        setting.version = int(setting.version or 0) + 1
        setting.updated_by = "scheduler"
        await session.flush()
        return epoch

    async def assert_scheduler_epoch(
        self,
        session: AsyncSession,
        expected_epoch: int,
    ) -> None:
        """Hold the epoch row through commit and reject a fenced scheduler."""

        setting = await session.scalar(
            select(SystemSetting)
            .where(SystemSetting.key == SCHEDULER_EPOCH_KEY)
            .with_for_update(read=True, key_share=True)
        )
        if setting is None:
            raise SchedulerLockLost("scheduler epoch row is missing")
        if not isinstance(setting.value, dict):
            raise SchedulerLockLost("scheduler epoch row is invalid")
        raw_epoch = setting.value.get("epoch")
        if isinstance(raw_epoch, bool) or not isinstance(raw_epoch, int | str):
            raise SchedulerLockLost("scheduler epoch row is invalid")
        try:
            observed_epoch = int(raw_epoch)
        except (TypeError, ValueError) as exc:
            raise SchedulerLockLost("scheduler epoch row is invalid") from exc
        if observed_epoch != expected_epoch:
            raise SchedulerLockLost(
                f"scheduler epoch changed from {expected_epoch} to {observed_epoch}"
            )

    @staticmethod
    async def _invalidate_scheduler_connection(connection: AsyncConnection) -> None:
        """Finish invalidation even while the scheduler task is being cancelled."""

        invalidation = asyncio.create_task(connection.invalidate())
        cancellation: asyncio.CancelledError | None = None
        while not invalidation.done():
            try:
                await asyncio.shield(invalidation)
            except asyncio.CancelledError as exc:
                cancellation = exc
        await invalidation
        if cancellation is not None:
            raise cancellation

    @asynccontextmanager
    async def scheduler_lock(self) -> AsyncIterator[SchedulerLockHandle]:
        """Hold the scheduler advisory lock on one dedicated physical connection.

        PostgreSQL session advisory locks belong to a physical connection, not an
        ORM transaction.  AUTOCOMMIT prevents the lock-holder from retaining an
        idle transaction and its vacuum horizon for the scheduler lifetime.
        Abnormal exits invalidate the connection because an interrupted unlock
        cannot safely prove the server-side lock state.
        """

        connection = await self.engine.connect()
        postgres = connection.dialect.name == "postgresql"
        acquired = False
        handle = SchedulerLockHandle(
            connection=connection,
            backend_pid=None,
            postgresql=postgres,
        )
        try:
            if postgres:
                try:
                    connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                    acquired = await self.acquire_scheduler_lock(connection)
                except BaseException:
                    await self._invalidate_scheduler_connection(connection)
                    raise
                if not acquired:
                    raise SchedulerLockUnavailable(
                        "another scheduler owns the PostgreSQL advisory lock"
                    )
                try:
                    backend_pid = int(
                        (
                            await connection.execute(text("SELECT pg_backend_pid()"))
                        ).scalar_one()
                    )
                    handle = SchedulerLockHandle(
                        connection=connection,
                        backend_pid=backend_pid,
                        postgresql=True,
                    )
                    await self.assert_scheduler_lock(handle)
                except BaseException:
                    await self._invalidate_scheduler_connection(connection)
                    raise
            else:
                handle = SchedulerLockHandle(
                    connection=connection,
                    backend_pid=None,
                    postgresql=False,
                )

            try:
                yield handle
            except BaseException:
                if postgres and acquired:
                    await self._invalidate_scheduler_connection(connection)
                raise

            if postgres and acquired:
                try:
                    released = await self.release_scheduler_lock(connection)
                except BaseException:
                    await self._invalidate_scheduler_connection(connection)
                    raise
                if not released:
                    await self._invalidate_scheduler_connection(connection)
                    raise SchedulerLockReleaseError(
                        "PostgreSQL did not confirm scheduler advisory lock release"
                    )
        finally:
            await connection.close()

    async def close(self) -> None:
        await self.engine.dispose()
