from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.enums import EmploymentType, Seniority, WorkMode
from app.modules.ingestion.sources.base import NormalizedPosting, PostingLocation
from app.modules.ingestion.sources.config import (
    GreenhouseConfig,
    LeverConfig,
    load_greenhouse_config,
    load_lever_config,
)

_DAY = "2026-11-27"


def test_shipped_lever_config_loads() -> None:
    config = load_lever_config()
    assert len(config.companies) == 11
    assert config.company("paytm").name == "Paytm"
    with pytest.raises(KeyError):
        config.company("not-configured")


def test_shipped_greenhouse_config_loads() -> None:
    config = load_greenhouse_config()
    assert len(config.boards) == 11
    mongodb = config.board("mongodb")
    assert mongodb.metadata.work_mode is not None
    assert mongodb.metadata.work_mode.values["2-flexible"] is WorkMode.HYBRID
    assert config.board("hackerrank").location_work_mode["in-office"] is WorkMode.ONSITE
    assert config.board("groww").location_work_mode == {}


def test_duplicate_lever_slug_is_rejected() -> None:
    company = {"slug": "acme", "name": "Acme", "instance": "global"}
    with pytest.raises(ValidationError, match="duplicate"):
        LeverConfig.model_validate(
            {"version": 1, "validated_on": _DAY, "companies": [company, company]}
        )


def _board(**fields: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "validated_on": _DAY,
        "boards": [{"token": "acme", "name": "Acme", **fields}],
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"location_work_mode": {"remote": "remote-ish"}},
        {"location_work_mode": {"remote": "unknown"}},
        {"location_work_mode": {"Remote": "remote"}},
        {"metadata": {"employment_type": {"field": "Type", "values": {"ft": "full_time"}}},
         "colour": "blue"},
    ],
    ids=["not-an-enum-value", "unknown-as-value", "key-not-normalised", "unexpected-key"],
)  # fmt: skip
def test_invalid_greenhouse_config_is_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GreenhouseConfig.model_validate(_board(**fields))


def _posting(**overrides: Any) -> NormalizedPosting:
    base = NormalizedPosting(
        source="lever",
        source_board="acme",
        external_id="1",
        title="Backend Engineer",
        description="Build APIs",
        company_name="Acme",
        locations=(
            PostingLocation("Remote - India", None, "IN", WorkMode.REMOTE),
            PostingLocation("Bangalore", "Bengaluru", "IN", WorkMode.REMOTE),
        ),
        work_mode=WorkMode.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        seniority=Seniority.UNKNOWN,
        posted_at=datetime(2026, 11, 1, tzinfo=UTC),
        expires_at=None,
        source_url="https://jobs.example.test/acme/1",
        skill_sections=(),
    )
    return dataclasses.replace(base, **overrides)


def test_content_hash_is_raw_sha256_and_tracks_stored_fields() -> None:
    posting = _posting()
    assert len(posting.content_hash()) == 32
    assert posting.content_hash() == _posting().content_hash()
    assert posting.content_hash() != _posting(description="Build APIs in Go").content_hash()
    # Skill sections are not stored on jobs, so they must not force an update or re-embed.
    assert posting.content_hash() == _posting(skill_sections=None).content_hash()


def test_canonical_hash_ignores_source_and_description() -> None:
    posting = _posting()
    elsewhere = _posting(source="greenhouse", source_board="other", external_id="9",
                         description="Different words")  # fmt: skip
    assert posting.canonical_hash() == elsewhere.canonical_hash()
    assert posting.canonical_hash() != _posting(title="Frontend Engineer").canonical_hash()


def test_primary_location_prefers_a_city() -> None:
    assert _posting().primary_location == PostingLocation(
        "Bangalore", "Bengaluru", "IN", WorkMode.REMOTE
    )
    assert _posting(locations=()).primary_location is None
