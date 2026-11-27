from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select

from app.db.models import Job, JobSkill, Skill
from app.db.session import Database
from app.modules.ingestion.seed import SkillSeedService
from app.modules.ingestion.skills import SkillCatalogue
from app.repositories import skills as skills_repo

pytestmark = pytest.mark.integration


def _catalogue(version: int, skills: list[tuple[str, str, list[str]]]) -> SkillCatalogue:
    return SkillCatalogue.model_validate(
        {
            "version": version,
            "categories": ["x", "y"],
            "skills": [{"name": n, "category": c, "aliases": a} for n, c, a in skills],
        }
    )


async def _aliases(db: Database) -> dict[str, str]:
    async with db.session() as session:
        return await skills_repo.load_aliases(session)


async def test_seed_writes_the_shipped_catalogue(fresh_db: Database) -> None:
    catalogue = SkillCatalogue.load()
    result = await SkillSeedService(fresh_db).run(catalogue)
    expected = {a: s.name for s in catalogue.skills for a in s.aliases}
    assert (result.skills_inserted, result.skills_updated) == (len(catalogue.skills), 0)
    assert (result.aliases_written, result.aliases_deleted) == (len(expected), 0)
    assert await _aliases(fresh_db) == expected


async def test_second_seed_changes_nothing(fresh_db: Database) -> None:
    service = SkillSeedService(fresh_db)
    catalogue = SkillCatalogue.load()
    await service.run(catalogue)
    again = await service.run(catalogue)
    assert (again.skills_inserted, again.skills_updated) == (0, 0)
    assert (again.aliases_written, again.aliases_deleted) == (0, 0)


async def test_catalogue_change_is_applied_and_removed_skills_keep_their_links(
    fresh_db: Database,
) -> None:
    service = SkillSeedService(fresh_db)
    await service.run(
        _catalogue(1, [("A", "x", ["a1", "a3"]), ("B", "x", ["b1"]), ("C", "x", ["c1"])])
    )
    async with fresh_db.session() as session, session.begin():
        c_id = await session.scalar(select(Skill.id).where(Skill.canonical_name == "C"))
        job = Job(
            source="lever",
            source_board="acme",
            external_id="seed-test-1",
            title="Engineer",
            description="Build things",
            content_hash=b"\x01" * 32,
            canonical_hash=b"\x02" * 32,
        )
        session.add(job)
        await session.flush()
        session.add(JobSkill(job_id=job.id, skill_id=c_id, requirement="required"))

    result = await service.run(
        _catalogue(2, [("A", "y", ["a1"]), ("B", "x", ["b1", "a3"]), ("D", "x", ["d1"])])
    )

    assert (result.skills_inserted, result.skills_updated) == (1, 1)
    assert (result.aliases_written, result.aliases_deleted) == (2, 1)
    assert await _aliases(fresh_db) == {"a1": "A", "a3": "B", "b1": "B", "d1": "D"}
    async with fresh_db.session() as session:
        category = await session.scalar(select(Skill.category).where(Skill.canonical_name == "A"))
        links = await session.scalar(
            select(func.count()).select_from(JobSkill).where(JobSkill.skill_id == c_id)
        )
    assert category == "y"
    assert links == 1


async def test_existing_skill_with_other_casing_is_renamed_not_duplicated(
    fresh_db: Database,
) -> None:
    async with fresh_db.session() as session, session.begin():
        session.add(Skill(canonical_name="python", category=None))
    result = await SkillSeedService(fresh_db).run(_catalogue(1, [("Python", "x", ["python"])]))
    async with fresh_db.session() as session:
        names = (await session.scalars(select(Skill.canonical_name))).all()
    assert (result.skills_inserted, result.skills_updated) == (0, 1)
    assert names == ["Python"]


async def test_empty_catalogue_is_refused(fresh_db: Database) -> None:
    empty: dict[str, Any] = {"version": 1, "categories": ["x"], "skills": []}
    with pytest.raises(ValueError, match="empty catalogue"):
        await SkillSeedService(fresh_db).run(SkillCatalogue.model_validate(empty))
