from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import Database
from tests.conftest import make_settings, paradedb_image

pytestmark = pytest.mark.integration


def test_tests_use_the_pinned_production_image() -> None:
    assert "@sha256:" in paradedb_image()


async def test_ping_succeeds_against_live_database(database: Database) -> None:
    assert await database.ping() is True


async def test_ping_fails_cleanly_when_database_unreachable() -> None:
    unreachable = Database.from_settings(
        make_settings("postgresql+psycopg://careeros:x@127.0.0.1:1/careeros")
    )
    try:
        assert await unreachable.ping() is False
    finally:
        await unreachable.dispose()


async def test_statement_timeout_is_applied_to_sessions(database: Database) -> None:
    async with database.session() as session:
        value = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
    assert value == "15s"


async def test_statement_timeout_cancels_runaway_query(postgres_url: str) -> None:
    strict = Database.from_settings(make_settings(postgres_url, db_statement_timeout_ms=200))
    try:
        async with strict.session() as session:
            with pytest.raises(DBAPIError, match="statement timeout"):
                await session.execute(text("SELECT pg_sleep(2)"))
    finally:
        await strict.dispose()


async def test_required_extensions_are_installable(database: Database) -> None:
    async with database.session() as session:
        rows = await session.execute(
            text("SELECT name FROM pg_available_extensions WHERE name = ANY(:names) ORDER BY name"),
            {"names": ["citext", "pg_search", "pg_trgm", "unaccent", "vector"]},
        )
        assert list(rows.scalars()) == ["citext", "pg_search", "pg_trgm", "unaccent", "vector"]
