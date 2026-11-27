"""Queries on the skill catalogue (skills, skill_aliases)."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence

from sqlalchemy import Boolean, delete, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Skill, SkillAlias


async def upsert_skills(
    session: AsyncSession, skills: Sequence[tuple[str, str | None]]
) -> tuple[int, int]:
    """(inserted, updated). Keyed on lower(canonical_name), the unique index, so a row stored
    as "python" is renamed to "Python" instead of colliding. Unchanged rows are not touched."""
    if not skills:
        return 0, 0
    base = pg_insert(Skill).values(
        [{"canonical_name": name, "category": category} for name, category in skills]
    )
    upsert = base.on_conflict_do_update(
        index_elements=[func.lower(Skill.canonical_name)],
        set_={"canonical_name": base.excluded.canonical_name, "category": base.excluded.category},
        where=or_(
            Skill.canonical_name.is_distinct_from(base.excluded.canonical_name),
            Skill.category.is_distinct_from(base.excluded.category),
        ),
    ).returning(literal_column("(xmax = 0)", Boolean).label("inserted"))
    flags = (await session.execute(upsert)).scalars().all()
    inserted = sum(1 for flag in flags if flag)
    return inserted, len(flags) - inserted


async def skill_ids_by_lower_name(
    session: AsyncSession, names: Collection[str]
) -> dict[str, uuid.UUID]:
    lowered = [name.lower() for name in names]
    rows = await session.execute(
        select(func.lower(Skill.canonical_name), Skill.id).where(
            func.lower(Skill.canonical_name).in_(lowered)
        )
    )
    return dict(rows.all())


async def upsert_aliases(session: AsyncSession, aliases: Mapping[str, uuid.UUID]) -> int:
    """Number of aliases inserted or moved to another skill."""
    if not aliases:
        return 0
    base = pg_insert(SkillAlias).values(
        [{"alias": alias, "skill_id": skill_id} for alias, skill_id in aliases.items()]
    )
    upsert = base.on_conflict_do_update(
        index_elements=[SkillAlias.alias],
        set_={"skill_id": base.excluded.skill_id},
        where=SkillAlias.skill_id.is_distinct_from(base.excluded.skill_id),
    ).returning(SkillAlias.alias)
    return len((await session.execute(upsert)).all())


async def delete_aliases_except(session: AsyncSession, keep: Collection[str]) -> int:
    result = await session.execute(
        delete(SkillAlias).where(SkillAlias.alias.not_in(list(keep))).returning(SkillAlias.alias)
    )
    return len(result.all())


async def load_aliases(session: AsyncSession) -> dict[str, str]:
    """alias -> canonical skill name: what the extractor matches during sync."""
    rows = await session.execute(
        select(SkillAlias.alias, Skill.canonical_name).join(Skill, Skill.id == SkillAlias.skill_id)
    )
    return dict(rows.all())
