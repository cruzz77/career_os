"""Users, sessions, profiles and the skill catalogue."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, CITEXT, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_check
from app.domain.enums import ExperienceLevel, UserSkillSource

_SHA256_BYTES = 32


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("length(email) BETWEEN 3 AND 254", name="email_length"),
    )

    email: Mapped[str] = mapped_column(CITEXT)
    password_hash: Mapped[str] = mapped_column(Text)


class UserSession(UUIDPrimaryKey, Timestamps, Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored; absolute expiry
    is derived from created_at, sliding expiry is expires_at."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint(f"octet_length(token_hash) = {_SHA256_BYTES}", name="token_hash_length"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = mapped_column(BYTEA)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    last_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)


class UserProfile(Timestamps, Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        enum_check("experience_level", ExperienceLevel),
        CheckConstraint(
            "years_experience IS NULL OR years_experience BETWEEN 0 AND 80",
            name="years_experience_range",
        ),
        CheckConstraint(
            f"resume_sha256 IS NULL OR octet_length(resume_sha256) = {_SHA256_BYTES}",
            name="resume_sha256_length",
        ),
        CheckConstraint("profile_version >= 1", name="profile_version_positive"),
        CheckConstraint("jsonb_typeof(education) = 'array'", name="education_is_array"),
        CheckConstraint("jsonb_typeof(experience) = 'array'", name="experience_is_array"),
        CheckConstraint("jsonb_typeof(projects) = 'array'", name="projects_is_array"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    headline: Mapped[str | None] = mapped_column(Text)
    experience_level: Mapped[str | None] = mapped_column(Text)
    years_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    education: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    experience: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    projects: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    interests: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    resume_text: Mapped[str | None] = mapped_column(Text)
    resume_sha256: Mapped[bytes | None] = mapped_column(BYTEA)
    reviewed_at: Mapped[datetime | None]
    profile_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Skill(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "skills"
    __table_args__ = (
        # Case-insensitive uniqueness without forcing a display casing on the stored name.
        Index("uq_skills_lower_canonical_name", text("lower(canonical_name)"), unique=True),
        CheckConstraint("length(btrim(canonical_name)) > 0", name="canonical_name_not_blank"),
    )

    canonical_name: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)


class SkillAlias(Timestamps, Base):
    __tablename__ = "skill_aliases"
    __table_args__ = (
        CheckConstraint("alias = lower(alias)", name="alias_lowercase"),
        CheckConstraint("length(btrim(alias)) > 0", name="alias_not_blank"),
    )

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )


class UserSkill(Timestamps, Base):
    __tablename__ = "user_skills"
    __table_args__ = (enum_check("source", UserSkillSource),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    source: Mapped[str] = mapped_column(Text)
