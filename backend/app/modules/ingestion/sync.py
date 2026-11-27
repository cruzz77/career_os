"""Hourly job sync (blueprint 6.4, 6.4.2): upsert, skills, expiry, safety valve, sweeps.

Fetching happens before the board's transaction opens, so no connection is held during HTTP.
Only a complete fetch can expire jobs; postings the source listed but that failed
normalisation are still live there, so they are touched, never expired.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.core.logging import get_logger
from app.db.session import Database
from app.domain.enums import ExpiredReason, JobStatus
from app.modules.ingestion.mapping import normalise
from app.modules.ingestion.skills import SkillExtractor
from app.modules.ingestion.sources.base import FetchResult, JobSource, NormalizedPosting
from app.repositories import job_sync as repo
from app.repositories import skills as skills_repo

STALE_AFTER: Final = timedelta(hours=24)
# Safety valve: one fetch may not expire more than half of a board's active jobs (and more
# than five): an empty or truncated response is likelier than a mass closure.
VALVE_SHARE: Final = 0.5
VALVE_MIN_JOBS: Final = 5

_log = get_logger(__name__)


class SkillCatalogueEmptyError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class BoardSyncResult:
    source: str
    board: str
    complete: bool
    error: str | None
    fetched: int
    inserted: int
    updated: int
    unchanged: int
    reactivated: int
    failed: int
    expired: int
    skills_rewritten: int
    valve_tripped: bool


@dataclass(frozen=True, slots=True)
class SyncReport:
    boards: tuple[BoardSyncResult, ...]
    expired_past_deadline: int
    expired_stale: int


def _job_values(posting: NormalizedPosting, company_id: uuid.UUID) -> dict[str, object]:
    primary = posting.primary_location
    return {
        "company_id": company_id,
        "title": posting.title,
        "description": posting.description,
        "location_city": primary.city if primary else None,
        "location_country": primary.country if primary else None,
        "work_mode": posting.work_mode.value,
        "employment_type": posting.employment_type.value,
        "seniority": posting.seniority.value,
        "posted_at": posting.posted_at,
        "expires_at": posting.expires_at,
        "source_url": posting.source_url,
        "content_hash": posting.content_hash(),
        "canonical_hash": posting.canonical_hash(),
        "is_synthetic": False,
    }


class SyncService:
    def __init__(
        self,
        database: Database,
        sources: Sequence[JobSource],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._sources = sources
        self._clock = clock

    async def run(self) -> SyncReport:
        extractor, ids = await self._load_skills()
        results: list[BoardSyncResult] = []
        for source in self._sources:
            for board in source.boards():
                fetch = await source.fetch_all(board)
                results.append(await self._apply(fetch, extractor, ids))
        past, stale = await self._sweep()
        return SyncReport(tuple(results), past, stale)

    async def _load_skills(self) -> tuple[SkillExtractor, Mapping[str, uuid.UUID]]:
        async with self._database.session() as session:
            aliases = await skills_repo.load_aliases(session)
            ids = await repo.skill_ids(session)
        if not aliases:
            raise SkillCatalogueEmptyError(
                "skill_aliases is empty: run `python -m app.cli seed-skills` first"
            )
        return SkillExtractor(aliases), ids

    async def _apply(
        self, fetch: FetchResult, extractor: SkillExtractor, ids: Mapping[str, uuid.UUID]
    ) -> BoardSyncResult:
        now = self._clock()
        inserted = updated = unchanged = reactivated = expired = rewritten = 0
        valve = False
        async with self._database.session() as session, session.begin():
            stored = await repo.board_jobs(session, source=fetch.source, board=fetch.board)
            current = await repo.board_skills(session, [job.id for job in stored.values()])
            company_id: uuid.UUID | None = None
            for posting in fetch.postings:
                if company_id is None:
                    company_id = await repo.upsert_company(
                        session,
                        name=posting.company_name,
                        normalized_name=normalise(posting.company_name) or posting.source_board,
                    )
                found = extractor.extract(
                    posting.skill_sections,
                    exclude_aliases=(posting.company_name, posting.source_board),
                )
                wanted = {name: req.value for name, req in found.items() if name in ids}
                skills_text = ", ".join(sorted(wanted))
                job = stored.get(posting.external_id)
                if job is None:
                    job_id = await repo.insert_job(
                        session,
                        {
                            **_job_values(posting, company_id),
                            "source": posting.source,
                            "source_board": posting.source_board,
                            "external_id": posting.external_id,
                            "status": JobStatus.ACTIVE.value,
                            "skills_text": skills_text,
                            "first_seen_at": now,
                            "last_seen_at": now,
                            "last_verified_at": now,
                        },
                    )
                    await repo.replace_job_skills(
                        session, job_id, {ids[name]: req for name, req in wanted.items()}
                    )
                    inserted += 1
                    continue
                values: dict[str, object] = {"last_seen_at": now, "last_verified_at": now}
                if job.content_hash != posting.content_hash():
                    values |= _job_values(posting, company_id)
                    updated += 1
                else:
                    unchanged += 1
                if job.status == JobStatus.EXPIRED.value:
                    values |= {
                        "status": JobStatus.ACTIVE.value,
                        "expired_at": None,
                        "expired_reason": None,
                    }
                    reactivated += 1
                if current.get(job.id, {}) != wanted:
                    values["skills_text"] = skills_text
                    await repo.replace_job_skills(
                        session, job.id, {ids[name]: req for name, req in wanted.items()}
                    )
                    rewritten += 1
                await repo.update_job(session, job.id, values)

            parsed = {posting.external_id for posting in fetch.postings}
            listed_unparsed = [
                job.id
                for external_id, job in stored.items()
                if external_id in fetch.seen_external_ids and external_id not in parsed
            ]
            await repo.touch_seen(session, listed_unparsed, now=now)

            missing: list[uuid.UUID] = []
            if fetch.complete:
                active = [job for job in stored.values() if job.status == JobStatus.ACTIVE.value]
                missing = [j.id for j in active if j.external_id not in fetch.seen_external_ids]
                if len(missing) > VALVE_MIN_JOBS and len(missing) > VALVE_SHARE * len(active):
                    valve = True
                    _log.warning(
                        "sync_safety_valve",
                        source=fetch.source,
                        board=fetch.board,
                        would_expire=len(missing),
                        active=len(active),
                    )
                else:
                    expired = await repo.expire_jobs(
                        session, missing, reason=ExpiredReason.MISSING_FROM_SOURCE, now=now
                    )

            unmapped = Counter(
                f"{field}={raw}" for p in fetch.postings for field, raw in p.unmapped
            )
            notes = {
                "board": fetch.board,
                "complete": fetch.complete,
                "error": fetch.error,
                "valve_tripped": valve,
                "missing": len(missing),
                "reactivated": reactivated,
                "skills_rewritten": rewritten,
                "unmapped": dict(sorted(unmapped.items())),
            }
            await repo.record_run(
                session,
                source=fetch.source,
                started_at=now,
                finished_at=self._clock(),
                fetched=len(fetch.seen_external_ids),
                inserted=inserted,
                updated=updated,
                unchanged=unchanged,
                failed=fetch.failed,
                expired=expired,
                notes=json.dumps(notes, sort_keys=True),
            )
        return BoardSyncResult(
            source=fetch.source,
            board=fetch.board,
            complete=fetch.complete,
            error=fetch.error,
            fetched=len(fetch.seen_external_ids),
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            reactivated=reactivated,
            failed=fetch.failed,
            expired=expired,
            skills_rewritten=rewritten,
            valve_tripped=valve,
        )

    async def _sweep(self) -> tuple[int, int]:
        now = self._clock()
        async with self._database.session() as session, session.begin():
            past = await repo.expire_past_deadline(session, now=now)
            stale = await repo.expire_stale(session, cutoff=now - STALE_AFTER, now=now)
            await repo.record_run(
                session,
                source="sweep",
                started_at=now,
                finished_at=self._clock(),
                expired=past + stale,
                notes=json.dumps({"past_expires_at": past, "stale_not_seen": stale}),
            )
        return past, stale
