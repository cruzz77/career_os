"""Live freshness check for a single job (blueprint 6.4.2, mechanism 2)."""

from __future__ import annotations

import asyncio
import functools
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.session import Database
from app.domain.enums import (
    Availability,
    EmploymentType,
    ExpiredReason,
    JobStatus,
    PostingStatus,
    Seniority,
    SkillRequirement,
    WorkMode,
)
from app.domain.jobs import JobSkillView, JobView, PostingCheck
from app.repositories import jobs as jobs_repo
from app.repositories.jobs import JobDetail

FRESHNESS_TTL: Final = timedelta(minutes=15)
LIVE_CHECK_TIMEOUT_S: Final = 2.0

_log = get_logger(__name__)


class PostingChecker(Protocol):
    """The one capability of Retrieval's JobSource this service needs."""

    async def check_posting(self, slug: str, external_id: str) -> PostingCheck: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobFreshnessService:
    def __init__(
        self,
        database: Database,
        checkers: Mapping[str, PostingChecker],
        *,
        ttl: timedelta = FRESHNESS_TTL,
        timeout_s: float = LIVE_CHECK_TIMEOUT_S,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._checkers = checkers
        self._ttl = ttl
        self._timeout_s = timeout_s
        self._clock = clock
        self._in_flight: dict[uuid.UUID, asyncio.Task[Availability]] = {}

    async def ensure_fresh(self, job_id: uuid.UUID) -> JobView:
        detail = await self._load(job_id)
        if detail.status == JobStatus.EXPIRED:
            return _to_view(detail, Availability.CLOSED)
        if self._clock() - detail.last_verified_at < self._ttl:
            return _to_view(detail, Availability.OPEN)
        availability = await self._check_once(detail)
        refreshed = await self._load(job_id)
        if refreshed.status == JobStatus.EXPIRED:
            availability = Availability.CLOSED
        return _to_view(refreshed, availability)

    async def _load(self, job_id: uuid.UUID) -> JobDetail:
        async with self._database.session() as session:
            detail = await jobs_repo.get_detail(session, job_id)
        if detail is None:
            raise NotFoundError("Job not found")
        return detail

    async def _check_once(self, detail: JobDetail) -> Availability:
        # Several users opening the same stale job share one outbound call. No await between
        # the lookup and the insert, so two requests cannot both start a check.
        task = self._in_flight.get(detail.id)
        if task is None:
            task = asyncio.create_task(self._check_and_record(detail))
            self._in_flight[detail.id] = task
            task.add_done_callback(functools.partial(self._forget_in_flight, detail.id))
        # shield: one caller disconnecting must not cancel the check the others are awaiting.
        return await asyncio.shield(task)

    def _forget_in_flight(self, job_id: uuid.UUID, _done: asyncio.Task[Availability]) -> None:
        self._in_flight.pop(job_id, None)

    async def _check_and_record(self, detail: JobDetail) -> Availability:
        checker = self._checkers.get(detail.source)
        if checker is None:
            _log.info("live_check_skipped", job_id=str(detail.id), source=detail.source)
            return Availability.UNVERIFIED
        try:
            async with asyncio.timeout(self._timeout_s):
                result = await checker.check_posting(detail.source_board, detail.external_id)
        except TimeoutError:
            _log.warning("live_check_timeout", job_id=str(detail.id), source=detail.source)
            return Availability.UNVERIFIED
        except Exception:
            # A failing source must degrade to "unverified", never to a 500 on the job page.
            _log.exception("live_check_failed", job_id=str(detail.id), source=detail.source)
            return Availability.UNVERIFIED
        return await self._record(detail, result.status)

    async def _record(self, detail: JobDetail, status: PostingStatus) -> Availability:
        now = self._clock()
        if status is PostingStatus.OPEN:
            async with self._database.session() as session, session.begin():
                await jobs_repo.mark_verified(session, job_id=detail.id, verified_at=now)
            return Availability.OPEN
        if status is PostingStatus.NOT_FOUND:
            async with self._database.session() as session, session.begin():
                await jobs_repo.expire(
                    session,
                    job_id=detail.id,
                    reason=ExpiredReason.SOURCE_NOT_FOUND,
                    expired_at=now,
                )
            _log.info("job_expired", job_id=str(detail.id), reason="source_not_found")
            return Availability.CLOSED
        return Availability.UNVERIFIED


def _to_view(detail: JobDetail, availability: Availability) -> JobView:
    return JobView(
        id=detail.id,
        title=detail.title,
        company=detail.company_name,
        description=detail.description,
        location_city=detail.location_city,
        location_country=detail.location_country,
        work_mode=WorkMode(detail.work_mode),
        employment_type=EmploymentType(detail.employment_type),
        seniority=Seniority(detail.seniority),
        posted_at=detail.posted_at,
        source=detail.source,
        source_url=detail.source_url,
        skills=[
            JobSkillView(name=name, requirement=SkillRequirement(requirement))
            for name, requirement in detail.skills
        ],
        status=JobStatus(detail.status),
        availability=availability,
        last_verified_at=detail.last_verified_at,
        expired_at=detail.expired_at,
    )
