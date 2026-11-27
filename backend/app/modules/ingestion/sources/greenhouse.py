"""Greenhouse Job Board API adapter (blueprint 6.4.1, 6.4.2).

Docs: docs.greenhouse.io/job-board.html.

Behaviour measured on 2026-11-27: `jobs?content=true` returns every published job with
`meta.total`; an unknown job and an unknown board both answer 404 with a JSON body
{"status": 404, "error": "Job not found"}. `content` is entity-escaped HTML.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.domain.enums import EmploymentType, PostingStatus, Seniority, WorkMode
from app.domain.jobs import PostingCheck
from app.domain.locations import country_code, parse_location, split_locations
from app.modules.ingestion import mapping
from app.modules.ingestion.mapping import FieldOutcome
from app.modules.ingestion.skills import Section, SectionKind, html_to_text, split_html_sections
from app.modules.ingestion.sources.base import FetchResult, NormalizedPosting, PostingLocation
from app.modules.ingestion.sources.config import GreenhouseBoard, GreenhouseConfig
from app.modules.ingestion.sources.http import PoliteHttpClient, SourceUnavailableError

GREENHOUSE: Final = "greenhouse"
BASE_URL: Final = "https://boards-api.greenhouse.io/v1/boards"

_log = get_logger(__name__)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _id(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return _text(value)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _deadline(value: object) -> datetime | None:
    parsed = _parse_datetime(value)
    # A bare date is a deadline that includes its last day.
    if parsed is not None and len(_text(value)) == len("YYYY-MM-DD"):
        return parsed + timedelta(days=1)
    return parsed


def _metadata(row: Mapping[str, Any]) -> dict[str, object]:
    fields: dict[str, object] = {}
    items = row.get("metadata")
    for item in items if isinstance(items, list) else []:
        entry = _mapping(item)
        name = _text(entry.get("name"))
        if name and name not in fields:
            fields[name] = entry.get("value")
    return fields


def _metadata_country(value: object) -> str | None:
    if isinstance(value, list):
        named = [item for item in value if _text(item)]
        value = named[0] if len(named) == 1 else None  # several countries name none
    return country_code(value) if isinstance(value, str) else None


def _locations(
    raw: str, tokens: Mapping[str, WorkMode], job_mode: WorkMode, default_country: str | None
) -> tuple[PostingLocation, ...]:
    locations: list[PostingLocation] = []
    seen: set[tuple[str | None, ...]] = set()
    for segment in (part for part in raw.split(";") if part.strip()):
        mode = mapping.segment_work_mode(segment, tokens) if tokens else None
        for place in split_locations(segment):
            parsed = parse_location(place, default_country=default_country)
            key = (
                (parsed.city, parsed.country)
                if parsed.city or parsed.country
                else (place.casefold(),)
            )
            if key not in seen:
                seen.add(key)
                locations.append(
                    PostingLocation(place, parsed.city, parsed.country, mode or job_mode)
                )
    return tuple(locations)


def normalise_greenhouse_posting(
    row: Mapping[str, Any], *, board: GreenhouseBoard
) -> NormalizedPosting | None:
    """None when the posting cannot be shown honestly: no id, title, link-back URL or text."""
    external_id, title = _id(row.get("id")), _text(row.get("title"))
    url = _text(row.get("absolute_url"))
    description = html_to_text(row.get("content"))
    if not (external_id and title and url and description):
        return None

    fields = _metadata(row)
    meta = board.metadata
    unmapped: list[tuple[str, str]] = []

    employment: FieldOutcome[EmploymentType] = FieldOutcome(EmploymentType.UNKNOWN)
    if meta.employment_type is not None:
        employment = mapping.metadata_lookup(
            fields.get(meta.employment_type.field),
            meta.employment_type.values,
            EmploymentType.UNKNOWN,
        )
        if employment.unmapped:
            unmapped.append((meta.employment_type.field, employment.unmapped))
    meta_mode: FieldOutcome[WorkMode] = FieldOutcome(WorkMode.UNKNOWN)
    if meta.work_mode is not None:
        meta_mode = mapping.metadata_lookup(
            fields.get(meta.work_mode.field), meta.work_mode.values, WorkMode.UNKNOWN
        )
        if meta_mode.unmapped:
            unmapped.append((meta.work_mode.field, meta_mode.unmapped))
    structured_seniority: FieldOutcome[Seniority] = FieldOutcome(Seniority.UNKNOWN)
    if meta.seniority is not None:
        structured_seniority = mapping.metadata_lookup(
            fields.get(meta.seniority.field), meta.seniority.values, Seniority.UNKNOWN
        )
        if structured_seniority.unmapped:
            unmapped.append((meta.seniority.field, structured_seniority.unmapped))
    default_country = (
        _metadata_country(fields.get(meta.country.field)) if meta.country is not None else None
    )

    location_raw = _text(_mapping(row.get("location")).get("name"))
    location_mode = mapping.location_work_mode(location_raw or None, board.location_work_mode)
    work_mode = mapping.combine_work_mode(location_mode, meta_mode.value)

    posted_at = _parse_datetime(row.get("first_published"))
    expires_at = _deadline(row.get("application_deadline"))
    if expires_at is not None and posted_at is not None and expires_at < posted_at:
        # Contradicts the posting date; jobs has CHECK(expires_at >= posted_at). Not stored.
        unmapped.append(("application_deadline", _text(row.get("application_deadline"))))
        expires_at = None

    sections = [Section(SectionKind.NEUTRAL, title)]
    sections += split_html_sections(row.get("content"), company=board.name)
    return NormalizedPosting(
        source=GREENHOUSE,
        source_board=board.token,
        external_id=external_id,
        title=title,
        description=description,
        company_name=board.name,
        locations=_locations(location_raw, board.location_work_mode, work_mode, default_country),
        work_mode=work_mode,
        employment_type=employment.value,
        seniority=mapping.combine_seniority(structured_seniority.value, title),
        posted_at=posted_at,
        expires_at=expires_at,
        source_url=url,
        skill_sections=tuple(sections),
        unmapped=tuple(unmapped),
    )


def _has_error_body(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and "error" in body


class GreenhouseSource:
    def __init__(self, http: PoliteHttpClient, config: GreenhouseConfig) -> None:
        self._http = http
        self._config = config

    @property
    def name(self) -> str:
        return GREENHOUSE

    def boards(self) -> tuple[str, ...]:
        return tuple(board.token for board in self._config.boards)

    async def fetch_all(self, board: str) -> FetchResult:
        config = self._config.board(board)
        url = f"{BASE_URL}/{quote(board, safe='')}/jobs"
        try:
            response = await self._http.get(url, params={"content": "true"})
        except SourceUnavailableError as exc:
            return self._incomplete(board, exc.reason)
        if response.status_code != 200:
            return self._incomplete(board, f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            return self._incomplete(board, "response is not JSON")
        jobs = _mapping(body).get("jobs")
        total = _mapping(_mapping(body).get("meta")).get("total")
        if not isinstance(jobs, list):
            return self._incomplete(board, "response has no jobs list")
        if not isinstance(total, int) or isinstance(total, bool) or total != len(jobs):
            # Without a matching total the fetch cannot be proven complete, and an incomplete
            # fetch must never expire anything (blueprint 6.4.2, mechanism 1).
            return self._incomplete(board, f"meta.total {total!r} != {len(jobs)} jobs")

        ids: set[str] = set()
        postings: list[NormalizedPosting] = []
        failed = 0
        for item in jobs:
            row = _mapping(item)
            external_id = _id(row.get("id"))
            if not external_id:
                failed += 1
                continue
            ids.add(external_id)
            posting = normalise_greenhouse_posting(row, board=config)
            if posting is None:
                failed += 1
            else:
                postings.append(posting)
        return FetchResult(
            source=GREENHOUSE,
            board=board,
            complete=True,
            postings=tuple(postings),
            seen_external_ids=frozenset(ids),
            failed=failed,
        )

    async def check_posting(self, board: str, external_id: str) -> PostingCheck:
        if not external_id.isdigit():
            return PostingCheck(status=PostingStatus.ERROR)  # Greenhouse ids are numeric
        url = f"{BASE_URL}/{quote(board, safe='')}/jobs/{external_id}"
        try:
            response = await self._http.get(url, max_attempts=1)
        except SourceUnavailableError:
            return PostingCheck(status=PostingStatus.ERROR)
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                return PostingCheck(status=PostingStatus.ERROR)
            same = _id(_mapping(body).get("id")) == external_id
            return PostingCheck(status=PostingStatus.OPEN if same else PostingStatus.ERROR)
        if response.status_code in (404, 410) and _has_error_body(response):
            return PostingCheck(status=PostingStatus.NOT_FOUND)
        return PostingCheck(status=PostingStatus.ERROR)

    def _incomplete(self, board: str, reason: str) -> FetchResult:
        _log.warning("source_fetch_incomplete", source=GREENHOUSE, board=board, reason=reason)
        return FetchResult(
            source=GREENHOUSE,
            board=board,
            complete=False,
            postings=(),
            seen_external_ids=frozenset(),
            error=reason,
        )
