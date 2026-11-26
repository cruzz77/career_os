"""Async engine, session factory and connectivity probe."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

_CONNECT_TIMEOUT_S: Final = 5
_PING_TIMEOUT_S: Final = 3.0


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        # expire_on_commit=False: under async, touching an expired attribute after commit would
        # trigger implicit IO and fail, and services return loaded objects after committing.
        self._sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_s,
            pool_pre_ping=True,
            # Set at connect time so every pooled connection carries it: Postgres cancels a
            # runaway query itself instead of relying on the client to give up.
            connect_args={
                "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
                "connect_timeout": _CONNECT_TIMEOUT_S,
            },
        )
        return cls(engine)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with asyncio.timeout(_PING_TIMEOUT_S), self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError, TimeoutError):
            return False
        return True

    async def dispose(self) -> None:
        await self._engine.dispose()
