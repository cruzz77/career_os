"""Queries on users."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email))


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def insert_if_email_free(
    session: AsyncSession, *, email: str, password_hash: str
) -> User | None:
    """None when the email is taken. ON CONFLICT avoids a check-then-insert race, and email is
    citext, so the conflict is case-insensitive."""
    statement = (
        pg_insert(User)
        .values(email=email, password_hash=password_hash)
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User)
    )
    return await session.scalar(statement)


async def update_password_hash(
    session: AsyncSession, *, user_id: uuid.UUID, password_hash: str
) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(password_hash=password_hash)
    )


async def delete_by_id(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Cascades to every table holding the user's data (sessions, profile, preferences...)."""
    deleted = await session.scalar(delete(User).where(User.id == user_id).returning(User.id))
    return deleted is not None
