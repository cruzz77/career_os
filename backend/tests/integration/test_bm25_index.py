from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.models import Job
from app.db.session import Database

pytestmark = pytest.mark.integration

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

_SEARCH = text(
    "SELECT external_id FROM jobs "
    "WHERE (title ||| :q OR skills_text ||| :q OR description ||| :q) AND status = 'active' "
    "ORDER BY pdb.score(id) DESC, external_id"
)


async def _insert(db: Database) -> None:
    rows = [
        ("py", "Backend Engineer", "Python", "Build Python services and APIs", "remote"),
        ("chef", "Pastry Chef", "", "Bake bread and croissants", "onsite"),
    ]
    async with db.session() as session, session.begin():
        for i, (external_id, title, skills, description, mode) in enumerate(rows):
            session.add(
                Job(
                    source="test",
                    source_board="b",
                    external_id=external_id,
                    title=title,
                    skills_text=skills,
                    description=description,
                    work_mode=mode,
                    content_hash=bytes([i + 1]) * 32,
                    canonical_hash=bytes([i + 1]) * 32,
                )
            )


async def _search(db: Database, query: str) -> list[str]:
    async with db.session() as session:
        return list((await session.scalars(_SEARCH, {"q": query})).all())


async def test_bm25_index_exists_with_the_paradedb_access_method(fresh_db: Database) -> None:
    async with fresh_db.session() as session:
        method = await session.scalar(
            text(
                "SELECT am.amname FROM pg_class c JOIN pg_am am ON am.oid = c.relam "
                "WHERE c.relname = 'ix_jobs_bm25'"
            )
        )
    assert method in {"paradedb", "bm25"}


async def test_search_ranks_matches_and_stems_english(fresh_db: Database) -> None:
    await _insert(fresh_db)
    assert await _search(fresh_db, "python") == ["py"]
    assert await _search(fresh_db, "engineers") == ["py"]  # stemmed to "engineer"
    assert await _search(fresh_db, "croissant") == ["chef"]
    assert await _search(fresh_db, "kubernetes") == []


async def test_filters_combine_with_search(fresh_db: Database) -> None:
    await _insert(fresh_db)
    query = text(
        "SELECT external_id FROM jobs WHERE description ||| 'python bread' "
        "AND work_mode = 'onsite' ORDER BY external_id"
    )
    async with fresh_db.session() as session:
        assert list((await session.scalars(query)).all()) == ["chef"]


async def test_an_update_is_visible_to_the_next_search(fresh_db: Database) -> None:
    await _insert(fresh_db)
    async with fresh_db.session() as session, session.begin():
        await session.execute(
            text(
                "UPDATE jobs SET status = 'expired', expired_at = now(), "
                "expired_reason = 'missing_from_source' WHERE external_id = 'py'"
            )
        )
    assert await _search(fresh_db, "python") == []


def test_downgrade_drops_the_index_but_keeps_the_extension(fresh_database_url: str) -> None:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", fresh_database_url)
    plain = fresh_database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def state() -> tuple[bool, bool]:
        with psycopg.connect(plain) as conn:
            index = conn.execute("SELECT to_regclass('ix_jobs_bm25') IS NOT NULL").fetchone()
            ext = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search')"
            ).fetchone()
        assert index is not None
        assert ext is not None
        return bool(index[0]), bool(ext[0])

    assert state() == (True, True)
    command.downgrade(config, "0001_core")
    assert state() == (False, True)
    command.upgrade(config, "head")
    assert state() == (True, True)
