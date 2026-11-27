from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.domain.enums import EmploymentType, Seniority, WorkMode
from app.modules.ingestion import mapping
from app.modules.ingestion.mapping import FieldOutcome

E, S, W = EmploymentType, Seniority, WorkMode

# Real location-token tables, as in greenhouse_boards.yaml.
TWILIO: Mapping[str, WorkMode] = {"remote": W.REMOTE}
HACKERRANK: Mapping[str, WorkMode] = {
    "remote": W.REMOTE, "hybrid": W.HYBRID, "onsite": W.ONSITE, "in-office": W.ONSITE,
}  # fmt: skip
OBSERVEAI: Mapping[str, WorkMode] = {"remote": W.REMOTE, "hybrid": W.HYBRID}
MONGODB_WORK_MODE: Mapping[str, WorkMode] = {
    "1-in office": W.ONSITE, "2-flexible": W.HYBRID,
    "3-fully remote": W.REMOTE, "3cf-customer facing remote": W.REMOTE,
}  # fmt: skip


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Full-Time\tEmployee ", "full-time employee"), ("", None), ("   ", None), (None, None)],
)
def test_normalise(raw: object, expected: str | None) -> None:
    assert mapping.normalise(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("onsite", FieldOutcome(W.ONSITE)),
        ("Hybrid", FieldOutcome(W.HYBRID)),
        ("REMOTE", FieldOutcome(W.REMOTE)),
        ("unspecified", FieldOutcome(W.UNKNOWN, unmapped="unspecified")),
        (None, FieldOutcome(W.UNKNOWN)),
    ],
)
def test_lever_work_mode(raw: object, expected: FieldOutcome[WorkMode]) -> None:
    assert mapping.lever_work_mode(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Junior", FieldOutcome(S.JUNIOR)),
        ("Senior", FieldOutcome(S.SENIOR)),
        ("All Levels", FieldOutcome(S.UNKNOWN)),
        ("Mid-Senior", FieldOutcome(S.UNKNOWN, unmapped="mid-senior")),
        (None, FieldOutcome(S.UNKNOWN)),
    ],
)
def test_lever_level(raw: object, expected: FieldOutcome[Seniority]) -> None:
    assert mapping.lever_level(raw) == expected


# Every spelling observed on 2026-11-27, plus one new value that must be counted.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Full-time", FieldOutcome(E.FULL_TIME)),
        ("Full-Time", FieldOutcome(E.FULL_TIME)),
        ("full time", FieldOutcome(E.FULL_TIME)),
        ("Full-time Employment", FieldOutcome(E.FULL_TIME)),
        ("Full Time Employee", FieldOutcome(E.FULL_TIME)),
        ("Full-time Onsite or Remote", FieldOutcome(E.FULL_TIME)),
        ("Full-time: Remote", FieldOutcome(E.FULL_TIME)),
        ("Internship", FieldOutcome(E.INTERNSHIP)),
        ("Internship/Co-op", FieldOutcome(E.INTERNSHIP)),
        ("Intern", FieldOutcome(E.INTERNSHIP)),
        ("Contract", FieldOutcome(E.CONTRACT)),
        ("Contractual", FieldOutcome(E.CONTRACT)),
        ("Consultant", FieldOutcome(E.CONTRACT)),
        ("Fixed-Term", FieldOutcome(E.CONTRACT)),
        ("Permanent", FieldOutcome(E.UNKNOWN)),
        ("On-roll", FieldOutcome(E.UNKNOWN)),
        ("Short Term", FieldOutcome(E.UNKNOWN)),
        ("Scholarship", FieldOutcome(E.UNKNOWN)),
        (None, FieldOutcome(E.UNKNOWN)),
        ("Freelance", FieldOutcome(E.UNKNOWN, unmapped="freelance")),
    ],
)
def test_lever_employment_type(raw: object, expected: FieldOutcome[EmploymentType]) -> None:
    assert mapping.lever_employment_type(raw) == expected


def test_lever_table_keys_are_normalised_and_disjoint_from_unstated() -> None:
    tables: list[tuple[Mapping[str, object], frozenset[str]]] = [
        (mapping.LEVER_WORKPLACE_TYPE, frozenset()),
        (mapping.LEVER_COMMITMENT, mapping.LEVER_COMMITMENT_UNSTATED),
        (mapping.LEVER_LEVEL, mapping.LEVER_LEVEL_UNSTATED),
    ]
    for table, unstated in tables:
        for key in (*table, *unstated):
            assert key == mapping.normalise(key)
        assert not set(table) & unstated


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Software Engineer Intern", S.INTERN),
        ("Summer Internship 2027 - Data", S.INTERN),
        ("Internal Tools Engineer", S.UNKNOWN),
        ("International Tax Manager", S.UNKNOWN),
        ("Sr. Software Engineer", S.SENIOR),
        ("Senior Product Designer", S.SENIOR),
        ("Staff Software Engineer", S.SENIOR),
        ("Staff Accountant", S.UNKNOWN),
        ("Head of Data", S.SENIOR),
        ("Jr. Analyst", S.JUNIOR),
        ("New Grad Software Engineer", S.JUNIOR),
        ("Entry-Level Support Engineer", S.JUNIOR),
        ("Software Engineer II", S.UNKNOWN),
        ("Engineering Manager", S.UNKNOWN),
        ("Associate Product Manager", S.UNKNOWN),
        ("Senior Manager, Intern Programs", S.UNKNOWN),
        ("Tech Lead", S.UNKNOWN),
        ("Director of Engineering", S.SENIOR),
        ("VP, Sales", S.SENIOR),
        ("Principal Data Scientist", S.SENIOR),
    ],
)
def test_title_seniority(title: str, expected: Seniority) -> None:
    assert mapping.title_seniority(title) is expected


@pytest.mark.parametrize(
    ("structured", "title", "expected"),
    [
        (S.UNKNOWN, "Senior Engineer", S.SENIOR),
        (S.JUNIOR, "Software Engineer", S.JUNIOR),
        (S.SENIOR, "Senior Engineer", S.SENIOR),
        (S.JUNIOR, "Senior Engineer", S.UNKNOWN),
        (S.UNKNOWN, "Software Engineer", S.UNKNOWN),
    ],
)
def test_combine_seniority(structured: Seniority, title: str, expected: Seniority) -> None:
    assert mapping.combine_seniority(structured, title) is expected


@pytest.mark.parametrize(
    ("location", "tokens", "expected"),
    [
        ("Hybrid in Bangalore, India", HACKERRANK, W.HYBRID),
        ("Onsite in Bangalore", HACKERRANK, W.ONSITE),
        ("In-office in Bangalore, India", HACKERRANK, W.ONSITE),
        ("Remote Globally", HACKERRANK, W.REMOTE),
        ("Remote - India", TWILIO, W.REMOTE),
        ("Remote- Ontario, Canada (DNU)", TWILIO, W.REMOTE),
        ("Redwood City, CA (Hybrid)", OBSERVEAI, W.HYBRID),
        ("Remote-US", OBSERVEAI, W.REMOTE),
        ("Texas Remote", TWILIO, W.REMOTE),
        ("USA , Remote", TWILIO, W.REMOTE),
        ("Seattle, WA (Remote)", TWILIO, W.REMOTE),
        ("Remote, Canada; Remote, United States", TWILIO, W.REMOTE),
        ("Bangalore, India; Remote, Poland", TWILIO, W.UNKNOWN),
        ("Remote - California; San Francisco, California", TWILIO, W.UNKNOWN),
        ("Bengaluru, India", TWILIO, W.UNKNOWN),
        ("Hybrid in Bangalore, India", TWILIO, W.UNKNOWN),
        ("Remotely located team", TWILIO, W.UNKNOWN),
        (None, TWILIO, W.UNKNOWN),
        ("Remote", {}, W.UNKNOWN),
        (" ; ", TWILIO, W.UNKNOWN),
    ],
)
def test_location_work_mode(
    location: str | None, tokens: Mapping[str, WorkMode], expected: WorkMode
) -> None:
    assert mapping.location_work_mode(location, tokens) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2-Flexible", FieldOutcome(W.HYBRID)),
        (["3-Fully Remote"], FieldOutcome(W.REMOTE)),
        (["1-In Office", "2-Flexible"], FieldOutcome(W.UNKNOWN)),
        ("Mostly hybrid", FieldOutcome(W.UNKNOWN, unmapped="mostly hybrid")),
        (None, FieldOutcome(W.UNKNOWN)),
        ([], FieldOutcome(W.UNKNOWN)),
    ],
)
def test_metadata_lookup(value: object, expected: FieldOutcome[WorkMode]) -> None:
    assert mapping.metadata_lookup(value, MONGODB_WORK_MODE, W.UNKNOWN) == expected


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ((W.REMOTE, W.UNKNOWN), W.REMOTE),
        ((W.REMOTE, W.REMOTE), W.REMOTE),
        ((W.REMOTE, W.HYBRID), W.UNKNOWN),
        ((W.UNKNOWN, W.UNKNOWN), W.UNKNOWN),
    ],
)
def test_combine_work_mode(modes: tuple[WorkMode, ...], expected: WorkMode) -> None:
    assert mapping.combine_work_mode(*modes) is expected
