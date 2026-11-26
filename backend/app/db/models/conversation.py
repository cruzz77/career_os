"""Conversations, messages, agent run logs and the versioned preference store."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_check
from app.domain.enums import (
    SINGLE_VALUED_DIMENSIONS,
    AgentRunStatus,
    MessageRole,
    PreferenceDimension,
    PreferenceOperator,
    PreferenceSource,
    PreferenceStrength,
    ToolCallStatus,
)

_ACTIVE: Final = "superseded_at IS NULL"
_SINGLE_VALUED_SQL: Final = ", ".join(f"'{d.value}'" for d in sorted(SINGLE_VALUED_DIMENSIONS))


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "pending_patch IS NULL OR jsonb_typeof(pending_patch) = 'object'",
            name="pending_patch_is_object",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # A PreferencePatch waiting on a clarification answer (blueprint 6.1).
    pending_patch: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Message(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "messages"
    __table_args__ = (
        enum_check("role", MessageRole),
        CheckConstraint(
            "(role = 'tool') = (tool_call_id IS NOT NULL)", name="tool_call_id_iff_tool_role"
        ),
        # Leading column also serves the conversation_id foreign key.
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    tool_call_id: Mapped[str | None] = mapped_column(Text)


class UserPreference(UUIDPrimaryKey, Timestamps, Base):
    """Append-only: a change supersedes the active row and inserts a new one."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        enum_check("dimension", PreferenceDimension),
        enum_check("operator", PreferenceOperator),
        enum_check("strength", PreferenceStrength),
        enum_check("source", PreferenceSource),
        # A clear op supersedes without a successor, so only one direction is enforced.
        CheckConstraint(
            "superseded_by IS NULL OR superseded_at IS NOT NULL",
            name="superseded_by_requires_superseded_at",
        ),
        CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= valid_from",
            name="superseded_after_valid_from",
        ),
        Index("ix_user_preferences_active_user_id", "user_id", postgresql_where=text(_ACTIVE)),
        Index(
            "uq_user_preferences_active_single_valued",
            "user_id",
            "dimension",
            unique=True,
            postgresql_where=text(f"{_ACTIVE} AND dimension IN ({_SINGLE_VALUED_SQL})"),
        ),
    )

    # Full index as well as the active-only one: cascading deletes must find superseded rows.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    dimension: Mapped[str] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSONB)
    strength: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    valid_from: Mapped[datetime] = mapped_column(server_default=text("now()"))
    superseded_at: Mapped[datetime | None]
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_preferences.id", ondelete="SET NULL"), index=True
    )


class AgentRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        enum_check("status", AgentRunStatus),
        CheckConstraint("steps >= 0", name="steps_non_negative"),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0)",
            name="tokens_non_negative",
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint("status <> 'succeeded' OR error IS NULL", name="succeeded_has_no_error"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(Text)
    steps: Mapped[int] = mapped_column(Integer)
    # Nullable: a run that fails before the first LLM response has no usage to report.
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class ToolCall(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        enum_check("status", ToolCallStatus),
        # Arguments that failed validation must never be recorded as having run.
        CheckConstraint("args_valid OR status = 'rejected'", name="invalid_args_rejected"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
    )

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(Text)
    args: Mapped[Any] = mapped_column(JSONB)
    args_valid: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[Any | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
