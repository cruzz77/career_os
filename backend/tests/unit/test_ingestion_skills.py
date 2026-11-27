from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.enums import SkillRequirement
from app.modules.ingestion.skills import (
    Section,
    SectionKind,
    SkillCatalogue,
    SkillExtractor,
    heading_kind,
    html_to_text,
    normalise_heading,
    refine_sections,
    split_html_sections,
)

REQ, PREF = SkillRequirement.REQUIRED, SkillRequirement.PREFERRED
K = SectionKind


def _catalogue(skills: list[dict[str, Any]]) -> SkillCatalogue:
    return SkillCatalogue.model_validate({"version": 1, "categories": ["x"], "skills": skills})


MINI = _catalogue(
    [
        {"name": n, "category": "x", "aliases": a}
        for n, a in [
            ("React", ["react"]), ("React Native", ["react native"]), ("Ruby", ["ruby"]),
            ("Ruby on Rails", ["ruby on rails"]), ("Apache Spark", ["apache spark", "spark"]),
            ("SQL", ["sql"]), ("MySQL", ["mysql"]), ("NoSQL", ["nosql"]), ("Java", ["java"]),
            ("JavaScript", ["javascript"]), ("PostgreSQL", ["postgresql", "postgres"]),
            ("Python", ["python"]), ("Kafka", ["kafka"]), ("AWS", ["aws"]),
            ("MongoDB", ["mongodb"]),
        ]
    ]
)  # fmt: skip
EXTRACT = SkillExtractor(MINI)


def _skills(text: str) -> set[str]:
    return set(EXTRACT.extract([Section(K.REQUIRED, text)]))


def test_shipped_catalogue_loads_and_validates() -> None:
    catalogue = SkillCatalogue.load()
    assert catalogue.version >= 1
    assert len(catalogue.skills) >= 150


@pytest.mark.parametrize(
    "skills",
    [
        [{"name": "A", "category": "x", "aliases": ["dup"]},
         {"name": "B", "category": "x", "aliases": ["dup"]}],
        [{"name": "Python", "category": "x", "aliases": ["Python"]}],
        [{"name": "A", "category": "nope", "aliases": ["a"]}],
        [{"name": "Go", "category": "x", "aliases": ["golang"]},
         {"name": "go", "category": "x", "aliases": ["go lang"]}],
    ],
    ids=["duplicate-alias", "alias-not-normalised", "unknown-category", "duplicate-name"],
)  # fmt: skip
def test_invalid_catalogue_is_rejected(skills: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        _catalogue(skills)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("experience with React Native", {"React Native"}),
        ("Ruby on Rails apps", {"Ruby on Rails"}),
        ("Apache Spark or plain spark", {"Apache Spark"}),
    ],
)
def test_longest_alias_wins(text: str, expected: set[str]) -> None:
    assert _skills(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MySQL at scale", {"MySQL"}),
        ("modern JavaScript", {"JavaScript"}),
        ("NoSQL stores", {"NoSQL"}),
        ("the internals of Postgres", {"PostgreSQL"}),
    ],
)
def test_no_matches_inside_longer_words(text: str, expected: set[str]) -> None:
    assert _skills(text) == expected


def test_company_name_is_not_a_skill() -> None:
    found = EXTRACT.extract(
        [Section(K.REQUIRED, "MongoDB is hiring. You know MongoDB and Kafka.")],
        exclude_aliases=["MongoDB"],
    )
    assert found == {"Kafka": REQ}


def test_requirement_follows_the_section() -> None:
    found = EXTRACT.extract(
        [
            Section(K.NEUTRAL, "You will build services in Java"),
            Section(K.REQUIRED, "Strong Python"),
            Section(K.PREFERRED, "Kafka is a plus"),
            Section(K.BOILERPLATE, "Our platform runs on AWS"),
        ]
    )
    assert found == {"Java": PREF, "Python": REQ, "Kafka": PREF}


def test_required_anywhere_beats_preferred_elsewhere() -> None:
    found = EXTRACT.extract(
        [Section(K.PREFERRED, "Python nice to have"), Section(K.REQUIRED, "Python"),
         Section(K.PREFERRED, "more Python")]
    )  # fmt: skip
    assert found == {"Python": REQ}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("What You\u2019ll Bring:", "what you'll bring"),
        ("In this role, you will...", "in this role, you will"),
        ("  Nice to have  ", "nice to have"),
        ("", None),
    ],
)
def test_normalise_heading(raw: str, expected: str | None) -> None:
    assert normalise_heading(raw) == expected


@pytest.mark.parametrize(
    ("heading", "company", "expected"),
    [
        ("what we require", None, K.REQUIRED),
        ("bonus qualifications", None, K.PREFERRED),
        ("responsibilities", None, K.NEUTRAL),
        ("about acme", "Acme", K.BOILERPLATE),
        ("something else", "Acme", None),
    ],
)
def test_heading_kind(heading: str, company: str | None, expected: SectionKind | None) -> None:
    assert heading_kind(heading, company) is expected


def test_greenhouse_html_sections_drive_requirements() -> None:
    # Greenhouse sends entity-escaped HTML.
    content = (
        "&lt;h2&gt;About Acme&lt;/h2&gt;&lt;p&gt;We run on AWS&lt;/p&gt;"
        "&lt;p&gt;&lt;strong&gt;What we look for:&lt;/strong&gt;&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
        "&lt;p&gt;&lt;strong&gt;Nice to have&lt;/strong&gt;&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;Kafka&lt;/li&gt;&lt;/ul&gt;"
        "&lt;p&gt;Use &lt;strong&gt;great&lt;/strong&gt; judgement&lt;/p&gt;"
    )
    sections = split_html_sections(content, company="Acme")
    assert [s.kind for s in sections] == [K.BOILERPLATE, K.REQUIRED, K.PREFERRED]
    assert "great judgement" in sections[-1].text
    assert EXTRACT.extract(sections) == {"Python": REQ, "Kafka": PREF}


def test_html_to_text_unescapes_twice_and_keeps_lines() -> None:
    markup = (
        "&lt;p&gt;R&amp;amp;D&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;li&gt;Two&lt;/li&gt;&lt;/ul&gt;"
    )
    assert html_to_text(markup) == "R&D\nOne\nTwo"


def test_plain_text_heading_lines_split_sections() -> None:
    text = (
        "Key Responsibilities:\nBuild services in Java\n"
        "Expectations/ Requirements:\nStrong Python\n"
        "Why join us:\nWe run on AWS"
    )
    sections = refine_sections([Section(K.NEUTRAL, text)])
    assert [s.kind for s in sections] == [K.NEUTRAL, K.REQUIRED, K.BOILERPLATE]
    assert EXTRACT.extract(sections) == {"Java": PREF, "Python": REQ}


def test_heading_line_inside_html_ends_the_requirements() -> None:
    content = (
        "&lt;h2&gt;Requirements&lt;/h2&gt;&lt;p&gt;Python&lt;/p&gt;"
        "&lt;p&gt;Compensation&lt;/p&gt;&lt;p&gt;AWS stipend&lt;/p&gt;"
    )
    assert EXTRACT.extract(split_html_sections(content)) == {"Python": REQ}


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("expectations/ requirements", K.REQUIRED),
        ("you may also have", K.PREFERRED),
        ("key responsibilities", K.NEUTRAL),
        ("eeo is the law", K.BOILERPLATE),
    ],
)
def test_headings_added_from_the_2026_11_27_data(heading: str, expected: SectionKind) -> None:
    assert heading_kind(heading) is expected
