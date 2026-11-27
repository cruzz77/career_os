"""Source values to the hard-filter enums (blueprint 6.4.1).

A column is filled only from a value the source states, through an explicit exact-match table.
Everything else is UNKNOWN, which retrieval keeps and labels "not specified". A wrong value
would break the zero-violation guarantee on hard filters, while UNKNOWN only costs a label, so
every ambiguous case resolves to UNKNOWN. Nothing here reads description text.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from app.domain.enums import EmploymentType, Seniority, WorkMode


def normalise(raw: object) -> str | None:
    """Key form used by every mapping table: NFKC, casefolded, whitespace collapsed."""
    if raw is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", str(raw)).casefold().split())
    return text or None


@dataclass(frozen=True, slots=True)
class FieldOutcome[E: (WorkMode, EmploymentType, Seniority)]:
    value: E
    # Set when the source stated a value no table maps; counted in ingestion_runs.notes so a
    # new spelling shows up instead of silently becoming UNKNOWN forever.
    unmapped: str | None = None


def lookup[E: (WorkMode, EmploymentType, Seniority)](
    raw: object,
    table: Mapping[str, E],
    unknown: E,
    *,
    known_unstated: frozenset[str] = frozenset(),
) -> FieldOutcome[E]:
    key = normalise(raw)
    if key is None or key in known_unstated:
        return FieldOutcome(unknown)
    mapped = table.get(key)
    if mapped is None:
        return FieldOutcome(unknown, unmapped=key)
    return FieldOutcome(mapped)


def metadata_lookup[E: (WorkMode, EmploymentType, Seniority)](
    value: object, table: Mapping[str, E], unknown: E
) -> FieldOutcome[E]:
    """A Greenhouse custom-field value, which may be a scalar or a list."""
    if isinstance(value, list):
        stated = [item for item in value if normalise(item) is not None]
        if len(stated) > 1:
            # Several values ("1-In Office", "2-Flexible") do not state one value.
            return FieldOutcome(unknown)
        value = stated[0] if stated else None
    return lookup(value, table, unknown)


# --- Lever: tables shared by every Lever company, built from the values seen on 2026-11-27 ---

LEVER_WORKPLACE_TYPE: Final[Mapping[str, WorkMode]] = {
    "onsite": WorkMode.ONSITE,
    "hybrid": WorkMode.HYBRID,
    "remote": WorkMode.REMOTE,
}

LEVER_COMMITMENT: Final[Mapping[str, EmploymentType]] = {
    "full-time": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "full-time employment": EmploymentType.FULL_TIME,
    "full time employee": EmploymentType.FULL_TIME,
    "full-time onsite or remote": EmploymentType.FULL_TIME,
    "full-time: remote": EmploymentType.FULL_TIME,
    "internship": EmploymentType.INTERNSHIP,
    "internship/co-op": EmploymentType.INTERNSHIP,
    "intern": EmploymentType.INTERNSHIP,
    "contract": EmploymentType.CONTRACT,
    "contractual": EmploymentType.CONTRACT,
    "consultant": EmploymentType.CONTRACT,
    "fixed-term": EmploymentType.CONTRACT,
}
# Known values that do not state the hours: "permanent" and "on-roll" describe the employment
# relationship and can be part-time. Mapping them to full_time would assert something unsaid.
LEVER_COMMITMENT_UNSTATED: Final = frozenset({"permanent", "on-roll", "short term", "scholarship"})

LEVER_LEVEL: Final[Mapping[str, Seniority]] = {
    "junior": Seniority.JUNIOR,
    "senior": Seniority.SENIOR,
}
LEVER_LEVEL_UNSTATED: Final = frozenset({"all levels"})


def lever_work_mode(raw: object) -> FieldOutcome[WorkMode]:
    return lookup(raw, LEVER_WORKPLACE_TYPE, WorkMode.UNKNOWN)


def lever_employment_type(raw: object) -> FieldOutcome[EmploymentType]:
    return lookup(
        raw, LEVER_COMMITMENT, EmploymentType.UNKNOWN, known_unstated=LEVER_COMMITMENT_UNSTATED
    )


def lever_level(raw: object) -> FieldOutcome[Seniority]:
    return lookup(raw, LEVER_LEVEL, Seniority.UNKNOWN, known_unstated=LEVER_LEVEL_UNSTATED)


# --- Seniority from the title: only words that name a level on their own ---


def _words(*phrases: str) -> re.Pattern[str]:
    # Lookarounds instead of \b, so "sr." and "jr." match and "internal" does not match "intern".
    alternation = "|".join(re.escape(phrase) for phrase in phrases)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")


_STAFF_ROLES: Final = (
    "software", "engineer", "machine learning", "ml", "data", "security", "site reliability",
    "research", "product", "design", "designer", "scientist", "applied", "backend", "frontend",
    "full stack", "infrastructure", "platform",
)  # fmt: skip

# Deliberately absent: manager, lead, associate, I/II/III, L-levels. Their level depends on the
# company, so they say nothing comparable. "staff" counts only before a technical role: a
# "Staff Accountant" is not a staff-level engineer.
_TITLE_LEVELS: Final[tuple[tuple[re.Pattern[str], Seniority], ...]] = (
    (_words("intern", "interns", "internship", "trainee", "apprentice", "apprenticeship"),
     Seniority.INTERN),
    (_words("junior", "jr", "new grad", "new graduate", "entry level", "entry-level"),
     Seniority.JUNIOR),
    (_words("senior", "sr", "principal", "director", "vp", "vice president", "chief", "head of",
            *(f"staff {role}" for role in _STAFF_ROLES)),
     Seniority.SENIOR),
)  # fmt: skip


def title_seniority(title: str) -> Seniority:
    text = normalise(title) or ""
    levels = {level for pattern, level in _TITLE_LEVELS if pattern.search(text)}
    # "Senior Manager, Intern Programs" names two levels, so it names none.
    return levels.pop() if len(levels) == 1 else Seniority.UNKNOWN


def combine_seniority(structured: Seniority, title: str) -> Seniority:
    from_title = title_seniority(title)
    if structured is Seniority.UNKNOWN:
        return from_title
    if from_title is Seniority.UNKNOWN or from_title is structured:
        return structured
    return Seniority.UNKNOWN


# --- Work mode stated in a Greenhouse location string ---


@lru_cache(maxsize=64)
def _token_patterns(token: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    t = re.escape(token)
    # Start: "remote - india", "remote, canada", "hybrid in bangalore", "remote-us", "remote".
    # End: "redwood city, ca (hybrid)", "texas remote", "usa , remote".
    return re.compile(rf"^{t}(?![a-z0-9])"), re.compile(rf"(?<![a-z0-9])\(?{t}\)?$")


def segment_work_mode(segment: str, tokens: Mapping[str, WorkMode]) -> WorkMode | None:
    text = normalise(segment)
    if text is None:
        return None
    found = {
        mode
        for token, mode in tokens.items()
        for pattern in _token_patterns(token)
        if pattern.search(text)
    }
    return found.pop() if len(found) == 1 else None


def location_work_mode(location: str | None, tokens: Mapping[str, WorkMode]) -> WorkMode:
    """Every ";"-separated segment must state the same mode, or the job's mode is UNKNOWN."""
    if location is None or not tokens:
        return WorkMode.UNKNOWN
    segments = [segment for segment in location.split(";") if segment.strip()]
    modes = {segment_work_mode(segment, tokens) for segment in segments}
    if len(modes) != 1:
        return WorkMode.UNKNOWN
    (mode,) = modes
    return mode if mode is not None else WorkMode.UNKNOWN


def combine_work_mode(*modes: WorkMode) -> WorkMode:
    stated = {mode for mode in modes if mode is not WorkMode.UNKNOWN}
    return stated.pop() if len(stated) == 1 else WorkMode.UNKNOWN
