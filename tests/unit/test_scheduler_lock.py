import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.scheduler.src.gpu_control_scheduler import main as scheduler_main
from packages.gpu_control_core.database import (
    SCHEDULER_LOCK_CLASS_ID,
    SCHEDULER_LOCK_ID,
    SCHEDULER_LOCK_OBJECT_ID,
    Database,
    SchedulerLockHandle,
    SchedulerLockLost,
    SchedulerLockReleaseError,
    SchedulerLockUnavailable,
)
from packages.gpu_control_core.models import Base
from packages.gpu_control_core.settings import Settings


class ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


def postgresql_database(*results: object) -> tuple[Database, MagicMock, MagicMock]:
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    connection.execution_options = AsyncMock(return_value=connection)
    connection.execute = AsyncMock(side_effect=[ScalarResult(value) for value in results])
    connection.invalidate = AsyncMock()
    connection.close = AsyncMock()

    engine = MagicMock()
    engine.connect = AsyncMock(return_value=connection)

    database = object.__new__(Database)
    database.engine = cast(AsyncEngine, engine)
    return database, engine, connection


async def test_scheduler_lock_uses_one_autocommit_connection_for_lock_and_unlock() -> None:
    database, engine, connection = postgresql_database(True, 4242, True, True)

    async with database.scheduler_lock() as handle:
        assert handle.connection is connection
        assert handle.backend_pid == 4242
        assert handle.postgresql is True

    engine.connect.assert_awaited_once_with()
    connection.execution_options.assert_awaited_once_with(isolation_level="AUTOCOMMIT")
    assert connection.execute.await_count == 4
    acquire_call, pid_call, ownership_call, release_call = connection.execute.await_args_list
    assert str(acquire_call.args[0]) == "SELECT pg_try_advisory_lock(:lock_id)"
    assert str(pid_call.args[0]) == "SELECT pg_backend_pid()"
    ownership_sql = str(ownership_call.args[0])
    assert "pg_backend_pid() = :backend_pid" in ownership_sql
    assert "FROM pg_locks" in ownership_sql
    assert "objsubid = 1" in ownership_sql
    assert str(release_call.args[0]) == "SELECT pg_advisory_unlock(:lock_id)"
    assert acquire_call.args[1] == {"lock_id": SCHEDULER_LOCK_ID}
    assert ownership_call.args[1] == {
        "backend_pid": 4242,
        "lock_class_id": SCHEDULER_LOCK_CLASS_ID,
        "lock_object_id": SCHEDULER_LOCK_OBJECT_ID,
    }
    assert release_call.args[1] == {"lock_id": SCHEDULER_LOCK_ID}
    connection.invalidate.assert_not_awaited()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_fails_closed_when_another_scheduler_owns_it() -> None:
    database, _engine, connection = postgresql_database(False)

    with pytest.raises(SchedulerLockUnavailable, match="another scheduler owns"):
        async with database.scheduler_lock():
            pytest.fail("unavailable scheduler lock must not enter the protected body")

    connection.invalidate.assert_not_awaited()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_invalidates_connection_on_protected_body_failure() -> None:
    database, _engine, connection = postgresql_database(True, 4242, True)

    with pytest.raises(RuntimeError, match="scheduler loop failed"):
        async with database.scheduler_lock():
            raise RuntimeError("scheduler loop failed")

    assert connection.execute.await_count == 3
    connection.invalidate.assert_awaited_once_with()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_invalidates_connection_on_cancellation() -> None:
    database, _engine, connection = postgresql_database(True, 4242, True)

    with pytest.raises(asyncio.CancelledError):
        async with database.scheduler_lock():
            raise asyncio.CancelledError

    assert connection.execute.await_count == 3
    connection.invalidate.assert_awaited_once_with()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_invalidates_connection_when_acquire_is_ambiguous() -> None:
    database, _engine, connection = postgresql_database()
    connection.execute.side_effect = RuntimeError("connection lost after lock request")

    with pytest.raises(RuntimeError, match="connection lost"):
        async with database.scheduler_lock():
            pytest.fail("ambiguous lock acquisition must fail closed")

    connection.invalidate.assert_awaited_once_with()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_requires_confirmed_unlock() -> None:
    database, _engine, connection = postgresql_database(True, 4242, True, False)

    with pytest.raises(SchedulerLockReleaseError, match="did not confirm"):
        async with database.scheduler_lock():
            pass

    connection.invalidate.assert_awaited_once_with()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_invalidates_connection_when_unlock_is_ambiguous() -> None:
    database, _engine, connection = postgresql_database()
    connection.execute.side_effect = [
        ScalarResult(True),
        ScalarResult(4242),
        ScalarResult(True),
        RuntimeError("connection lost after unlock request"),
    ]

    with pytest.raises(RuntimeError, match="connection lost"):
        async with database.scheduler_lock():
            pass

    connection.invalidate.assert_awaited_once_with()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_fails_closed_when_initial_ownership_check_fails() -> None:
    database, _engine, connection = postgresql_database(True, 4242, False)

    with pytest.raises(SchedulerLockLost, match="no longer owns"):
        async with database.scheduler_lock():
            pytest.fail("unverified scheduler lock must not enter the protected body")

    connection.invalidate.assert_awaited_once_with()
    connection.close.assert_awaited_once_with()


async def test_scheduler_lock_liveness_query_failure_is_normalized() -> None:
    database, _engine, connection = postgresql_database()
    connection.execute.side_effect = RuntimeError("backend disconnected")
    handle = SchedulerLockHandle(connection=connection, backend_pid=4242, postgresql=True)

    with pytest.raises(SchedulerLockLost, match="liveness query failed") as captured:
        await database.assert_scheduler_lock(handle)

    assert isinstance(captured.value.__cause__, RuntimeError)


async def test_scheduler_epoch_increments_and_rejects_stale_owner_on_sqlite(
    tmp_path: Path,
) -> None:
    database = Database(
        Settings(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'epoch.db').as_posix()}",
            job_root=tmp_path / "jobs",
        )
    )
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            async with session.begin():
                first = await database.advance_scheduler_epoch(session, backend_pid=1)
        async with database.session() as session:
            async with session.begin():
                await database.assert_scheduler_epoch(session, first)
        async with database.session() as session:
            async with session.begin():
                second = await database.advance_scheduler_epoch(session, backend_pid=2)
        assert second == first + 1
        async with database.session() as session:
            with pytest.raises(SchedulerLockLost, match="epoch changed"):
                async with session.begin():
                    await database.assert_scheduler_epoch(session, first)
    finally:
        await database.close()


def lock_monitor_scheduler() -> scheduler_main.Scheduler:
    scheduler = object.__new__(scheduler_main.Scheduler)
    scheduler.stop_event = asyncio.Event()
    scheduler.wakeup = asyncio.Event()
    scheduler.executions = {}
    scheduler.batch_assemblies = {}
    scheduler.scheduler_lock_failure = None
    scheduler.db = MagicMock()
    return scheduler


async def test_scheduler_lock_monitor_stops_and_cancels_active_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = lock_monitor_scheduler()
    scheduler.db.assert_scheduler_lock = AsyncMock(side_effect=SchedulerLockLost("lock lost"))
    monkeypatch.setattr(scheduler_main, "SCHEDULER_LOCK_CHECK_INTERVAL_SECONDS", 0.001)
    active = asyncio.create_task(asyncio.Event().wait())
    scheduler.executions["job-1"] = active
    handle = SchedulerLockHandle(
        connection=MagicMock(), backend_pid=4242, postgresql=True
    )

    with pytest.raises(SchedulerLockLost, match="lock lost"):
        await asyncio.wait_for(scheduler.monitor_scheduler_lock(handle), timeout=1)

    assert scheduler.stop_event.is_set()
    assert scheduler.wakeup.is_set()
    assert scheduler.scheduler_lock_failure is not None
    await asyncio.gather(active, return_exceptions=True)
    assert active.cancelled()


async def test_scheduler_lock_failure_cleanup_is_bounded() -> None:
    release = asyncio.Event()

    async def cancellation_resistant_task() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(cancellation_resistant_task())
    await asyncio.sleep(0)
    started = asyncio.get_running_loop().time()

    pending = await scheduler_main.Scheduler.cancel_tasks_bounded([task], 0.01)

    elapsed = asyncio.get_running_loop().time() - started
    assert pending == 1
    assert elapsed < 0.2
    release.set()
    await task


async def test_commit_as_leader_does_not_retain_epoch_after_commit() -> None:
    scheduler = object.__new__(scheduler_main.Scheduler)
    scheduler.assert_scheduler_epoch = AsyncMock()
    session = MagicMock()
    session.commit = AsyncMock()

    await scheduler.commit_as_leader(session)

    scheduler.assert_scheduler_epoch.assert_awaited_once_with(session)
    session.commit.assert_awaited_once_with()
