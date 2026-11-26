"""Declarative base, constraint naming convention and shared column mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Final

from sqlalchemy import CheckConstraint, DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic names: migrations can drop exactly what they created, and a constraint
# violation in a log names the rule that was broken.
NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        uuid.UUID: UUID(as_uuid=True),
    }


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Bumped by a database trigger (migration 0001), not ORM onupdate: the ingestion CLI
    # updates rows in bulk and through raw SQL, which onupdate never sees.
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


def enum_check(column: str, allowed: type[StrEnum]) -> CheckConstraint:
    """CHECK constraint named ck_<table>_<column>, listing exactly the enum's values."""
    values = ", ".join(f"'{member.value}'" for member in allowed)
    return CheckConstraint(f"{column} IN ({values})", name=column)
