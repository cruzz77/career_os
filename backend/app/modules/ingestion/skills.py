"""Skill catalogue and section-aware skill extraction (blueprint 5: skills, skill_aliases).

Skills are scoring and explanation features, not hard filters, so matching them in description
text is allowed (blueprint 6.4.1 forbids text inference only for hard-filter columns).
Company boilerplate is skipped: "About Databricks" names AWS, Azure and GCP on every posting.
A skill is `required` only when it appears under a heading that states requirements; anywhere
else it is `preferred`, so `missing_required` never claims more than the posting says.
"""

from __future__ import annotations

import html
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import SkillRequirement
from app.modules.ingestion.mapping import normalise

CATALOGUE_PATH: Final = Path(__file__).with_name("skills_catalogue.yaml")


class CatalogueSkill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    category: str
    aliases: tuple[str, ...] = Field(min_length=1)


class SkillCatalogue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    categories: tuple[str, ...]
    skills: tuple[CatalogueSkill, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        names: set[str] = set()
        owners: dict[str, str] = {}
        for skill in self.skills:
            if skill.category not in self.categories:
                raise ValueError(f"{skill.name}: unknown category {skill.category!r}")
            # skills has UNIQUE(lower(canonical_name)); catch it here, not at seed time.
            if skill.name.casefold() in names:
                raise ValueError(f"duplicate skill name {skill.name!r}")
            names.add(skill.name.casefold())
            for alias in skill.aliases:
                if alias != normalise(alias):
                    raise ValueError(f"{skill.name}: alias {alias!r} is not normalised")
                if alias in owners:
                    raise ValueError(f"alias {alias!r} on both {owners[alias]} and {skill.name}")
                owners[alias] = skill.name
        return self

    @classmethod
    def load(cls, path: Path = CATALOGUE_PATH) -> SkillCatalogue:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- HTML and headings ---

_TAG: Final = re.compile(r"<[^>]+>")
_BLOCK_END: Final = re.compile(r"<\s*(?:br|/p|/li|/h[1-6]|/div|/ul|/ol|/tr)\b[^>]*>", re.I)
_HEADING_TAG: Final = re.compile(r"<(h[1-6]|strong|b)(?:\s[^>]*)?>(.*?)</\1\s*>", re.S | re.I)


def html_to_text(markup: object) -> str:
    """Greenhouse content is entity-escaped HTML: unescape, keep line breaks, drop tags."""
    once = html.unescape(str(markup or ""))
    text = html.unescape(_TAG.sub(" ", _BLOCK_END.sub("\n", once)))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


class SectionKind(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    NEUTRAL = "neutral"
    BOILERPLATE = "boilerplate"


@dataclass(frozen=True, slots=True)
class Section:
    kind: SectionKind
    text: str


def normalise_heading(text: object) -> str | None:
    heading = normalise(text)
    if heading is None:
        return None
    heading = heading.replace("\u2019", "'").replace("\u2018", "'").rstrip(" :.?!").strip()
    return heading or None


_R, _P, _N, _B = (SectionKind.REQUIRED, SectionKind.PREFERRED, SectionKind.NEUTRAL,
                  SectionKind.BOILERPLATE)  # fmt: skip

# Built from the headings seen on 2026-11-27 (Lever lists and descriptionPlain lines, Greenhouse
# h*/strong and standalone lines). Keys are in normalise_heading form. Neutral and boilerplate
# headings are listed too: in plain text they are what ends a requirements section.
HEADING_KINDS: Final[Mapping[str, SectionKind]] = {
    **dict.fromkeys(
        [
            "what we require",
            "qualifications",
            "qualification",
            "requirements",
            "required qualifications",
            "required qualification",
            "minimum qualifications",
            "basic qualifications",
            "minimum requirements",
            "additional requirements",
            "candidate requirements",
            "what you will need",
            "what you'll need",
            "what we look for",
            "what you'll bring",
            "what you will bring",
            "what you bring",
            "what you'll bring to the role",
            "what you will bring to the table",
            "who you are",
            "about you",
            "internship requirements",
            "program requirements",
            "experience and qualifications",
            "experience and qualification",
            "experience & qualifications",
            "qualifications & experience",
            "skills & qualifications",
            "skills",
            "key skills",
            "key skill",
            "technical skills",
            "skills and competencies",
            "competencies",
            "technical qualifications",
            "what we're looking for",
            "what we are looking for",
            "must have",
            "must haves",
            "expectations/ requirements",
            "role requirement & expectation",
            "required knowledge, skills, and abilities",
            "experience you'll need",
            "the experience we need",
            "ideal background",
            "directly relevant background",
            "as an ideal candidate, you will have",
            "candidate profile",
            "what makes you a great fit",
            "you should apply if",
            "you should apply if you",
            "you should apply if you have",
            "you should apply if you have/are",
            "we'd love to hear from you, if you",
            "we'd love to hear from you, if you have",
            "experience",
            "education",
            "educational qualification",
            "education and experience",
            "education & experience",
            "education and training",
            "soft skills",
        ],
        _R,
    ),
    **dict.fromkeys(
        [
            "what we value",
            "bonus qualifications",
            "preferred qualifications",
            "desired qualifications",
            "nice to have",
            "nice to haves",
            "desired",
            "desirable skills",
            "desired skills and experience",
            "bonus points",
            "preferred",
            "bonus",
            "good to have",
            "you may also have",
            "preferred if you have experience in any of the following",
            "superpowers/ skills that will help you succeed in this role",
        ],
        _P,
    ),
    **dict.fromkeys(
        [
            "in this role, you will",
            "in this role you will",
            "core responsibilities",
            "coreresponsiblities",
            "responsibilities",
            "key responsibilities",
            "key responsibility",
            "responsibilities would be as follows",
            "role and responsibilities",
            "job duties and responsibilities",
            "what you'll do",
            "what you will do",
            "what will you do",
            "what will you be doing",
            "what you'll be doing",
            "what you will be doing",
            "what you'll drive",
            "what you will own",
            "what is expected out of you",
            "position expectations",
            "success measures",
            "what success looks like in the role",
            "what success looks like in this role",
            "technologies we use",
            "about the team",
            "the team",
            "about the role",
            "about role",
            "about the job",
            "the role",
            "role overview",
            "overview of the role",
            "an overview of this role",
            "about team & about role",
            "about the team & role",
            "role purpose",
            "role purpose & impact",
            "role profile",
            "role summary",
            "job summary",
            "job brief",
            "job snapshot",
            "position summary",
            "the opportunity",
            "opportunity",
            "the impact you will have",
            "the impact you'll make",
            "where you can make an impact",
            "what you will learn",
            "what is the team like",
        ],
        _N,
    ),
    **dict.fromkeys(
        [
            "benefits",
            "our benefits",
            "perks",
            "perks & benefits",
            "what we offer",
            "who we are",
            "about us",
            "compliance",
            "compensation",
            "competitive compensation",
            "competitive compensation including equity",
            "salary range",
            "us pay range",
            "pay range transparency",
            "mongodb's base salary range for this role in the u.s. is",
            "great earning potential",
            "requisition id",
            "why join us",
            "why you should apply",
            "why you'll love working here",
            "why you'll love it here",
            "things we love",
            "our commitment to diversity and inclusion",
            "our commitment to inclusion and belonging",
            "eeo is the law",
            "equal opportunity employer/veterans/disabled",
            "twilio is proud to be an equal opportunity employer",
            "inmobi is an equal opportunity employer",
            "privacy policy",
            "data privacy notice for job candidates",
            "country hiring guidelines",
            "stay alert to recruitment fraud",
            "hiring and how we work",
            "how gitlab supports full-time employees",
            "inclusion @ rubrik",
            "the okta experience",
            "get to know okta",
            "driving social impact",
            "supporting your well-being",
            "secure every identity, from ai to human",
            "twilio thinks big. do you",
            "see yourself at twilio",
            "inmobi (corporate)",
            "inmobi advertising",
            "glance",
            "award-winning culture, best-in-class benefits",
            "employee assistance program (eap)",
            "what's the inmobi family like",
            "who are we and what do we do",
            "a quick snapshot of our u.s. benefits",
            "what do we promise",
            "what can we promise",
            "our culture & values",
            "our vision",
            "our values",
            "the values that form our foundation are",
            "location",
            "travel",
            "where you'll be",
            "our principles",
            "to apply, please submit the following",
        ],
        _B,
    ),
}


def heading_kind(heading: str | None, company: str | None = None) -> SectionKind | None:
    if heading is None:
        return None
    known = HEADING_KINDS.get(heading)
    if known is not None:
        return known
    company_key = normalise(company)
    if company_key is not None and heading == f"about {company_key}":
        return SectionKind.BOILERPLATE
    return None


def split_html_sections(markup: object, *, company: str | None = None) -> list[Section]:
    """h1-h6 always start a section; <strong>/<b> only when their text is a known heading,
    so inline emphasis does not cut a requirements list in half."""
    source = html.unescape(str(markup or ""))
    sections: list[Section] = []
    kind, start = SectionKind.NEUTRAL, 0
    for match in _HEADING_TAG.finditer(source):
        known = heading_kind(normalise_heading(html_to_text(match.group(2))), company)
        if known is None and not match.group(1).lower().startswith("h"):
            continue
        sections.append(Section(kind, html_to_text(source[start : match.start()])))
        kind, start = known or SectionKind.NEUTRAL, match.end()
    sections.append(Section(kind, html_to_text(source[start:])))
    return refine_sections([section for section in sections if section.text], company=company)


_MAX_HEADING_LINE: Final = 80


def split_lines_into_sections(
    text: str, kind: SectionKind, *, company: str | None = None
) -> list[Section]:
    """Plain-text headings: a line that is exactly a known heading starts a new section.
    Unknown lines never split, so a stray short sentence cannot end a requirements list."""
    sections: list[Section] = []
    current: list[str] = []
    for line in text.splitlines():
        known = (
            heading_kind(normalise_heading(line), company)
            if len(line) <= _MAX_HEADING_LINE
            else None
        )
        if known is None:
            current.append(line)
            continue
        sections.append(Section(kind, "\n".join(current)))
        kind, current = known, []
    sections.append(Section(kind, "\n".join(current)))
    return [section for section in sections if section.text.strip()]


def refine_sections(sections: Sequence[Section], *, company: str | None = None) -> list[Section]:
    return [
        part
        for section in sections
        for part in split_lines_into_sections(section.text, section.kind, company=company)
    ]


# --- Extraction ---


class SkillExtractor:
    def __init__(self, source: SkillCatalogue | Mapping[str, str]) -> None:
        """From the catalogue file (tests, tooling) or alias -> name from skill_aliases (sync)."""
        if isinstance(source, SkillCatalogue):
            self._alias_to_skill = {
                alias: skill.name for skill in source.skills for alias in skill.aliases
            }
        else:
            self._alias_to_skill = dict(source)
        if not self._alias_to_skill:
            # An empty alternation would match the empty string at every position.
            raise ValueError("no skill aliases to match")
        # Longest alias first, so at any position "react native" wins over "react".
        alternation = "|".join(
            re.escape(alias) for alias in sorted(self._alias_to_skill, key=len, reverse=True)
        )
        self._pattern = re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])")

    def extract(
        self, sections: Sequence[Section], *, exclude_aliases: Collection[str] = ()
    ) -> dict[str, SkillRequirement]:
        """Canonical skill name -> requirement. Required anywhere beats preferred elsewhere."""
        excluded = {key for alias in exclude_aliases if (key := normalise(alias)) is not None}
        found: dict[str, SkillRequirement] = {}
        for section in sections:
            if section.kind is SectionKind.BOILERPLATE:
                continue
            requirement = (
                SkillRequirement.REQUIRED
                if section.kind is SectionKind.REQUIRED
                else SkillRequirement.PREFERRED
            )
            for match in self._pattern.finditer(normalise(section.text) or ""):
                alias = match.group(0)
                if alias in excluded:
                    continue
                name = self._alias_to_skill[alias]
                if found.get(name) is not SkillRequirement.REQUIRED:
                    found[name] = requirement
        return found
