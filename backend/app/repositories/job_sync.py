"""Bulk queries for the job sync (blueprint 6.4, 6.4.2)."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, IngestionRun, Job, JobEmbedding, JobSkill, Skill
from app.domain.enums import ExpiredReason, JobStatus


@dataclass(frozen=True, slots=True)
class StoredJob:
    id: uuid.UUID
    external_id: str
    content_hash: bytes
    status: str


async def upsert_company(session: AsyncSession, *, name: str, normalized_name: str) -> uuid.UUID:
    base = pg_insert(Company).values(name=name, normalized_name=normalized_name)
    upsert = base.on_conflict_do_update(
        index_elements=[Company.normalized_name],
        set_={"name": base.excluded.name},
        where=Company.name.is_distinct_from(base.excluded.name),
    ).returning(Company.id)
    company_id = await session.scalar(upsert)
    if company_id is None:
        # DO UPDATE ... WHERE false returns no row for an unchanged company.
        company_id = await session.scalar(
            select(Company.id).where(Company.normalized_name == normalized_name)
        )
    if company_id is None:
        raise RuntimeError(f"company {normalized_name!r} vanished during upsert")
    return company_id


async def board_jobs(session: AsyncSession, *, source: str, board: str) -> dict[str, StoredJob]:
    rows = await session.execute(
        select(Job.id, Job.external_id, Job.content_hash, Job.status).where(
            Job.source == source, Job.source_board == board
        )
    )
    return {
        external_id: StoredJob(job_id, external_id, content_hash, status)
        for job_id, external_id, content_hash, status in rows.all()
    }


async def board_skills(
    session: AsyncSession, job_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, dict[str, str]]:
    if not job_ids:
        return {}
    rows = await session.execute(
        select(JobSkill.job_id, Skill.canonical_name, JobSkill.requirement)
        .join(Skill, Skill.id == JobSkill.skill_id)
        .where(JobSkill.job_id.in_(list(job_ids)))
    )
    skills: dict[uuid.UUID, dict[str, str]] = {}
    for job_id, name, requirement in rows.all():
        skills.setdefault(job_id, {})[name] = requirement
    return skills


async def skill_ids(session: AsyncSession) -> dict[str, uuid.UUID]:
    return dict((await session.execute(select(Skill.canonical_name, Skill.id))).all())


async def insert_job(session: AsyncSession, values: Mapping[str, object]) -> uuid.UUID:
    return (await session.execute(insert(Job).values(**values).returning(Job.id))).scalar_one()


async def update_job(
    session: AsyncSession, job_id: uuid.UUID, values: Mapping[str, object]
) -> None:
    await session.execute(update(Job).where(Job.id == job_id).values(**values))


async def replace_job_skills(
    session: AsyncSession, job_id: uuid.UUID, skills: Mapping[uuid.UUID, str]
) -> None:
    await session.execute(delete(JobSkill).where(JobSkill.job_id == job_id))
    if skills:
        await session.execute(
            insert(JobSkill).values(
                [
                    {"job_id": job_id, "skill_id": skill_id, "requirement": requirement}
                    for skill_id, requirement in skills.items()
                ]
            )
        )


async def touch_seen(
    session: AsyncSession, job_ids: Collection[uuid.UUID], *, now: datetime
) -> None:
    if job_ids:
        await session.execute(
            update(Job)
            .where(Job.id.in_(list(job_ids)), Job.status == JobStatus.ACTIVE.value)
            .values(last_seen_at=now, last_verified_at=func.greatest(Job.last_verified_at, now))
        )


async def expire_jobs(
    session: AsyncSession, job_ids: Collection[uuid.UUID], *, reason: ExpiredReason, now: datetime
) -> int:
    if not job_ids:
        return 0
    result = await session.execute(
        update(Job)
        .where(Job.id.in_(list(job_ids)), Job.status == JobStatus.ACTIVE.value)
        .values(status=JobStatus.EXPIRED.value, expired_at=now, expired_reason=reason.value)
        .returning(Job.id)
    )
    return len(result.all())


async def expire_past_deadline(session: AsyncSession, *, now: datetime) -> int:
    result = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.ACTIVE.value, Job.expires_at < now)
        .values(
            status=JobStatus.EXPIRED.value,
            expired_at=now,
            expired_reason=ExpiredReason.PAST_EXPIRES_AT.value,
        )
        .returning(Job.id)
    )
    return len(result.all())


async def expire_stale(session: AsyncSession, *, cutoff: datetime, now: datetime) -> int:
    result = await session.execute(
        update(Job)
        .where(Job.status == JobStatus.ACTIVE.value, Job.last_seen_at < cutoff)
        .values(
            status=JobStatus.EXPIRED.value,
            expired_at=now,
            expired_reason=ExpiredReason.STALE_NOT_SEEN.value,
        )
        .returning(Job.id)
    )
    return len(result.all())


async def record_run(
    session: AsyncSession,
    *,
    source: str,
    started_at: datetime,
    finished_at: datetime,
    fetched: int = 0,
    inserted: int = 0,
    updated: int = 0,
    unchanged: int = 0,
    failed: int = 0,
    expired: int = 0,
    notes: str | None = None,
) -> None:
    session.add(
        IngestionRun(
            source=source,
            started_at=started_at,
            finished_at=finished_at,
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            failed=failed,
            expired=expired,
            notes=notes,
        )
    )
    await session.flush()


@dataclass(frozen=True, slots=True)
class EmbeddingCandidate:
    job_id: uuid.UUID
    text: str
    content_hash: bytes
    has_vector: bool


async def jobs_needing_embedding(
    session: AsyncSession, *, model_version: str, limit: int
) -> list[EmbeddingCandidate]:
    """Active jobs with no vector for this model, or one computed from older content."""
    rows = await session.execute(
        select(
            Job.id,
            Job.title,
            Job.description,
            Job.content_hash,
            JobEmbedding.job_id.is_not(None),
        )
        .outerjoin(
            JobEmbedding,
            and_(JobEmbedding.job_id == Job.id, JobEmbedding.model_version == model_version),
        )
        .where(
            Job.status == JobStatus.ACTIVE.value,
            or_(JobEmbedding.job_id.is_(None), JobEmbedding.content_hash != Job.content_hash),
        )
        .order_by(Job.id)
        .limit(limit)
    )
    return [
        EmbeddingCandidate(job_id, f"{title}\n{description}", content_hash, bool(has_vector))
        for job_id, title, description, content_hash, has_vector in rows.all()
    ]


async def upsert_embeddings(
    session: AsyncSession,
    *,
    model_version: str,
    rows: Sequence[tuple[uuid.UUID, NDArray[np.float32], bytes]],
    now: datetime,
) -> None:
    base = pg_insert(JobEmbedding).values(
        [
            {
                "job_id": job_id,
                "model_version": model_version,
                "embedding": vector,
                "content_hash": content_hash,
                "embedded_at": now,
            }
            for job_id, vector, content_hash in rows
        ]
    )
    await session.execute(
        base.on_conflict_do_update(
            index_elements=[JobEmbedding.job_id, JobEmbedding.model_version],
            set_={
                "embedding": base.excluded.embedding,
                "content_hash": base.excluded.content_hash,
                "embedded_at": base.excluded.embedded_at,
            },
        )
    )
