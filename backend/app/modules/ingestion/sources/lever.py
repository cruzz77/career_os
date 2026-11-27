"""Lever Postings API adapter (blueprint 6.4.1, 6.4.2; docs: github.com/lever/postings-api).

Behaviour measured on 2026-11-27: the list endpoint returns published postings, pages with
skip/limit; an unknown posting and an unknown company both answer 404 with a JSON body
{"ok": false, "error": "Document not found"}.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import quote

import httpx

from app.core.logging import get_logger
from app.domain.enums import PostingStatus, WorkMode
from app.domain.jobs import PostingCheck
from app.domain.locations import parse_location, split_locations
from app.modules.ingestion import mapping
from app.modules.ingestion.skills import (
    Section,
    SectionKind,
    heading_kind,
    html_to_text,
    normalise_heading,
    refine_sections,
    split_lines_into_sections,
)
from app.modules.ingestion.sources.base import FetchResult, NormalizedPosting, PostingLocation
from app.modules.ingestion.sources.config import LeverConfig
from app.modules.ingestion.sources.http import PoliteHttpClient, SourceUnavailableError

LEVER: Final = "lever"
PAGE_SIZE: Final = 100
MAX_PAGES: Final = 50
_HOSTS: Final[Mapping[str, str]] = {
    "global": "https://api.lever.co",
    "eu": "https://api.eu.lever.co",
}

_log = get_logger(__name__)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lever_locations(
    categories: Mapping[str, Any], country: object, mode: WorkMode
) -> tuple[PostingLocation, ...]:
    primary = _text(categories.get("location"))
    listed = categories.get("allLocations")
    raws = [_text(item) for item in listed if _text(item)] if isinstance(listed, list) else []
    if primary and primary not in raws:
        raws.insert(0, primary)
    default_country = _text(country) or None
    locations: list[PostingLocation] = []
    seen: set[tuple[str | None, ...]] = set()
    for raw in raws:
        for place in split_locations(raw):
            # Lever's `country` describes the primary location only.
            parsed = parse_location(
                place, default_country=default_country if raw == primary else None
            )
            key = (
                (parsed.city, parsed.country)
                if parsed.city or parsed.country
                else (place.casefold(),)
            )
            if key not in seen:
                seen.add(key)
                locations.append(PostingLocation(place, parsed.city, parsed.country, mode))
    return tuple(locations)


def normalise_lever_posting(
    row: Mapping[str, Any], *, board: str, company_name: str
) -> NormalizedPosting | None:
    """None when the posting cannot be shown honestly: no id, title, link-back URL or text."""
    external_id, title, url = (
        _text(row.get("id")),
        _text(row.get("text")),
        _text(row.get("hostedUrl")),
    )
    if not (external_id and title and url):
        return None
    categories = _mapping(row.get("categories"))

    parts: list[str] = []
    sections: list[Section] = [Section(SectionKind.NEUTRAL, title)]
    plain = _text(row.get("descriptionPlain"))
    if plain:
        parts.append(plain)
        sections += refine_sections([Section(SectionKind.NEUTRAL, plain)], company=company_name)
    lists = row.get("lists")
    for item in lists if isinstance(lists, list) else []:
        entry = _mapping(item)
        heading, body = _text(entry.get("text")), html_to_text(entry.get("content"))
        parts += [part for part in (heading, body) if part]
        kind = heading_kind(normalise_heading(heading), company_name) or SectionKind.NEUTRAL
        sections += split_lines_into_sections(body, kind, company=company_name)
    # Kept for search and display; not read for skills (company boilerplate in practice).
    additional = _text(row.get("additionalPlain"))
    if additional:
        parts.append(additional)
    if not (plain or any(_text(_mapping(item).get("content")) for item in lists or [])):
        return None

    work = mapping.lever_work_mode(row.get("workplaceType"))
    employment = mapping.lever_employment_type(categories.get("commitment"))
    level = mapping.lever_level(categories.get("level"))
    created = row.get("createdAt")
    posted_at = (
        datetime.fromtimestamp(created / 1000, tz=UTC)
        if isinstance(created, int | float) and not isinstance(created, bool)
        else None
    )
    unmapped = tuple(
        (field, raw)
        for field, raw in (
            ("workplaceType", work.unmapped),
            ("commitment", employment.unmapped),
            ("level", level.unmapped),
        )
        if raw is not None
    )
    return NormalizedPosting(
        source=LEVER,
        source_board=board,
        external_id=external_id,
        title=title,
        description="\n\n".join(parts),
        company_name=company_name,
        locations=_lever_locations(categories, row.get("country"), work.value),
        work_mode=work.value,
        employment_type=employment.value,
        seniority=mapping.combine_seniority(level.value, title),
        posted_at=posted_at,
        expires_at=None,  # Lever publishes no closing date
        source_url=url,
        skill_sections=tuple(sections),
        unmapped=unmapped,
    )


def _has_error_body(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and "error" in body


class LeverSource:
    def __init__(self, http: PoliteHttpClient, config: LeverConfig) -> None:
        self._http = http
        self._config = config

    @property
    def name(self) -> str:
        return LEVER

    def boards(self) -> tuple[str, ...]:
        return tuple(company.slug for company in self._config.companies)

    def _base_url(self, slug: str) -> str:
        company = self._config.company(slug)
        return f"{_HOSTS[company.instance]}/v0/postings/{quote(slug, safe='')}"

    async def fetch_all(self, board: str) -> FetchResult:
        company = self._config.company(board)
        url = self._base_url(board)
        rows: list[Mapping[str, Any]] = []
        ids: set[str] = set()
        failed = 0
        for page in range(MAX_PAGES):
            params = {"mode": "json", "skip": str(page * PAGE_SIZE), "limit": str(PAGE_SIZE)}
            try:
                response = await self._http.get(url, params=params)
            except SourceUnavailableError as exc:
                return self._incomplete(board, exc.reason)
            if response.status_code != 200:
                # A 404 here means the whole board is gone or renamed: expire nothing; the 24 h
                # stale sweep covers it if it stays gone (blueprint 6.4.2, mechanism 4).
                return self._incomplete(board, f"HTTP {response.status_code}")
            try:
                batch = response.json()
            except ValueError:
                return self._incomplete(board, "response is not JSON")
            if not isinstance(batch, list):
                return self._incomplete(board, "response is not a list")
            new = 0
            for item in batch:
                entry = _mapping(item)
                external_id = _text(entry.get("id"))
                if not external_id:
                    failed += 1
                    continue
                if external_id not in ids:
                    ids.add(external_id)
                    rows.append(entry)
                    new += 1
            # A short page is the end; a page of only known ids means skip was ignored.
            if len(batch) < PAGE_SIZE or new == 0:
                break
        else:
            return self._incomplete(board, f"more than {MAX_PAGES} pages")

        postings: list[NormalizedPosting] = []
        for row in rows:
            posting = normalise_lever_posting(row, board=board, company_name=company.name)
            if posting is None:
                failed += 1
            else:
                postings.append(posting)
        return FetchResult(
            source=LEVER,
            board=board,
            complete=True,
            postings=tuple(postings),
            seen_external_ids=frozenset(ids),
            failed=failed,
        )

    async def check_posting(self, board: str, external_id: str) -> PostingCheck:
        url = f"{self._base_url(board)}/{quote(external_id, safe='')}"
        try:
            # One attempt: the caller's 2 s budget leaves no room for a retry.
            response = await self._http.get(url, params={"mode": "json"}, max_attempts=1)
        except SourceUnavailableError:
            return PostingCheck(status=PostingStatus.ERROR)
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                return PostingCheck(status=PostingStatus.ERROR)
            same = isinstance(body, dict) and _text(body.get("id")) == external_id
            return PostingCheck(status=PostingStatus.OPEN if same else PostingStatus.ERROR)
        if response.status_code in (404, 410) and _has_error_body(response):
            return PostingCheck(status=PostingStatus.NOT_FOUND)
        return PostingCheck(status=PostingStatus.ERROR)

    def _incomplete(self, board: str, reason: str) -> FetchResult:
        _log.warning("source_fetch_incomplete", source=LEVER, board=board, reason=reason)
        return FetchResult(
            source=LEVER,
            board=board,
            complete=False,
            postings=(),
            seen_external_ids=frozenset(),
            error=reason,
        )
