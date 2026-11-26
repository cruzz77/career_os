"""Bookkeeping rows for scheduled jobs (sync, embed, purge)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestionRun


async def record_purge(
    session: AsyncSession,
    *,
    started_at: datetime,
    finished_at: datetime,
    purged: int,
    notes: str,
) -> None:
    session.add(
        IngestionRun(
            source="purge",
            started_at=started_at,
            finished_at=finished_at,
            purged=purged,
            notes=notes,
        )
    )
    await session.flush()
