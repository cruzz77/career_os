"""Queries on server-side sessions. Every lookup is by token hash, never by plaintext token."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserSession


async def insert(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: bytes,
    created_at: datetime,
    expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
) -> UserSession:
    row = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        created_at=created_at,
        last_seen_at=created_at,
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    session.add(row)
    await session.flush()
    return row


async def get_active(
    session: AsyncSession, *, token_hash: bytes, now: datetime, absolute_ttl: timedelta
) -> UserSession | None:
    statement = select(UserSession).where(
        UserSession.token_hash == token_hash,
        UserSession.expires_at > now,
        UserSession.created_at > now - absolute_ttl,
    )
    return await session.scalar(statement)


async def extend(
    session: AsyncSession, *, session_id: uuid.UUID, now: datetime, expires_at: datetime
) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.id == session_id)
        .values(last_seen_at=now, expires_at=expires_at)
    )


async def delete_by_token_hash(session: AsyncSession, token_hash: bytes) -> bool:
    deleted = await session.scalar(
        delete(UserSession).where(UserSession.token_hash == token_hash).returning(UserSession.id)
    )
    return deleted is not None
