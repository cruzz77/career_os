"""What every job source returns (blueprint 6.4, 6.4.2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.enums import EmploymentType, Seniority, WorkMode
from app.domain.jobs import PostingCheck
from app.modules.ingestion.mapping import normalise
from app.modules.ingestion.skills import Section


@dataclass(frozen=True, slots=True)
class PostingLocation:
    raw: str
    city: str | None
    country: str | None
    work_mode: WorkMode


@dataclass(frozen=True, slots=True)
class NormalizedPosting:
    source: str
    source_board: str
    external_id: str
    title: str
    description: str
    company_name: str
    # Every place the posting lists, each with its own stated mode. jobs.location_* stores the
    # primary one; keeping all of them means a multi-location table needs no adapter change.
    locations: tuple[PostingLocation, ...]
    work_mode: WorkMode
    employment_type: EmploymentType
    seniority: Seniority
    posted_at: datetime | None
    expires_at: datetime | None
    source_url: str
    skill_sections: tuple[Section, ...]
    # (field, raw value) a table did not map; counted in ingestion_runs.notes.
    unmapped: tuple[tuple[str, str], ...] = ()

    @property
    def primary_location(self) -> PostingLocation | None:
        with_city = next((loc for loc in self.locations if loc.city), None)
        if with_city is not None:
            return with_city
        with_country = next((loc for loc in self.locations if loc.country), None)
        if with_country is not None:
            return with_country
        return self.locations[0] if self.locations else None

    def content_hash(self) -> bytes:
        """Raw SHA-256 of every stored field: any change means update and re-embed."""
        payload = {
            "title": self.title,
            "description": self.description,
            "company": self.company_name,
            "locations": [
                [loc.raw, loc.city, loc.country, loc.work_mode] for loc in self.locations
            ],
            "work_mode": self.work_mode,
            "employment_type": self.employment_type,
            "seniority": self.seniority,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "source_url": self.source_url,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).digest()

    def canonical_hash(self) -> bytes:
        """The same job across boards or sources: company, title and primary place only."""
        primary = self.primary_location
        place = (primary.city or primary.country or "") if primary else ""
        key = "\x1f".join(normalise(part) or "" for part in (self.company_name, self.title, place))
        return hashlib.sha256(key.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class FetchResult:
    source: str
    board: str
    # True only after HTTP 200 on every page and a full parse; only a complete fetch may expire
    # jobs (blueprint 6.4.2, mechanism 1).
    complete: bool
    postings: tuple[NormalizedPosting, ...]
    # Every id the source listed, including postings that failed normalisation: those are
    # still live at the source and must not be expired as "missing".
    seen_external_ids: frozenset[str]
    failed: int = 0
    error: str | None = None


class JobSource(Protocol):
    @property
    def name(self) -> str: ...

    def boards(self) -> tuple[str, ...]: ...

    async def fetch_all(self, board: str) -> FetchResult: ...

    async def check_posting(self, board: str, external_id: str) -> PostingCheck: ...
