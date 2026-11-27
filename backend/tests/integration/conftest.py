"""Fixtures for integration tests that need a database of their own."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.db.session import Database
from tests.conftest import make_settings

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@pytest.fixture
def fresh_database_url(postgres_url: str) -> str:
    """A newly created, migrated database in the shared container. For tests whose writes are
    global (the skill seed deletes aliases it does not list, sync expires whole boards), so
    they cannot interfere with tests sharing app_database."""
    name = f"careeros_t_{uuid.uuid4().hex[:12]}"
    admin_url = postgres_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = postgres_url.rsplit("/", 1)[0] + f"/{name}"
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


@pytest.fixture
async def fresh_db(fresh_database_url: str) -> AsyncIterator[Database]:
    db = Database.from_settings(make_settings(fresh_database_url))
    yield db
    await db.dispose()
