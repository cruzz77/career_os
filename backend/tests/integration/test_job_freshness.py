from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import NotFoundError
from app.db.models import Company, Job, JobSkill, Skill
from app.db.session import Database
from app.domain.enums import Availability, JobStatus, PostingStatus
from app.modules.jobs.freshness import JobFreshnessService
from tests.conftest import FakeClock
from tests.fakes import FakeChecker

pytestmark = pytest.mark.integration


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC))


def _service(
    db: Database, checker: FakeChecker, clock: FakeClock, timeout_s: float = 2.0
) -> JobFreshnessService:
    return JobFreshnessService(db, {"lever": checker}, clock=clock, timeout_s=timeout_s)


async def _insert_job(
    db: Database,
    clock: FakeClock,
    *,
    source: str = "lever",
    verified_ago: timedelta = timedelta(hours=1),
    expired: bool = False,
) -> uuid.UUID:
    job = Job(
        source=source,
        source_board="acme",
        external_id=uuid.uuid4().hex,
        title="Backend Engineer",
        description="Build APIs",
        content_hash=b"\x01" * 32,
        canonical_hash=b"\x02" * 32,
        last_verified_at=clock.now - verified_ago,
    )
    if expired:
        job.status = JobStatus.EXPIRED.value
        job.expired_at = clock.now - timedelta(days=1)
        job.expired_reason = "missing_from_source"
    async with db.session() as session, session.begin():
        session.add(job)
        await session.flush()
        return job.id


async def _status_of(db: Database, job_id: uuid.UUID) -> tuple[str, str | None, datetime]:
    async with db.session() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    return job.status, job.expired_reason, job.last_verified_at


async def test_recently_verified_job_makes_no_outbound_call(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker()
    job_id = await _insert_job(app_database, clock, verified_ago=timedelta(minutes=5))
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.OPEN
    assert checker.calls == []


async def test_stale_open_job_is_reverified(app_database: Database, clock: FakeClock) -> None:
    checker = FakeChecker(status=PostingStatus.OPEN)
    job_id = await _insert_job(app_database, clock)
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.OPEN
    assert view.last_verified_at == clock.now
    assert len(checker.calls) == 1
    assert checker.calls[0][0] == "acme"


async def test_not_found_expires_the_job_immediately(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker(status=PostingStatus.NOT_FOUND)
    job_id = await _insert_job(app_database, clock)
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.CLOSED
    assert view.status is JobStatus.EXPIRED
    assert view.expired_at == clock.now
    status, reason, _ = await _status_of(app_database, job_id)
    assert (status, reason) == ("expired", "source_not_found")


async def test_source_error_serves_db_copy_as_unverified(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker(status=PostingStatus.ERROR)
    job_id = await _insert_job(app_database, clock)
    before = await _status_of(app_database, job_id)
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.UNVERIFIED
    assert view.status is JobStatus.ACTIVE
    assert await _status_of(app_database, job_id) == before


async def test_timeout_leaves_the_job_active_and_unverified(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker(delay_s=1.0)
    job_id = await _insert_job(app_database, clock)
    before = await _status_of(app_database, job_id)
    view = await _service(app_database, checker, clock, timeout_s=0.05).ensure_fresh(job_id)
    assert view.availability is Availability.UNVERIFIED
    assert await _status_of(app_database, job_id) == before


async def test_checker_crash_degrades_to_unverified(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker(error=ConnectionError("source down"))
    job_id = await _insert_job(app_database, clock)
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.UNVERIFIED
    assert view.status is JobStatus.ACTIVE


async def test_source_without_a_checker_is_unverified(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker()
    job_id = await _insert_job(app_database, clock, source="synthetic")
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.UNVERIFIED
    assert checker.calls == []


async def test_expired_job_is_closed_without_a_call(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker()
    job_id = await _insert_job(app_database, clock, expired=True)
    view = await _service(app_database, checker, clock).ensure_fresh(job_id)
    assert view.availability is Availability.CLOSED
    assert checker.calls == []


async def test_ttl_prevents_repeat_calls(app_database: Database, clock: FakeClock) -> None:
    checker = FakeChecker()
    service = _service(app_database, checker, clock)
    job_id = await _insert_job(app_database, clock)
    await service.ensure_fresh(job_id)
    clock.advance(timedelta(minutes=14))
    await service.ensure_fresh(job_id)
    assert len(checker.calls) == 1
    clock.advance(timedelta(minutes=2))
    await service.ensure_fresh(job_id)
    assert len(checker.calls) == 2


async def test_concurrent_opens_share_one_outbound_call(
    app_database: Database, clock: FakeClock
) -> None:
    checker = FakeChecker(delay_s=0.05)
    service = _service(app_database, checker, clock)
    job_id = await _insert_job(app_database, clock)
    views = await asyncio.gather(*(service.ensure_fresh(job_id) for _ in range(5)))
    assert len(checker.calls) == 1
    assert {view.availability for view in views} == {Availability.OPEN}


async def test_unknown_job_is_not_found(app_database: Database, clock: FakeClock) -> None:
    with pytest.raises(NotFoundError):
        await _service(app_database, FakeChecker(), clock).ensure_fresh(uuid.uuid4())


async def test_view_carries_company_and_required_skills_first(
    app_database: Database, clock: FakeClock
) -> None:
    suffix = uuid.uuid4().hex[:8]
    job_id = await _insert_job(app_database, clock, verified_ago=timedelta(minutes=1))
    async with app_database.session() as session, session.begin():
        company = Company(name="Acme", normalized_name=f"acme-{suffix}")
        python = Skill(canonical_name=f"Python {suffix}")
        docker = Skill(canonical_name=f"Docker {suffix}")
        session.add_all([company, python, docker])
        await session.flush()
        job = await session.get(Job, job_id)
        assert job is not None
        job.company_id = company.id
        session.add_all(
            [
                JobSkill(job_id=job_id, skill_id=docker.id, requirement="preferred"),
                JobSkill(job_id=job_id, skill_id=python.id, requirement="required"),
            ]
        )
    view = await _service(app_database, FakeChecker(), clock).ensure_fresh(job_id)
    assert view.company == "Acme"
    assert [(s.name, s.requirement.value) for s in view.skills] == [
        (f"Python {suffix}", "required"),
        (f"Docker {suffix}", "preferred"),
    ]
