"""Daily hard delete of long-expired jobs and dead sessions (blueprint 6.4.2, 6.6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from app.core.logging import get_logger
from app.db.session import Database
from app.repositories import ingestion_runs, sessions
from app.repositories import jobs as jobs_repo

PURGE_EXPIRED_AFTER: Final = timedelta(days=30)

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    jobs_deleted: int
    sessions_deleted: int
    judged_jobs_protected: bool


def utc_now() -> datetime:
    return datetime.now(UTC)


class PurgeService:
    def __init__(
        self,
        database: Database,
        *,
        absolute_session_ttl: timedelta,
        retention: timedelta = PURGE_EXPIRED_AFTER,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._absolute_session_ttl = absolute_session_ttl
        self._retention = retention
        self._clock = clock

    async def run(self) -> PurgeResult:
        started_at = self._clock()
        # One transaction: the deletions and the ingestion_runs row commit together or not at
        # all, so the run log never claims a purge that was rolled back.
        async with self._database.session() as session, session.begin():
            protect_judged = await jobs_repo.eval_judgements_exists(session)
            if not protect_judged:
                _log.warning("purge_without_eval_judgements", detail="only saved jobs protected")
            jobs_deleted = await jobs_repo.purge_expired(
                session,
                expired_before=started_at - self._retention,
                spare_judged=protect_judged,
            )
            sessions_deleted = await sessions.delete_expired(
                session, now=started_at, absolute_ttl=self._absolute_session_ttl
            )
            await ingestion_runs.record_purge(
                session,
                started_at=started_at,
                finished_at=self._clock(),
                purged=jobs_deleted,
                notes=(
                    f"sessions_deleted={sessions_deleted}; "
                    f"judged_jobs_protected={str(protect_judged).lower()}"
                ),
            )
        result = PurgeResult(jobs_deleted, sessions_deleted, protect_judged)
        _log.info(
            "purge_finished",
            jobs_deleted=jobs_deleted,
            sessions_deleted=sessions_deleted,
            judged_jobs_protected=protect_judged,
        )
        return result
