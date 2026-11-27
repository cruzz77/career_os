from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.domain.enums import EmploymentType, PostingStatus, Seniority, WorkMode
from app.modules.ingestion.skills import SectionKind
from app.modules.ingestion.sources.base import PostingLocation
from app.modules.ingestion.sources.config import LeverConfig
from app.modules.ingestion.sources.lever import LeverSource, normalise_lever_posting
from tests.http_fakes import FakeTime, Script, polite_client

CONFIG = LeverConfig.model_validate(
    {
        "version": 1,
        "validated_on": "2026-11-27",
        "companies": [
            {"slug": "acme", "name": "Acme", "instance": "global"},
            {"slug": "euco", "name": "EU Co", "instance": "eu"},
        ],
    }
)
_CREATED_S = 1_764_201_600


def _raw(i: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"00000000-0000-0000-0000-{i:012d}",
        "text": "Senior Backend Engineer",
        "hostedUrl": f"https://jobs.lever.co/acme/{i}",
        "createdAt": _CREATED_S * 1000,
        "workplaceType": "hybrid",
        "country": "IN",
        "categories": {
            "commitment": "Full-time",
            "location": "Bangalore, Karnataka",
            "allLocations": ["Bangalore, Karnataka", "Mumbai"],
            "level": "Senior",
        },
        "descriptionPlain": "We build payments.",
        "lists": [
            {"text": "Requirements", "content": "<li>Python</li><li>Kafka</li>"},
            {"text": "Nice to have", "content": "<li>Go</li>"},
        ],
        "additionalPlain": "Equal opportunity employer.",
    }
    row.update(overrides)
    return row


def test_normalise_full_posting() -> None:
    posting = normalise_lever_posting(_raw(1), board="acme", company_name="Acme")
    assert posting is not None
    assert (posting.source, posting.source_board, posting.company_name) == ("lever", "acme", "Acme")
    assert posting.title == "Senior Backend Engineer"
    assert (posting.work_mode, posting.employment_type, posting.seniority) == (
        WorkMode.HYBRID, EmploymentType.FULL_TIME, Seniority.SENIOR,
    )  # fmt: skip
    assert posting.locations == (
        PostingLocation("Bangalore, Karnataka", "Bengaluru", "IN", WorkMode.HYBRID),
        PostingLocation("Mumbai", "Mumbai", "IN", WorkMode.HYBRID),
    )
    assert posting.posted_at == datetime.fromtimestamp(_CREATED_S, tz=UTC)
    assert posting.expires_at is None
    assert all(
        part in posting.description for part in ("Requirements", "Python", "Equal opportunity")
    )
    by_kind = {
        kind: " ".join(s.text for s in posting.skill_sections if s.kind is kind)
        for kind in SectionKind
    }
    assert "Python" in by_kind[SectionKind.REQUIRED]
    assert "Go" in by_kind[SectionKind.PREFERRED]
    assert not any("Equal opportunity" in s.text for s in posting.skill_sections)
    assert posting.unmapped == ()


def test_unmapped_values_are_reported() -> None:
    row = _raw(1, workplaceType="sometimes")
    row["categories"] = {**row["categories"], "commitment": "Freelance"}
    posting = normalise_lever_posting(row, board="acme", company_name="Acme")
    assert posting is not None
    assert (posting.work_mode, posting.employment_type) == (
        WorkMode.UNKNOWN,
        EmploymentType.UNKNOWN,
    )
    assert set(posting.unmapped) == {("workplaceType", "sometimes"), ("commitment", "freelance")}


@pytest.mark.parametrize(
    "overrides",
    [{"id": ""}, {"text": "  "}, {"hostedUrl": None}, {"descriptionPlain": "", "lists": []}],
    ids=["no-id", "no-title", "no-link-back", "no-text"],
)
def test_postings_that_cannot_be_shown_are_rejected(overrides: dict[str, Any]) -> None:
    assert normalise_lever_posting(_raw(1, **overrides), board="acme", company_name="Acme") is None


async def test_single_short_page_is_a_complete_fetch() -> None:
    script, fake = Script(httpx.Response(200, json=[_raw(1), _raw(2)])), FakeTime()
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("acme")
    assert result.complete
    assert result.error is None
    assert len(result.postings) == 2
    assert len(result.seen_external_ids) == 2
    request = script.requests[0]
    assert str(request.url).startswith("https://api.lever.co/v0/postings/acme?")
    assert dict(request.url.params) == {"mode": "json", "skip": "0", "limit": "100"}


async def test_two_pages_are_followed() -> None:
    first = [_raw(i) for i in range(100)]
    second = [_raw(i) for i in range(100, 105)]
    script, fake = (
        Script(httpx.Response(200, json=first), httpx.Response(200, json=second)),
        FakeTime(),
    )
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("acme")
    assert result.complete
    assert len(result.postings) == 105
    assert script.requests[1].url.params["skip"] == "100"


async def test_a_repeated_page_ends_the_fetch() -> None:
    page = [_raw(i) for i in range(100)]
    script, fake = (
        Script(httpx.Response(200, json=page), httpx.Response(200, json=page)),
        FakeTime(),
    )
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("acme")
    assert result.complete
    assert len(result.postings) == 100
    assert len(script.requests) == 2


async def test_board_404_is_incomplete() -> None:
    body = {"ok": False, "error": "Document not found"}
    script, fake = Script(httpx.Response(404, json=body)), FakeTime()
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("acme")
    assert not result.complete
    assert result.error == "HTTP 404"
    assert result.seen_external_ids == frozenset()


async def test_exhausted_retries_are_incomplete() -> None:
    script, fake = Script(*(httpx.Response(503) for _ in range(4))), FakeTime()
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("acme")
    assert not result.complete
    assert result.error == "HTTP 503"


async def test_malformed_posting_is_failed_but_still_seen() -> None:
    broken = _raw(2, text="")
    script, fake = Script(httpx.Response(200, json=[_raw(1), broken])), FakeTime()
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("acme")
    assert result.complete
    assert result.failed == 1
    assert len(result.postings) == 1
    assert broken["id"] in result.seen_external_ids


async def test_eu_instance_uses_the_eu_host() -> None:
    script, fake = Script(httpx.Response(200, json=[])), FakeTime()
    async with polite_client(script, fake) as http:
        result = await LeverSource(http, CONFIG).fetch_all("euco")
    assert result.complete
    assert result.postings == ()
    assert script.requests[0].url.host == "api.eu.lever.co"


_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(200, json={"id": _ID}), PostingStatus.OPEN),
        (
            httpx.Response(404, json={"ok": False, "error": "Document not found"}),
            PostingStatus.NOT_FOUND,
        ),
        (httpx.Response(410, json={"ok": False, "error": "Gone"}), PostingStatus.NOT_FOUND),
        (httpx.Response(404, text="<html>CDN error</html>"), PostingStatus.ERROR),
        (httpx.Response(503), PostingStatus.ERROR),
        (httpx.Response(200, json={"id": "someone-else"}), PostingStatus.ERROR),
    ],
    ids=["open", "404-json", "410-json", "404-html", "503", "wrong-id"],
)
async def test_check_posting(response: httpx.Response, expected: PostingStatus) -> None:
    script, fake = Script(response), FakeTime()
    async with polite_client(script, fake) as http:
        check = await LeverSource(http, CONFIG).check_posting("acme", _ID)
    assert check.status is expected
    assert len(script.requests) == 1


async def test_check_posting_quotes_the_id_into_the_path() -> None:
    script, fake = Script(httpx.Response(404, json={"error": "x"})), FakeTime()
    async with polite_client(script, fake) as http:
        await LeverSource(http, CONFIG).check_posting("acme", "../../admin")
    assert script.requests[0].url.raw_path.startswith(b"/v0/postings/acme/..%2F..%2Fadmin")
