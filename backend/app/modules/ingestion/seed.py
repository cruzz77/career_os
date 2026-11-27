"""Seed the skill catalogue into skills / skill_aliases, idempotently."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.db.session import Database
from app.modules.ingestion.skills import SkillCatalogue
from app.repositories import skills as skills_repo

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SeedResult:
    catalogue_version: int
    skills_inserted: int
    skills_updated: int
    aliases_written: int
    aliases_deleted: int


class SkillSeedService:
    """The catalogue file is the authority for aliases: aliases it no longer lists are deleted,
    so a correction to the file cannot be undone by a stale row. Skills are never deleted:
    user_skills and job_skills reference them, and the cascade would remove user data."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def run(self, catalogue: SkillCatalogue) -> SeedResult:
        if not catalogue.skills:
            raise ValueError("refusing to seed an empty catalogue: it would delete every alias")
        async with self._database.session() as session, session.begin():
            inserted, updated = await skills_repo.upsert_skills(
                session, [(skill.name, skill.category) for skill in catalogue.skills]
            )
            ids = await skills_repo.skill_ids_by_lower_name(
                session, [skill.name for skill in catalogue.skills]
            )
            aliases = {
                alias: ids[skill.name.lower()]
                for skill in catalogue.skills
                for alias in skill.aliases
            }
            written = await skills_repo.upsert_aliases(session, aliases)
            deleted = await skills_repo.delete_aliases_except(session, aliases.keys())
        result = SeedResult(catalogue.version, inserted, updated, written, deleted)
        _log.info(
            "skills_seeded",
            catalogue_version=result.catalogue_version,
            skills_inserted=inserted,
            skills_updated=updated,
            aliases_written=written,
            aliases_deleted=deleted,
        )
        return result
