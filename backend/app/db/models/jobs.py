"""Job index: companies, postings, their skills and embeddings, ingestion bookkeeping, and the
per-user records that point at jobs (saved jobs, recommendation logs)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_check
from app.domain.enums import EmploymentType, JobStatus, Seniority, SkillRequirement, WorkMode

EMBEDDING_DIMENSIONS: Final = 384
_SHA256_BYTES: Final = 32
_ACTIVE: Final = text("status = 'active'")

# The regconfig is explicit because generated columns only accept immutable expressions, and
# to_tsvector without one depends on the session's default_text_search_config.
_SEARCH_TSV: Final = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(skills_text, '')), 'B') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(description, '')), 'C')"
)


def _sha256_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"octet_length({column}) = {_SHA256_BYTES}", name=f"{column}_length")


class Company(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("normalized_name"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(normalized_name) > 0", name="normalized_name_not_blank"),
    )

    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)


class Job(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id"),
        enum_check("work_mode", WorkMode),
        enum_check("employment_type", EmploymentType),
        enum_check("seniority", Seniority),
        enum_check("status", JobStatus),
        _sha256_check("content_hash"),
        _sha256_check("canonical_hash"),
        CheckConstraint(
            "expires_at IS NULL OR posted_at IS NULL OR expires_at >= posted_at",
            name="expires_after_posted",
        ),
        CheckConstraint("last_seen_at >= first_seen_at", name="seen_in_order"),
        Index("ix_jobs_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_jobs_active_work_mode_location_city",
            "work_mode",
            "location_city",
            postgresql_where=_ACTIVE,
        ),
        Index(
            "ix_jobs_active_employment_type_seniority",
            "employment_type",
            "seniority",
            postgresql_where=_ACTIVE,
        ),
    )

    source: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    # Nullable on purpose: some feeds omit the company, and inventing an "Unknown" company
    # would assert something the source never said.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    # Denormalised cache of the canonical names in job_skills, feeding BM25 and FTS.
    # Invalidation path: the ingestion upsert rewrites it whenever it rewrites job_skills.
    skills_text: Mapped[str] = mapped_column(Text, server_default=text("''"))
    location_city: Mapped[str | None] = mapped_column(Text)
    location_country: Mapped[str | None] = mapped_column(Text)
    work_mode: Mapped[str] = mapped_column(Text, server_default=text("'unknown'"))
    employment_type: Mapped[str] = mapped_column(Text, server_default=text("'unknown'"))
    seniority: Mapped[str] = mapped_column(Text, server_default=text("'unknown'"))
    posted_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    content_hash: Mapped[bytes] = mapped_column(BYTEA)
    canonical_hash: Mapped[bytes] = mapped_column(BYTEA, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    last_seen_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source_url: Mapped[str | None] = mapped_column(Text)
    # Deferred: only the FTS retriever reads it, and it is large.
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed(_SEARCH_TSV, persisted=True), deferred=True
    )


class JobSkill(Timestamps, Base):
    __tablename__ = "job_skills"
    __table_args__ = (enum_check("requirement", SkillRequirement),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    requirement: Mapped[str] = mapped_column(Text)


class JobEmbedding(Timestamps, Base):
    __tablename__ = "job_embeddings"
    __table_args__ = (
        _sha256_check("content_hash"),
        CheckConstraint("length(btrim(model_version)) > 0", name="model_version_not_blank"),
        Index(
            "ix_job_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    model_version: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding: Mapped[NDArray[np.float32]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # The job content_hash this vector was computed from; a mismatch marks it stale.
    content_hash: Mapped[bytes] = mapped_column(BYTEA)
    embedded_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class IngestionRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "fetched >= 0 AND inserted >= 0 AND updated >= 0 AND unchanged >= 0 AND failed >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
        ),
    )

    source: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    finished_at: Mapped[datetime | None]
    fetched: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    inserted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    unchanged: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    notes: Mapped[str | None] = mapped_column(Text)


class SavedJob(Timestamps, Base):
    __tablename__ = "saved_jobs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    note: Mapped[str | None] = mapped_column(Text)


class RecommendationLog(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "recommendation_logs"
    __table_args__ = (
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint(
            "jsonb_typeof(score_breakdown) = 'object'", name="score_breakdown_is_object"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    config_version: Mapped[str] = mapped_column(Text)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
