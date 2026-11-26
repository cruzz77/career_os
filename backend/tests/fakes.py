"""Test doubles shared across test modules."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.domain.enums import PostingStatus
from app.domain.jobs import PostingCheck


@dataclass
class FakeChecker:
    """A PostingChecker with a scripted answer that records every call it receives."""

    status: PostingStatus = PostingStatus.OPEN
    delay_s: float = 0.0
    error: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def check_posting(self, slug: str, external_id: str) -> PostingCheck:
        self.calls.append((slug, external_id))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return PostingCheck(status=self.status)
