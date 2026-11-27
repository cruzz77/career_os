"""Canonical cities and countries, shared by ingestion (job locations) and preference memory
(user locations), so "Bangalore" in a preference and "Bengaluru" on a job compare equal.

Only stated values are read: a city or country is recognised when the string names it, through
an explicit table. Ambiguous names (Vancouver, Portland, Cambridge, Georgia) are deliberately
absent, a city whose usual country contradicts a stated country is dropped, and two-letter ISO
codes are not read as countries ("CA" in "Palo Alto, CA" is California, not Canada). Anything
unrecognised stays None: a location hard filter keeps and labels it rather than guessing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final


def _norm(raw: object) -> str | None:
    if raw is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", str(raw)).casefold().split())
    return text or None


@dataclass(frozen=True, slots=True)
class City:
    name: str
    country: str  # ISO 3166-1 alpha-2


_CITY_ROWS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    # India
    ("Bengaluru", "IN", ("bengaluru", "bangalore")),
    ("Mumbai", "IN", ("mumbai", "bombay")),
    ("Delhi", "IN", ("delhi", "new delhi")),
    ("Gurugram", "IN", ("gurugram", "gurgaon")),
    ("Noida", "IN", ("noida",)),
    ("Pune", "IN", ("pune",)),
    ("Hyderabad", "IN", ("hyderabad",)),
    ("Chennai", "IN", ("chennai", "madras")),
    ("Kolkata", "IN", ("kolkata", "calcutta")),
    ("Ahmedabad", "IN", ("ahmedabad",)),
    ("Surat", "IN", ("surat",)),
    ("Lucknow", "IN", ("lucknow",)),
    ("Jaipur", "IN", ("jaipur",)),
    ("Kochi", "IN", ("kochi", "cochin")),
    ("Chandigarh", "IN", ("chandigarh",)),
    ("Indore", "IN", ("indore",)),
    # United States
    ("New York", "US", ("new york", "new york city", "nyc")),
    ("San Francisco", "US", ("san francisco",)),
    ("Seattle", "US", ("seattle",)),
    ("Palo Alto", "US", ("palo alto",)),
    ("Mountain View", "US", ("mountain view",)),
    ("Menlo Park", "US", ("menlo park",)),
    ("Foster City", "US", ("foster city",)),
    ("Hayward", "US", ("hayward",)),
    ("Fremont", "US", ("fremont",)),
    ("San Diego", "US", ("san diego",)),
    ("San Jose", "US", ("san jose",)),
    ("Santa Clara", "US", ("santa clara",)),
    ("San Mateo", "US", ("san mateo",)),
    ("Redwood City", "US", ("redwood city",)),
    ("Sunnyvale", "US", ("sunnyvale",)),
    ("Los Angeles", "US", ("los angeles",)),
    ("Boston", "US", ("boston",)),
    ("Washington", "US", ("washington dc",)),
    ("Denver", "US", ("denver",)),
    ("Chicago", "US", ("chicago",)),
    ("Austin", "US", ("austin",)),
    ("Dallas", "US", ("dallas",)),
    ("Atlanta", "US", ("atlanta",)),
    ("Bellevue", "US", ("bellevue",)),
    ("Philadelphia", "US", ("philadelphia",)),
    ("Phoenix", "US", ("phoenix",)),
    ("Salt Lake City", "US", ("salt lake city",)),
    ("Miami", "US", ("miami",)),
    ("Nashville", "US", ("nashville",)),
    # Rest of the world
    ("Toronto", "CA", ("toronto",)),
    ("Montreal", "CA", ("montreal", "montr\u00e9al")),
    ("Ottawa", "CA", ("ottawa",)),
    ("London", "GB", ("london",)),
    ("Edinburgh", "GB", ("edinburgh",)),
    ("Dublin", "IE", ("dublin",)),
    ("Cork", "IE", ("cork",)),
    ("Paris", "FR", ("paris",)),
    ("Berlin", "DE", ("berlin",)),
    ("Munich", "DE", ("munich", "m\u00fcnchen")),
    ("Amsterdam", "NL", ("amsterdam",)),
    ("Stockholm", "SE", ("stockholm",)),
    ("Warsaw", "PL", ("warsaw", "warszawa")),
    ("Madrid", "ES", ("madrid",)),
    ("Barcelona", "ES", ("barcelona",)),
    ("Lisbon", "PT", ("lisbon",)),
    ("Zurich", "CH", ("zurich", "z\u00fcrich")),
    ("Vienna", "AT", ("vienna",)),
    ("Copenhagen", "DK", ("copenhagen",)),
    ("Tel Aviv", "IL", ("tel aviv",)),
    ("Singapore", "SG", ("singapore",)),
    ("Tokyo", "JP", ("tokyo",)),
    ("Seoul", "KR", ("seoul",)),
    ("Shanghai", "CN", ("shanghai",)),
    ("Beijing", "CN", ("beijing",)),
    ("Hong Kong", "HK", ("hong kong",)),
    ("Sydney", "AU", ("sydney",)),
    ("Melbourne", "AU", ("melbourne",)),
    ("Dubai", "AE", ("dubai",)),
    ("Abu Dhabi", "AE", ("abu dhabi",)),
    ("Luxembourg", "LU", ("luxembourg",)),
    ("Mexico City", "MX", ("mexico city",)),
    ("Sao Paulo", "BR", ("sao paulo", "s\u00e3o paulo")),
    ("Bogota", "CO", ("bogota", "bogot\u00e1")),
    ("Johannesburg", "ZA", ("johannesburg",)),
    ("Ho Chi Minh City", "VN", ("ho chi minh city",)),
    ("Jakarta", "ID", ("jakarta",)),
    ("Islamabad", "PK", ("islamabad",)),
)

_COUNTRY_ROWS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("IN", ("india",)),
    ("US", ("united states", "united states of america", "usa", "us", "u.s", "u.s.a")),
    ("GB", ("united kingdom", "uk", "england", "scotland", "great britain", "britain")),
    ("CA", ("canada",)),
    ("IE", ("ireland",)),
    ("DE", ("germany",)),
    ("FR", ("france",)),
    ("ES", ("spain",)),
    ("NL", ("netherlands", "the netherlands")),
    ("PL", ("poland",)),
    ("SE", ("sweden",)),
    ("JP", ("japan",)),
    ("SG", ("singapore",)),
    ("AU", ("australia",)),
    ("IL", ("israel",)),
    ("AE", ("united arab emirates", "uae")),
    ("BR", ("brazil",)),
    ("MX", ("mexico",)),
    ("CO", ("colombia",)),
    ("KR", ("south korea", "korea")),
    ("CN", ("china",)),
    ("HK", ("hong kong",)),
    ("LU", ("luxembourg",)),
    ("ZA", ("south africa",)),
    ("VN", ("vietnam",)),
    ("ID", ("indonesia",)),
    ("PK", ("pakistan",)),
    ("CH", ("switzerland",)),
    ("AT", ("austria",)),
    ("DK", ("denmark",)),
    ("PT", ("portugal",)),
    ("IT", ("italy",)),
    ("BE", ("belgium",)),
    ("FI", ("finland",)),
    ("NO", ("norway",)),
    ("HR", ("croatia",)),
    ("RS", ("serbia",)),
    ("SA", ("saudi arabia", "ksa")),
    ("TR", ("turkey",)),
    ("EG", ("egypt",)),
    ("EE", ("estonia",)),
    ("AR", ("argentina",)),
    ("CL", ("chile",)),
    ("NZ", ("new zealand",)),
    ("PH", ("philippines",)),
    ("MY", ("malaysia",)),
    ("TH", ("thailand",)),
    ("TW", ("taiwan",)),
)

# Regions name a country without naming a city. "in" (Indiana) is excluded: it is also a
# preposition; "georgia" is excluded: it is also a country.
_US_STATE_CODES: Final = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "ia", "ks", "ky",
    "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi",
    "wy", "dc",
)  # fmt: skip
_US_STATE_NAMES: Final = (
    "california", "texas", "new jersey", "massachusetts", "colorado", "illinois", "ohio",
    "oregon", "pennsylvania", "virginia", "north carolina", "south carolina", "michigan",
    "missouri", "arizona", "florida", "tennessee", "washington", "nevada", "utah", "connecticut",
    "maryland", "minnesota", "kansas", "alabama", "indiana", "delaware", "new hampshire",
    "rhode island", "wisconsin", "iowa", "kentucky", "oklahoma",
)  # fmt: skip
_CA_REGIONS: Final = (
    "on", "bc", "ab", "qc", "ns", "ontario", "british columbia", "alberta", "quebec", "nova scotia",
)  # fmt: skip
_IN_REGIONS: Final = (
    "karnataka", "maharashtra", "uttar pradesh", "haryana", "telangana", "tamil nadu", "gujarat",
    "west bengal", "rajasthan", "kerala", "punjab", "madhya pradesh",
)  # fmt: skip

CITIES: Final[Mapping[str, City]] = {
    alias: City(name, country) for name, country, aliases in _CITY_ROWS for alias in aliases
}
COUNTRIES: Final[Mapping[str, str]] = {
    alias: code for code, aliases in _COUNTRY_ROWS for alias in aliases
}
REGIONS: Final[Mapping[str, str]] = {
    **dict.fromkeys(_US_STATE_CODES, "US"),
    **dict.fromkeys(_US_STATE_NAMES, "US"),
    **dict.fromkeys(_CA_REGIONS, "CA"),
    **dict.fromkeys(_IN_REGIONS, "IN"),
}


def canonical_city(raw: object) -> str | None:
    key = _norm(raw)
    city = CITIES.get(key) if key else None
    return city.name if city else None


def country_code(raw: object) -> str | None:
    key = _norm(raw)
    return COUNTRIES.get(key) if key else None


@dataclass(frozen=True, slots=True)
class ParsedLocation:
    city: str | None
    country: str | None


_SPLIT: Final = re.compile(r";|\||\u2022|/|\s+or\s+")
_PAREN: Final = re.compile(r"\([^)]*\)")
_MODE_PREFIX: Final = re.compile(r"^(?:remote|hybrid|on-?site|in-office)\b(?:\s+in\b)?[\s,:\-]*")
_MODE_SUFFIX: Final = re.compile(r"[\s,\-]+(?:remote|hybrid|on-?site)$")
_UNKNOWN: Final = ParsedLocation(None, None)


def split_locations(raw: str | None) -> list[str]:
    """One string per stated place: ";", "|", bullets, "/" and " or " separate places."""
    return [part.strip() for part in _SPLIT.split(raw or "") if part.strip()]


def _clean(segment: str) -> str:
    text = _norm(segment) or ""
    # "Washington, D.C." would otherwise split on its comma into the state and "d.c.".
    text = text.replace("washington, d.c.", "washington dc").replace(
        "washington d.c.", "washington dc"
    )
    text = _PAREN.sub(" ", text)
    text = _MODE_PREFIX.sub("", text.strip())
    text = _MODE_SUFFIX.sub("", text.strip())
    return text.strip(" ,.-")


def _resolve_part(part: str) -> tuple[City | None, str | None]:
    if part in CITIES:
        return CITIES[part], None
    if part in COUNTRIES:
        return None, COUNTRIES[part]
    if part in REGIONS:
        return None, REGIONS[part]
    for separator in (" - ", "-"):
        head = part.split(separator, 1)[0].strip()
        if head != part and head in CITIES:  # "bangalore - dd", "bengaluru-vtp"
            return CITIES[head], None
    head, _, tail = part.rpartition(" ")
    if head and tail in COUNTRIES:  # "tokyo japan"
        return CITIES.get(head), COUNTRIES[tail]
    return None, None


def parse_location(segment: str, *, default_country: str | None = None) -> ParsedLocation:
    """One place. default_country is a country the source states separately (Lever's
    `country`), used only when the string itself names none."""
    parts = [part.strip(" .") for part in _clean(segment).split(",") if part.strip(" .")]
    city: City | None = None
    stated: set[str] = set()
    for part in parts:
        found_city, found_country = _resolve_part(part)
        if found_city is not None and city is None:
            city = found_city
        if found_country is not None:
            stated.add(found_country)
    if len(stated) > 1:
        return _UNKNOWN  # "Remote - UK, Spain, Ireland" names several countries, not one
    explicit = stated.pop() if stated else None
    if city is not None and explicit is not None and city.country != explicit:
        city = None  # "Paris, Texas": the stated country wins and the city is not Paris, FR
    country = explicit or (city.country if city else None)
    if country is None and default_country and len(default_country) == 2:
        country = default_country.upper()
    return ParsedLocation(city.name if city else None, country)
