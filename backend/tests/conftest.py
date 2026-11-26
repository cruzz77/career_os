from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from testcontainers.community.postgres import PostgresContainer

from app.core.config import Settings
from app.db.session import Database

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_COMPOSE_FILE = _BACKEND_DIR.parent / "infra" / "docker-compose.yml"
_PARADEDB_IMAGE = re.compile(r"^\s*image:\s*(paradedb/paradedb:\S+)\s*$", re.MULTILINE)
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://careeros:x@127.0.0.1:1/careeros"


def paradedb_image() -> str:
    """The exact image reference production runs, read from compose so it cannot drift."""
    match = _PARADEDB_IMAGE.search(_COMPOSE_FILE.read_text())
    assert match, f"no pinned paradedb image found in {_COMPOSE_FILE}"
    return match.group(1)


def make_settings(database_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": database_url,
        "frontend_origin": "http://localhost:3000",
        "proxy_secret": "t" * 32,
        "session_cookie_secure": False,
    }
    return Settings.model_validate({**values, **overrides})


@dataclass
class FakeClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Run the app's lifespan, then yield a client bound to it; httpx never runs lifespans."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    container = PostgresContainer(
        paradedb_image(),
        username="careeros",
        password="careeros-test",
        dbname="careeros",
        driver="psycopg",
    )
    with container as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
async def database(postgres_url: str) -> AsyncIterator[Database]:
    db = Database.from_settings(make_settings(postgres_url))
    yield db
    await db.dispose()


@pytest.fixture(scope="session")
def migrated_database_url(postgres_url: str) -> str:
    """A database in the shared container, migrated to head once per test run."""
    name = "careeros_app"
    admin_url = postgres_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = postgres_url.rsplit("/", 1)[0] + f"/{name}"
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url


@pytest.fixture(scope="session")
async def app_database(migrated_database_url: str) -> AsyncIterator[Database]:
    db = Database.from_settings(make_settings(migrated_database_url))
    yield db
    await db.dispose()
