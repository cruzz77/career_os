"""Queries on a single job: the detail read and the freshness writes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company, Job, JobSkill, SavedJob, Skill
from app.domain.enums import ExpiredReason, JobStatus


@dataclass(frozen=True, slots=True)
class JobDetail:
    id: uuid.UUID
    source: str
    source_board: str
    external_id: str
    title: str
    description: str
    company_name: str | None
    location_city: str | None
    location_country: str | None
    work_mode: str
    employment_type: str
    seniority: str
    posted_at: datetime | None
    source_url: str | None
    status: str
    expired_at: datetime | None
    last_verified_at: datetime
    skills: tuple[tuple[str, str], ...]


async def get_detail(session: AsyncSession, job_id: uuid.UUID) -> JobDetail | None:
    row = (
        await session.execute(
            select(Job, Company.name)
            .outerjoin(Company, Job.company_id == Company.id)
            .where(Job.id == job_id)
        )
    ).first()
    if row is None:
        return None
    job, company_name = row
    skill_rows = await session.execute(
        select(Skill.canonical_name, JobSkill.requirement)
        .join(JobSkill, JobSkill.skill_id == Skill.id)
        .where(JobSkill.job_id == job_id)
        # 'required' sorts after 'preferred', so descending puts required skills first.
        .order_by(JobSkill.requirement.desc(), Skill.canonical_name)
    )
    return JobDetail(
        id=job.id,
        source=job.source,
        source_board=job.source_board,
        external_id=job.external_id,
        title=job.title,
        description=job.description,
        company_name=company_name,
        location_city=job.location_city,
        location_country=job.location_country,
        work_mode=job.work_mode,
        employment_type=job.employment_type,
        seniority=job.seniority,
        posted_at=job.posted_at,
        source_url=job.source_url,
        status=job.status,
        expired_at=job.expired_at,
        last_verified_at=job.last_verified_at,
        skills=tuple((name, requirement) for name, requirement in skill_rows.all()),
    )


async def mark_verified(session: AsyncSession, *, job_id: uuid.UUID, verified_at: datetime) -> None:
    # GREATEST: a slow check finishing after a newer one must not move the timestamp back.
    await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.ACTIVE.value)
        .values(last_verified_at=func.greatest(Job.last_verified_at, verified_at))
    )


async def expire(
    session: AsyncSession, *, job_id: uuid.UUID, reason: ExpiredReason, expired_at: datetime
) -> bool:
    """False when the job was already expired (another path got there first)."""
    expired = await session.scalar(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.ACTIVE.value)
        .values(status=JobStatus.EXPIRED.value, expired_at=expired_at, expired_reason=reason.value)
        .returning(Job.id)
    )
    return expired is not None


async def eval_judgements_exists(session: AsyncSession) -> bool:
    """eval_judgements arrives with migration 0003; until then there is nothing to protect."""
    found = await session.scalar(text("SELECT to_regclass('public.eval_judgements') IS NOT NULL"))
    return bool(found)


async def purge_expired(
    session: AsyncSession, *, expired_before: datetime, spare_judged: bool
) -> int:
    """Hard-delete long-expired jobs, never ones a user saved or MatchBench labelled.
    job_skills and job_embeddings go with them through ON DELETE CASCADE."""
    statement = delete(Job).where(
        Job.status == JobStatus.EXPIRED.value,
        Job.expired_at < expired_before,
        ~exists().where(SavedJob.job_id == Job.id),
    )
    if spare_judged:
        statement = statement.where(
            text("NOT EXISTS (SELECT 1 FROM eval_judgements ej WHERE ej.job_id = jobs.id)")
        )
    deleted = await session.scalars(statement.returning(Job.id))
    return len(deleted.all())
