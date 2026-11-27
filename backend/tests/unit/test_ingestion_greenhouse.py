from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.domain.enums import EmploymentType, PostingStatus, Seniority, WorkMode
from app.modules.ingestion.skills import SectionKind
from app.modules.ingestion.sources.base import PostingLocation
from app.modules.ingestion.sources.config import GreenhouseConfig
from app.modules.ingestion.sources.greenhouse import GreenhouseSource, normalise_greenhouse_posting
from tests.http_fakes import FakeTime, Script, polite_client

CONFIG = GreenhouseConfig.model_validate(
    {
        "version": 1,
        "validated_on": "2026-11-27",
        "boards": [
            {"token": "hr", "name": "Acme",
             "location_work_mode": {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}},
            {"token": "mdb", "name": "Mdb",
             "metadata": {
                 "employment_type": {"field": "Employment Type",
                                     "values": {"full-time": "full_time", "intern": "internship"}},
                 "work_mode": {"field": "Working Model Eligibility",
                               "values": {"1-in office": "onsite", "2-flexible": "hybrid"}},
                 "seniority": {"field": "Job Level",
                               "values": {"intern": "intern", "vice president": "senior"}},
             }},
            {"token": "inm", "name": "Inm", "metadata": {"country": {"field": "Base Country"}}},
        ],
    }
)  # fmt: skip
HR, MDB, INM = (CONFIG.board(token) for token in ("hr", "mdb", "inm"))

_CONTENT = (
    "&lt;h2&gt;About Acme&lt;/h2&gt;&lt;p&gt;We use AWS&lt;/p&gt;"
    "&lt;h3&gt;What we look for&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
)


def _raw(i: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 4_000_000_000 + i,
        "title": "Software Engineer",
        "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{i}",
        "first_published": "2026-10-01T09:30:00-04:00",
        "application_deadline": None,
        "location": {"name": "Hybrid in Bangalore, India"},
        "metadata": [],
        "content": _CONTENT,
    }
    row.update(overrides)
    return row


def _meta(**values: object) -> list[dict[str, object]]:
    return [{"name": name.replace("_", " "), "value": value} for name, value in values.items()]


def test_location_prefix_drives_work_mode_and_sections_drive_skills() -> None:
    posting = normalise_greenhouse_posting(_raw(1), board=HR)
    assert posting is not None
    assert posting.external_id == "4000000001"
    assert posting.work_mode is WorkMode.HYBRID
    assert posting.employment_type is EmploymentType.UNKNOWN
    assert posting.locations == (
        PostingLocation("Hybrid in Bangalore, India", "Bengaluru", "IN", WorkMode.HYBRID),
    )
    assert posting.posted_at == datetime(2026, 10, 1, 13, 30, tzinfo=UTC)
    assert "Python" in posting.description
    assert "<" not in posting.description
    kinds = {s.kind for s in posting.skill_sections if "AWS" in s.text}
    assert kinds == {SectionKind.BOILERPLATE}
    assert any(
        s.kind is SectionKind.REQUIRED and "Python" in s.text for s in posting.skill_sections
    )


def test_mixed_segments_give_unknown_job_mode_and_per_place_modes() -> None:
    posting = normalise_greenhouse_posting(
        _raw(1, location={"name": "Remote, Poland; Bangalore, India"}), board=HR
    )
    assert posting is not None
    assert posting.work_mode is WorkMode.UNKNOWN
    assert posting.locations == (
        PostingLocation("Remote, Poland", None, "PL", WorkMode.REMOTE),
        PostingLocation("Bangalore, India", "Bengaluru", "IN", WorkMode.UNKNOWN),
    )


def test_structured_metadata_fills_the_fields() -> None:
    meta = [
        {"name": "Employment Type", "value": "Intern"},
        {"name": "Working Model Eligibility", "value": "2-Flexible"},
        {"name": "Job Level", "value": "Intern"},
    ]
    posting = normalise_greenhouse_posting(
        _raw(1, title="Software Engineer Intern", location={"name": "Gurugram"}, metadata=meta),
        board=MDB,
    )
    assert posting is not None
    assert (posting.employment_type, posting.work_mode, posting.seniority) == (
        EmploymentType.INTERNSHIP, WorkMode.HYBRID, Seniority.INTERN,
    )  # fmt: skip
    assert posting.locations == (PostingLocation("Gurugram", "Gurugram", "IN", WorkMode.HYBRID),)
    assert posting.unmapped == ()


def test_multi_valued_and_unmapped_metadata() -> None:
    meta = [
        {"name": "Employment Type", "value": "Sometimes"},
        {"name": "Working Model Eligibility", "value": ["1-In Office", "2-Flexible"]},
    ]
    posting = normalise_greenhouse_posting(
        _raw(1, location={"name": "Gurugram"}, metadata=meta), board=MDB
    )
    assert posting is not None
    assert posting.work_mode is WorkMode.UNKNOWN
    assert posting.employment_type is EmploymentType.UNKNOWN
    assert posting.unmapped == (("Employment Type", "sometimes"),)


def test_structured_and_title_seniority_that_disagree_give_unknown() -> None:
    meta = [{"name": "Job Level", "value": "Vice President"}]
    posting = normalise_greenhouse_posting(
        _raw(1, title="Junior Analyst", metadata=meta), board=MDB
    )
    assert posting is not None
    assert posting.seniority is Seniority.UNKNOWN


@pytest.mark.parametrize(
    ("location", "country_value", "expected"),
    [
        ("Bangalore", "India", ("Bengaluru", "IN")),
        ("Head Office", "India", (None, "IN")),
        ("Head Office", ["Germany", "Spain"], (None, None)),
    ],
)
def test_metadata_country_is_a_default_only(
    location: str, country_value: object, expected: tuple[str | None, str | None]
) -> None:
    meta = [{"name": "Base Country", "value": country_value}]
    posting = normalise_greenhouse_posting(
        _raw(1, location={"name": location}, metadata=meta), board=INM
    )
    assert posting is not None
    (place,) = posting.locations
    assert (place.city, place.country) == expected


def test_bare_date_deadline_includes_its_day() -> None:
    posting = normalise_greenhouse_posting(_raw(1, application_deadline="2026-12-31"), board=HR)
    assert posting is not None
    assert posting.expires_at == datetime(2027, 1, 1, tzinfo=UTC)


def test_deadline_before_publication_is_dropped_and_reported() -> None:
    posting = normalise_greenhouse_posting(_raw(1, application_deadline="2026-01-01"), board=HR)
    assert posting is not None
    assert posting.expires_at is None
    assert posting.unmapped == (("application_deadline", "2026-01-01"),)


@pytest.mark.parametrize(
    "overrides",
    [{"id": None}, {"title": " "}, {"absolute_url": ""}, {"content": "&lt;p&gt; &lt;/p&gt;"}],
    ids=["no-id", "no-title", "no-link-back", "no-text"],
)
def test_postings_that_cannot_be_shown_are_rejected(overrides: dict[str, Any]) -> None:
    assert normalise_greenhouse_posting(_raw(1, **overrides), board=HR) is None


async def test_matching_total_is_a_complete_fetch() -> None:
    body = {"jobs": [_raw(1), _raw(2)], "meta": {"total": 2}}
    script, fake = Script(httpx.Response(200, json=body)), FakeTime()
    async with polite_client(script, fake) as http:
        result = await GreenhouseSource(http, CONFIG).fetch_all("hr")
    assert result.complete
    assert len(result.postings) == 2
    assert result.seen_external_ids == frozenset({"4000000001", "4000000002"})
    request = script.requests[0]
    assert request.url.path == "/v1/boards/hr/jobs"
    assert request.url.params["content"] == "true"


@pytest.mark.parametrize("meta", [{"total": 3}, {}], ids=["mismatch", "missing"])
async def test_unprovable_total_is_incomplete(meta: dict[str, int]) -> None:
    body = {"jobs": [_raw(1), _raw(2)], "meta": meta}
    script, fake = Script(httpx.Response(200, json=body)), FakeTime()
    async with polite_client(script, fake) as http:
        result = await GreenhouseSource(http, CONFIG).fetch_all("hr")
    assert not result.complete
    assert result.error is not None
    assert "meta.total" in result.error


async def test_board_404_is_incomplete() -> None:
    body = {"status": 404, "error": "Job not found"}
    script, fake = Script(httpx.Response(404, json=body)), FakeTime()
    async with polite_client(script, fake) as http:
        result = await GreenhouseSource(http, CONFIG).fetch_all("hr")
    assert not result.complete
    assert result.error == "HTTP 404"


async def test_malformed_posting_is_failed_but_still_seen() -> None:
    body = {"jobs": [_raw(1), _raw(2, title="")], "meta": {"total": 2}}
    script, fake = Script(httpx.Response(200, json=body)), FakeTime()
    async with polite_client(script, fake) as http:
        result = await GreenhouseSource(http, CONFIG).fetch_all("hr")
    assert result.complete
    assert result.failed == 1
    assert "4000000002" in result.seen_external_ids


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(200, json={"id": 4_000_000_001}), PostingStatus.OPEN),
        (
            httpx.Response(404, json={"status": 404, "error": "Job not found"}),
            PostingStatus.NOT_FOUND,
        ),
        (httpx.Response(404, text="<html>CDN error</html>"), PostingStatus.ERROR),
        (httpx.Response(503), PostingStatus.ERROR),
        (httpx.Response(200, json={"id": 1}), PostingStatus.ERROR),
    ],
    ids=["open", "404-json", "404-html", "503", "wrong-id"],
)
async def test_check_posting(response: httpx.Response, expected: PostingStatus) -> None:
    script, fake = Script(response), FakeTime()
    async with polite_client(script, fake) as http:
        check = await GreenhouseSource(http, CONFIG).check_posting("hr", "4000000001")
    assert check.status is expected
    assert len(script.requests) == 1


async def test_non_numeric_id_is_an_error_without_a_request() -> None:
    script, fake = Script(), FakeTime()
    async with polite_client(script, fake) as http:
        check = await GreenhouseSource(http, CONFIG).check_posting("hr", "../admin")
    assert check.status is PostingStatus.ERROR
    assert script.requests == []
