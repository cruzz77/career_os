from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.cli import main
from app.core.config import get_settings
from app.db.models import IngestionRun, Job, JobSkill, SavedJob, Skill, User, UserSession
from app.db.session import Database
from app.modules.jobs.purge import PurgeService
from tests.conftest import FakeClock

pytestmark = pytest.mark.integration

_ABSOLUTE_TTL = timedelta(days=30)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC))


def _service(db: Database, clock: FakeClock) -> PurgeService:
    return PurgeService(db, absolute_session_ttl=_ABSOLUTE_TTL, clock=clock)


async def _insert_job(db: Database, clock: FakeClock, *, expired_days_ago: int | None) -> uuid.UUID:
    job = Job(
        source="lever",
        source_board="acme",
        external_id=uuid.uuid4().hex,
        title="Backend Engineer",
        description="Build APIs",
        content_hash=b"\x01" * 32,
        canonical_hash=b"\x02" * 32,
    )
    if expired_days_ago is not None:
        job.status = "expired"
        job.expired_at = clock.now - timedelta(days=expired_days_ago)
        job.expired_reason = "missing_from_source"
    async with db.session() as session, session.begin():
        session.add(job)
        await session.flush()
        return job.id


async def _insert_user(db: Database) -> uuid.UUID:
    async with db.session() as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:12]}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        return user.id


async def _exists(db: Database, model: type[Job] | type[UserSession], row_id: uuid.UUID) -> bool:
    async with db.session() as session:
        return await session.get(model, row_id) is not None


async def test_old_expired_job_is_deleted_with_its_skills(
    app_database: Database, clock: FakeClock
) -> None:
    job_id = await _insert_job(app_database, clock, expired_days_ago=31)
    async with app_database.session() as session, session.begin():
        skill = Skill(canonical_name=f"Go {uuid.uuid4().hex[:8]}")
        session.add(skill)
        await session.flush()
        session.add(JobSkill(job_id=job_id, skill_id=skill.id, requirement="required"))
    result = await _service(app_database, clock).run()
    assert result.jobs_deleted >= 1
    assert not await _exists(app_database, Job, job_id)
    async with app_database.session() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(JobSkill).where(JobSkill.job_id == job_id)
        )
    assert remaining == 0


async def test_recently_expired_job_is_kept(app_database: Database, clock: FakeClock) -> None:
    job_id = await _insert_job(app_database, clock, expired_days_ago=29)
    await _service(app_database, clock).run()
    assert await _exists(app_database, Job, job_id)


async def test_active_job_is_never_purged(app_database: Database, clock: FakeClock) -> None:
    job_id = await _insert_job(app_database, clock, expired_days_ago=None)
    clock.advance(timedelta(days=365))
    await _service(app_database, clock).run()
    assert await _exists(app_database, Job, job_id)


async def test_saved_job_is_spared(app_database: Database, clock: FakeClock) -> None:
    job_id = await _insert_job(app_database, clock, expired_days_ago=90)
    user_id = await _insert_user(app_database)
    async with app_database.session() as session, session.begin():
        session.add(SavedJob(user_id=user_id, job_id=job_id))
    await _service(app_database, clock).run()
    assert await _exists(app_database, Job, job_id)


async def test_judged_job_is_spared_once_eval_judgements_exists(
    app_database: Database, clock: FakeClock
) -> None:
    judged = await _insert_job(app_database, clock, expired_days_ago=90)
    unjudged = await _insert_job(app_database, clock, expired_days_ago=90)
    async with app_database.session() as session, session.begin():
        # Stand-in for migration 0003's table: only job_id matters to the purge.
        await session.execute(text("CREATE TABLE eval_judgements (job_id uuid NOT NULL)"))
        await session.execute(
            text("INSERT INTO eval_judgements (job_id) VALUES (:job_id)"), {"job_id": judged}
        )
    try:
        result = await _service(app_database, clock).run()
    finally:
        async with app_database.session() as session, session.begin():
            await session.execute(text("DROP TABLE eval_judgements"))
    assert result.judged_jobs_protected
    assert await _exists(app_database, Job, judged)
    assert not await _exists(app_database, Job, unjudged)


async def test_dead_sessions_are_deleted_and_live_ones_kept(
    app_database: Database, clock: FakeClock
) -> None:
    user_id = await _insert_user(app_database)
    now = clock.now
    windows = {
        "idle_expired": (now - timedelta(days=10), now - timedelta(days=1)),
        "past_absolute": (now - timedelta(days=40), now + timedelta(days=1)),
        "live": (now - timedelta(days=1), now + timedelta(days=6)),
    }
    ids: dict[str, uuid.UUID] = {}
    async with app_database.session() as session, session.begin():
        for name, (created_at, expires_at) in windows.items():
            row = UserSession(
                user_id=user_id,
                token_hash=os.urandom(32),
                created_at=created_at,
                last_seen_at=created_at,
                expires_at=expires_at,
            )
            session.add(row)
            await session.flush()
            ids[name] = row.id
    result = await _service(app_database, clock).run()
    assert result.sessions_deleted >= 2
    assert not await _exists(app_database, UserSession, ids["idle_expired"])
    assert not await _exists(app_database, UserSession, ids["past_absolute"])
    assert await _exists(app_database, UserSession, ids["live"])


async def test_every_purge_is_recorded(app_database: Database, clock: FakeClock) -> None:
    await _insert_job(app_database, clock, expired_days_ago=45)
    result = await _service(app_database, clock).run()
    async with app_database.session() as session:
        run = await session.scalar(
            select(IngestionRun)
            .where(IngestionRun.source == "purge", IngestionRun.started_at == clock.now)
            .order_by(IngestionRun.created_at.desc())
        )
    assert run is not None
    assert run.purged == result.jobs_deleted >= 1
    assert run.finished_at is not None
    assert f"sessions_deleted={result.sessions_deleted}" in (run.notes or "")
    assert "judged_jobs_protected=false" in (run.notes or "")


def test_cli_runs_purge(migrated_database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "ENVIRONMENT": "test",
        "DATABASE_URL": migrated_database_url,
        "FRONTEND_ORIGIN": "http://localhost:3000",
        "PROXY_SECRET": "t" * 32,
        "SESSION_COOKIE_SECURE": "false",
        "LOG_JSON": "true",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        assert main(["purge"]) == 0
    finally:
        get_settings.cache_clear()


def test_cli_requires_a_known_command() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    with pytest.raises(SystemExit):
        main(["drop-everything"])
