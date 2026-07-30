import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from packages.gpu_control_core.database import (
    Database,
    SchedulerLockLost,
    SchedulerLockUnavailable,
)
from packages.gpu_control_core.models import Base
from packages.gpu_control_core.settings import Settings


def postgres_url() -> str:
    url = os.environ.get("GPU_CONTROL_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("GPU_CONTROL_TEST_POSTGRES_URL is required for PostgreSQL lock tests")
    return url


def make_database(url: str, job_root: Path) -> Database:
    return Database(Settings(database_url=url, job_root=job_root))


async def test_scheduler_lock_is_idle_without_transaction_and_exclusive(tmp_path: Path) -> None:
    url = postgres_url()
    owner = make_database(url, tmp_path / "owner-jobs")
    contender = make_database(url, tmp_path / "contender-jobs")
    try:
        async with owner.scheduler_lock() as lock_handle:
            assert lock_handle.backend_pid is not None
            owner_pid = lock_handle.backend_pid
            await owner.assert_scheduler_lock(lock_handle)
            async with contender.engine.connect() as observer:
                activity = (
                    (
                        await observer.execute(
                            text(
                                "SELECT state, xact_start, backend_xmin "
                                "FROM pg_stat_activity WHERE pid = :pid"
                            ),
                            {"pid": owner_pid},
                        )
                    )
                    .mappings()
                    .one()
                )
            assert activity["state"] == "idle"
            assert activity["xact_start"] is None
            assert activity["backend_xmin"] is None

            with pytest.raises(SchedulerLockUnavailable):
                async with contender.scheduler_lock():
                    pytest.fail("a second scheduler must not enter while the lock is held")

        async with contender.scheduler_lock():
            pass
    finally:
        await owner.close()
        await contender.close()


async def test_scheduler_lock_detects_terminated_backend_and_allows_takeover(
    tmp_path: Path,
) -> None:
    url = postgres_url()
    owner = make_database(url, tmp_path / "owner-jobs")
    contender = make_database(url, tmp_path / "contender-jobs")
    try:
        with pytest.raises(SchedulerLockLost, match="liveness query failed"):
            async with owner.scheduler_lock() as lock_handle:
                assert lock_handle.backend_pid is not None
                async with contender.engine.connect() as terminator:
                    terminated = bool(
                        (
                            await terminator.execute(
                                text("SELECT pg_terminate_backend(:pid)"),
                                {"pid": lock_handle.backend_pid},
                            )
                        ).scalar_one()
                    )
                assert terminated
                await owner.assert_scheduler_lock(lock_handle)

        async with contender.scheduler_lock():
            pass
    finally:
        await owner.close()
        await contender.close()


async def test_scheduler_lock_detects_idle_session_disconnect_and_allows_takeover(
    tmp_path: Path,
) -> None:
    url = postgres_url()
    owner = make_database(url, tmp_path / "owner-jobs")
    contender = make_database(url, tmp_path / "contender-jobs")
    try:
        with pytest.raises(SchedulerLockLost, match="liveness query failed"):
            async with owner.scheduler_lock() as lock_handle:
                await lock_handle.connection.execute(
                    text("SET idle_session_timeout = '100ms'")
                )
                await asyncio.sleep(0.3)
                await owner.assert_scheduler_lock(lock_handle)

        async with contender.scheduler_lock():
            pass
    finally:
        await owner.close()
        await contender.close()


async def test_scheduler_lock_failure_invalidates_connection_and_releases_lock(
    tmp_path: Path,
) -> None:
    url = postgres_url()
    owner = make_database(url, tmp_path / "owner-jobs")
    contender = make_database(url, tmp_path / "contender-jobs")
    try:
        with pytest.raises(RuntimeError, match="scheduler loop failed"):
            async with owner.scheduler_lock():
                raise RuntimeError("scheduler loop failed")

        async with contender.scheduler_lock():
            pass
    finally:
        await owner.close()
        await contender.close()


async def test_takeover_epoch_waits_for_old_claim_and_rejects_late_claim(
    tmp_path: Path,
) -> None:
    """Prove the advisory handoff + row-lock commit barrier on PostgreSQL."""

    url = postgres_url()
    owner = make_database(url, tmp_path / "owner-jobs")
    contender = make_database(url, tmp_path / "contender-jobs")
    old_claim_session = owner.sessions()
    try:
        async with owner.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with owner.scheduler_lock():
            async with owner.session() as session:
                async with session.begin():
                    old_epoch = await owner.advance_scheduler_epoch(
                        session,
                        backend_pid=1,
                    )
            await old_claim_session.begin()
            await owner.assert_scheduler_epoch(old_claim_session, old_epoch)

        async def take_over() -> int:
            async with contender.scheduler_lock() as handle:
                async with contender.session() as session:
                    async with session.begin():
                        return await contender.advance_scheduler_epoch(
                            session,
                            backend_pid=handle.backend_pid,
                        )

        takeover = asyncio.create_task(take_over())
        await asyncio.sleep(0.1)
        assert not takeover.done()
        await old_claim_session.commit()
        new_epoch = await asyncio.wait_for(takeover, timeout=3)
        assert new_epoch == old_epoch + 1

        async with owner.session() as session:
            with pytest.raises(SchedulerLockLost, match="epoch changed"):
                async with session.begin():
                    await owner.assert_scheduler_epoch(session, old_epoch)
    finally:
        await old_claim_session.close()
        await owner.close()
        await contender.close()
