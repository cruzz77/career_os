from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import Company, IngestionRun, Job, JobSkill, Skill
from app.db.session import Database
from app.domain.enums import EmploymentType, PostingStatus, Seniority, WorkMode
from app.domain.jobs import PostingCheck
from app.modules.ingestion.seed import SkillSeedService
from app.modules.ingestion.skills import Section, SectionKind, SkillCatalogue
from app.modules.ingestion.sources.base import FetchResult, NormalizedPosting, PostingLocation
from app.modules.ingestion.sync import SkillCatalogueEmptyError, SyncService
from tests.conftest import FakeClock

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)


def _catalogue(version: int, go_alias: bool) -> SkillCatalogue:
    skills = [
        {"name": "Python", "category": "x", "aliases": ["python"]},
        {"name": "Kafka", "category": "x", "aliases": ["kafka"]},
        {"name": "Go", "category": "x", "aliases": ["golang"] if go_alias else ["go lang"]},
    ]
    return SkillCatalogue.model_validate(
        {"version": version, "categories": ["x"], "skills": skills}
    )


def _posting(i: int, **overrides: object) -> NormalizedPosting:
    base = NormalizedPosting(
        source="fake",
        source_board="acme",
        external_id=f"ext-{i}",
        title=f"Engineer {i}",
        description="Build services",
        company_name="Acme",
        locations=(PostingLocation("Bangalore", "Bengaluru", "IN", WorkMode.HYBRID),),
        work_mode=WorkMode.HYBRID,
        employment_type=EmploymentType.FULL_TIME,
        seniority=Seniority.UNKNOWN,
        posted_at=T0 - timedelta(days=3),
        expires_at=None,
        source_url=f"https://jobs.example.test/{i}",
        skill_sections=(Section(SectionKind.REQUIRED, "Python"),),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def _fetch(
    postings: Sequence[NormalizedPosting],
    *,
    board: str = "acme",
    complete: bool = True,
    extra_seen: Sequence[str] = (),
) -> FetchResult:
    if not complete:
        return FetchResult("fake", board, False, (), frozenset(), error="HTTP 503")
    seen = frozenset([p.external_id for p in postings] + list(extra_seen))
    return FetchResult("fake", board, True, tuple(postings), seen)


class FakeSource:
    def __init__(self, *boards: str) -> None:
        self._boards = boards or ("acme",)
        self.queue: dict[str, list[FetchResult]] = {board: [] for board in self._boards}

    @property
    def name(self) -> str:
        return "fake"

    def boards(self) -> tuple[str, ...]:
        return self._boards

    async def fetch_all(self, board: str) -> FetchResult:
        return self.queue[board].pop(0)

    async def check_posting(self, board: str, external_id: str) -> PostingCheck:
        return PostingCheck(status=PostingStatus.OPEN)


async def _seed(db: Database, *, go_alias: bool = True) -> None:
    await SkillSeedService(db).run(_catalogue(1 if go_alias else 2, go_alias))


async def _jobs(db: Database) -> dict[str, Job]:
    async with db.session() as session:
        rows = (await session.scalars(select(Job).where(Job.source == "fake"))).all()
    return {job.external_id: job for job in rows}


async def _skills(db: Database, job_id: object) -> dict[str, str]:
    async with db.session() as session:
        rows = await session.execute(
            select(Skill.canonical_name, JobSkill.requirement)
            .join(JobSkill, JobSkill.skill_id == Skill.id)
            .where(JobSkill.job_id == job_id)
        )
        return dict(rows.all())


def _service(db: Database, source: FakeSource, clock: FakeClock) -> SyncService:
    return SyncService(db, [source], clock=clock)


async def test_first_sync_inserts_jobs_skills_company_and_a_run(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    two = (Section(SectionKind.REQUIRED, "Kafka and Python"),)
    source.queue["acme"] = [_fetch([_posting(1), _posting(2, skill_sections=two)])]
    report = await _service(fresh_db, source, clock).run()
    (board,) = report.boards
    assert board.inserted == 2
    jobs = await _jobs(fresh_db)
    assert jobs["ext-1"].status == "active"
    assert jobs["ext-1"].skills_text == "Python"
    assert jobs["ext-2"].skills_text == "Kafka, Python"
    assert len(jobs["ext-1"].content_hash) == 32
    assert await _skills(fresh_db, jobs["ext-2"].id) == {"Kafka": "required", "Python": "required"}
    async with fresh_db.session() as session:
        companies = (await session.scalars(select(Company.name))).all()
        runs = await session.scalar(
            select(func.count()).select_from(IngestionRun).where(IngestionRun.source == "fake")
        )
    assert companies == ["Acme"]
    assert runs == 1


async def test_resync_of_unchanged_postings_is_idempotent(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [_fetch([_posting(1), _posting(2)]), _fetch([_posting(1), _posting(2)])]
    service = _service(fresh_db, source, clock)
    await service.run()
    clock.advance(timedelta(hours=1))
    (again,) = (await service.run()).boards
    assert (again.inserted, again.updated, again.unchanged) == (0, 0, 2)
    assert again.skills_rewritten == 0
    assert len(await _jobs(fresh_db)) == 2


async def test_changed_content_updates_the_job(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    changed = _posting(1, description="Build services in Go")
    source.queue["acme"] = [_fetch([_posting(1), _posting(2)]), _fetch([changed, _posting(2)])]
    service = _service(fresh_db, source, clock)
    await service.run()
    before = (await _jobs(fresh_db))["ext-1"].content_hash
    (board,) = (await service.run()).boards
    after = (await _jobs(fresh_db))["ext-1"]
    assert (board.updated, board.unchanged) == (1, 1)
    assert after.description == "Build services in Go"
    assert after.content_hash != before


async def test_authoritative_miss_expires_the_job(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [_fetch([_posting(1), _posting(2)]), _fetch([_posting(1)])]
    service = _service(fresh_db, source, clock)
    await service.run()
    clock.advance(timedelta(hours=1))
    (board,) = (await service.run()).boards
    gone = (await _jobs(fresh_db))["ext-2"]
    assert board.expired == 1
    assert (gone.status, gone.expired_reason) == ("expired", "missing_from_source")
    assert gone.expired_at == clock.now


async def test_incomplete_fetch_expires_nothing(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [_fetch([_posting(1), _posting(2)]), _fetch([], complete=False)]
    service = _service(fresh_db, source, clock)
    await service.run()
    clock.advance(timedelta(hours=1))
    (board,) = (await service.run()).boards
    jobs = await _jobs(fresh_db)
    assert board.expired == 0
    assert {job.status for job in jobs.values()} == {"active"}
    assert jobs["ext-2"].last_seen_at == T0


async def test_safety_valve_skips_a_mass_expiry(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    ten = [_posting(i) for i in range(10)]
    source.queue["acme"] = [_fetch(ten), _fetch(ten[:2])]
    service = _service(fresh_db, source, clock)
    await service.run()
    (board,) = (await service.run()).boards
    assert board.valve_tripped
    assert board.expired == 0
    assert {job.status for job in (await _jobs(fresh_db)).values()} == {"active"}
    async with fresh_db.session() as session:
        notes = await session.scalar(
            select(IngestionRun.notes)
            .where(IngestionRun.source == "fake")
            .order_by(IngestionRun.created_at.desc())
            .limit(1)
        )
    assert notes is not None
    assert '"valve_tripped": true' in notes


async def test_safety_valve_does_not_protect_a_small_board(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    four = [_posting(i) for i in range(4)]
    source.queue["acme"] = [_fetch(four), _fetch([])]
    service = _service(fresh_db, source, clock)
    await service.run()
    (board,) = (await service.run()).boards
    assert not board.valve_tripped
    assert board.expired == 4


async def test_reappearing_job_is_reactivated(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [
        _fetch([_posting(1), _posting(2)]),
        _fetch([_posting(1)]),
        _fetch([_posting(1), _posting(2)]),
    ]
    service = _service(fresh_db, source, clock)
    await service.run()
    await service.run()
    (board,) = (await service.run()).boards
    back = (await _jobs(fresh_db))["ext-2"]
    assert board.reactivated == 1
    assert (back.status, back.expired_at, back.expired_reason) == ("active", None, None)


async def test_listed_but_unparseable_job_is_touched_not_expired(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [
        _fetch([_posting(1), _posting(2)]),
        _fetch([_posting(1)], extra_seen=["ext-2"]),
    ]
    service = _service(fresh_db, source, clock)
    await service.run()
    clock.advance(timedelta(hours=1))
    await service.run()
    job = (await _jobs(fresh_db))["ext-2"]
    assert job.status == "active"
    assert job.last_seen_at == clock.now


async def test_past_deadline_is_expired_by_the_sweep(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [_fetch([_posting(1, expires_at=T0 - timedelta(hours=1))])]
    report = await _service(fresh_db, source, clock).run()
    job = (await _jobs(fresh_db))["ext-1"]
    assert report.expired_past_deadline == 1
    assert (job.status, job.expired_reason) == ("expired", "past_expires_at")


async def test_stale_sweep_expires_jobs_of_a_failing_board(fresh_db: Database) -> None:
    await _seed(fresh_db)
    source, clock = FakeSource("acme", "other"), FakeClock(T0)
    source.queue["acme"] = [_fetch([_posting(1)]), _fetch([_posting(1)])]
    source.queue["other"] = [
        _fetch([_posting(5, source_board="other")], board="other"),
        _fetch([], board="other", complete=False),
    ]
    service = _service(fresh_db, source, clock)
    await service.run()
    clock.advance(timedelta(hours=25))
    report = await service.run()
    jobs = await _jobs(fresh_db)
    assert report.expired_stale == 1
    assert (jobs["ext-5"].status, jobs["ext-5"].expired_reason) == ("expired", "stale_not_seen")
    assert jobs["ext-1"].status == "active"


async def test_sync_refuses_to_run_without_a_catalogue(fresh_db: Database) -> None:
    source, clock = FakeSource(), FakeClock(T0)
    source.queue["acme"] = [_fetch([_posting(1)])]
    with pytest.raises(SkillCatalogueEmptyError, match="seed-skills"):
        await _service(fresh_db, source, clock).run()


async def test_catalogue_change_rewrites_skills_without_a_content_change(
    fresh_db: Database,
) -> None:
    await _seed(fresh_db, go_alias=True)
    source, clock = FakeSource(), FakeClock(T0)
    sections = (Section(SectionKind.REQUIRED, "golang and python"),)
    posting = _posting(1, skill_sections=sections)
    source.queue["acme"] = [_fetch([posting]), _fetch([posting])]
    service = _service(fresh_db, source, clock)
    await service.run()
    job = (await _jobs(fresh_db))["ext-1"]
    assert await _skills(fresh_db, job.id) == {"Go": "required", "Python": "required"}
    await _seed(fresh_db, go_alias=False)
    (board,) = (await service.run()).boards
    job = (await _jobs(fresh_db))["ext-1"]
    assert (board.updated, board.skills_rewritten) == (0, 1)
    assert job.skills_text == "Python"
    assert await _skills(fresh_db, job.id) == {"Python": "required"}
