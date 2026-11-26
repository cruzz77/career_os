"""Signup, login, logout, session authentication and account deletion."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthenticatedError,
)
from app.core.rate_limit import RateLimit, RateLimiter
from app.core.security import (
    MAX_SESSION_TOKEN_LENGTH,
    PasswordHasher,
    hash_session_token,
    new_session_token,
)
from app.db.session import Database
from app.domain.auth import (
    AuthenticatedUser,
    AuthResult,
    ClientInfo,
    IssuedSession,
    LoginRequest,
    SignupRequest,
    UserPublic,
)
from app.repositories import sessions, users

Clock = Callable[[], datetime]

# One message for unknown email and wrong password, so the response never says which.
_INVALID_CREDENTIALS: Final = "Invalid email or password"
_MAX_USER_AGENT_LENGTH: Final = 512


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    sliding_ttl: timedelta
    absolute_ttl: timedelta
    # Extending on every request would turn every read into a write; once a minute is plenty.
    touch_interval: timedelta = timedelta(minutes=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> SessionPolicy:
        return cls(
            sliding_ttl=timedelta(days=settings.session_sliding_ttl_days),
            absolute_ttl=timedelta(days=settings.session_absolute_ttl_days),
        )

    def expiry(self, *, created_at: datetime, now: datetime) -> datetime:
        return min(now + self.sliding_ttl, created_at + self.absolute_ttl)


class AuthService:
    def __init__(
        self,
        database: Database,
        hasher: PasswordHasher,
        policy: SessionPolicy,
        *,
        rate_limiter: RateLimiter,
        login_email_limit: RateLimit,
        clock: Clock = utc_now,
    ) -> None:
        self._database = database
        self._hasher = hasher
        self._policy = policy
        self._rate_limiter = rate_limiter
        self._login_email_limit = login_email_limit
        self._clock = clock

    async def signup(self, request: SignupRequest, client: ClientInfo) -> AuthResult:
        password_hash = await self._hasher.hash(request.password)
        async with self._database.session() as session, session.begin():
            user = await users.insert_if_email_free(
                session, email=request.email, password_hash=password_hash
            )
            if user is None:
                raise ConflictError("An account with this email already exists")
            issued = await self._issue_session(session, user.id, client)
            return AuthResult(user=UserPublic.model_validate(user), session=issued)

    async def login(self, request: LoginRequest, client: ClientInfo) -> AuthResult:
        # Per account, independent of IP: stops slow guessing against one email from many IPs.
        decision = await self._rate_limiter.hit(
            f"email:{request.email.lower()}", self._login_email_limit
        )
        if not decision.allowed:
            raise RateLimitedError(
                "Too many login attempts for this account",
                retry_after_s=decision.retry_after_s,
            )
        # Lookup and writes are separate transactions, so no pooled connection is held while
        # Argon2 runs.
        async with self._database.session() as session:
            user = await users.get_by_email(session, request.email)
        if user is None:
            await self._hasher.verify_against_dummy(request.password)
            raise UnauthenticatedError(_INVALID_CREDENTIALS)
        check = await self._hasher.verify(request.password, user.password_hash)
        if not check.valid:
            raise UnauthenticatedError(_INVALID_CREDENTIALS)
        async with self._database.session() as session, session.begin():
            if check.updated_hash is not None:
                await users.update_password_hash(
                    session, user_id=user.id, password_hash=check.updated_hash
                )
            issued = await self._issue_session(session, user.id, client)
        return AuthResult(user=UserPublic.model_validate(user), session=issued)

    async def authenticate(self, token: str) -> AuthenticatedUser | None:
        if not token or len(token) > MAX_SESSION_TOKEN_LENGTH:
            return None
        now = self._clock()
        async with self._database.session() as session, session.begin():
            row = await sessions.get_active(
                session,
                token_hash=hash_session_token(token),
                now=now,
                absolute_ttl=self._policy.absolute_ttl,
            )
            if row is None:
                return None
            if now - row.last_seen_at >= self._policy.touch_interval:
                await sessions.extend(
                    session,
                    session_id=row.id,
                    now=now,
                    expires_at=self._policy.expiry(created_at=row.created_at, now=now),
                )
            return AuthenticatedUser(user_id=row.user_id, session_id=row.id)

    async def logout(self, token: str) -> None:
        if not token or len(token) > MAX_SESSION_TOKEN_LENGTH:
            return
        async with self._database.session() as session, session.begin():
            await sessions.delete_by_token_hash(session, hash_session_token(token))

    async def get_user(self, user_id: uuid.UUID) -> UserPublic:
        async with self._database.session() as session:
            user = await users.get_by_id(session, user_id)
        if user is None:
            raise NotFoundError("User not found")
        return UserPublic.model_validate(user)

    async def delete_account(self, user_id: uuid.UUID) -> None:
        async with self._database.session() as session, session.begin():
            if not await users.delete_by_id(session, user_id):
                raise NotFoundError("User not found")

    async def _issue_session(
        self, session: AsyncSession, user_id: uuid.UUID, client: ClientInfo
    ) -> IssuedSession:
        token = new_session_token()
        now = self._clock()
        expires_at = self._policy.expiry(created_at=now, now=now)
        await sessions.insert(
            session,
            user_id=user_id,
            token_hash=hash_session_token(token),
            created_at=now,
            expires_at=expires_at,
            user_agent=_clean_user_agent(client.user_agent),
            ip=_clean_ip(client.ip),
        )
        return IssuedSession(token=token, expires_at=expires_at)


def _clean_user_agent(user_agent: str | None) -> str | None:
    return user_agent[:_MAX_USER_AGENT_LENGTH] if user_agent else None


def _clean_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None
