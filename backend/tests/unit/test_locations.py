from __future__ import annotations

import pytest

from app.domain.locations import (
    ParsedLocation,
    canonical_city,
    country_code,
    parse_location,
    split_locations,
)


# Real location strings from the 2026-11-27 Lever and Greenhouse data.
@pytest.mark.parametrize(
    ("raw", "city", "country"),
    [
        ("Bangalore, Karnataka", "Bengaluru", "IN"),
        ("Noida, Uttar Pradesh", "Noida", "IN"),
        ("Mumbai,Maharashtra", "Mumbai", "IN"),
        ("bengaluru", "Bengaluru", "IN"),
        ("Bangalore - DD", "Bengaluru", "IN"),
        ("Bengaluru-VTP, India", "Bengaluru", "IN"),
        ("Bangalore East, Bengaluru, Karnataka, India", "Bengaluru", "IN"),
        ("Gurgaon", "Gurugram", "IN"),
        ("Hybrid in Bangalore, India", "Bengaluru", "IN"),
        ("In-office in Bangalore, India", "Bengaluru", "IN"),
        ("Remote - India", None, "IN"),
        ("Remote, United States", None, "US"),
        ("USA , Remote", None, "US"),
        ("Remote-US", None, "US"),
        ("Texas Remote", None, "US"),
        ("Remote - Washington", None, "US"),
        ("New York, NY", "New York", "US"),
        ("New York City", "New York", "US"),
        ("Palo Alto, CA", "Palo Alto", "US"),
        ("Washington, D.C.", "Washington", "US"),
        ("Seattle, WA (Remote)", "Seattle", "US"),
        ("Redwood City, CA (Hybrid)", "Redwood City", "US"),
        ("Toronto, ON, Canada", "Toronto", "CA"),
        ("Remote- Ontario, Canada (DNU)", None, "CA"),
        ("London, United Kingdom", "London", "GB"),
        ("London, UK", "London", "GB"),
        ("UAE, Dubai", "Dubai", "AE"),
        ("Tokyo Japan", "Tokyo", "JP"),
        ("Singapore", "Singapore", "SG"),
        ("Paris, Texas", None, "US"),
        ("Remote - UK, Spain, Ireland", None, None),
        ("East Coast", None, None),
        ("Asia", None, None),
        ("Remote Globally", None, None),
    ],
)
def test_parse_location(raw: str, city: str | None, country: str | None) -> None:
    assert parse_location(raw) == ParsedLocation(city, country)


def test_default_country_fills_only_when_none_is_stated() -> None:
    assert parse_location("Head Office", default_country="lu") == ParsedLocation(None, "LU")
    assert parse_location("London, UK", default_country="US") == ParsedLocation("London", "GB")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mumbai / Bangalore", ["Mumbai", "Bangalore"]),
        ("Santa Clara or Mountain View, CA", ["Santa Clara", "Mountain View, CA"]),
        (
            "San Francisco, CA \u2022 New York, NY \u2022 United States",
            ["San Francisco, CA", "New York, NY", "United States"],
        ),
        ("Bengaluru, India; Remote, Poland", ["Bengaluru, India", "Remote, Poland"]),
        ("Portland, OR", ["Portland, OR"]),
        ("", []),
    ],
)
def test_split_locations(raw: str, expected: list[str]) -> None:
    assert split_locations(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("Bangalore", "Bengaluru"), (" GURGAON ", "Gurugram"), ("Atlantis", None)]
)
def test_canonical_city(raw: str, expected: str | None) -> None:
    assert canonical_city(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [("India", "IN"), ("U.S", "US"), ("CA", None)])
def test_country_code(raw: str, expected: str | None) -> None:
    assert country_code(raw) == expected
