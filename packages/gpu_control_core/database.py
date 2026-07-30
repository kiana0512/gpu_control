from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .settings import Settings

SCHEDULER_LOCK_ID = 0x47504354
ADMISSION_LOCK_ID = 0x47504341444D4954


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

    async def acquire_scheduler_lock(self, session: AsyncSession) -> bool:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            result = await session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": SCHEDULER_LOCK_ID}
            )
            return bool(result.scalar_one())
        return True

    async def release_scheduler_lock(self, session: AsyncSession) -> None:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": SCHEDULER_LOCK_ID}
            )

    async def close(self) -> None:
        await self.engine.dispose()
