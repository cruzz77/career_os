from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import select

from app.core.errors import ConflictError, NotFoundError, UnauthenticatedError
from app.core.rate_limit import InMemoryRateLimiter, RateLimit
from app.core.security import PasswordHasher, hash_session_token
from app.db.models import User, UserSession
from app.db.session import Database
from app.domain.auth import ClientInfo, LoginRequest, SignupRequest
from app.modules.auth.service import AuthService, SessionPolicy
from tests.conftest import FakeClock

pytestmark = pytest.mark.integration

_PASSWORD = "correct horse battery staple"
_CLIENT = ClientInfo(user_agent="pytest-agent", ip="203.0.113.7")
_POLICY = SessionPolicy(sliding_ttl=timedelta(days=7), absolute_ttl=timedelta(days=30))
_HASHER = PasswordHasher()
# These tests exercise auth, not throttling; the limiter has its own tests.
_GENEROUS = RateLimit(name="login_per_email", max_requests=10_000, window_s=900)


def _email() -> str:
    return f"{uuid.uuid4().hex[:12]}@example.com"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC))


@pytest.fixture
def service(app_database: Database, clock: FakeClock) -> AuthService:
    return AuthService(
        app_database,
        _HASHER,
        _POLICY,
        rate_limiter=InMemoryRateLimiter(),
        login_email_limit=_GENEROUS,
        clock=clock,
    )


async def test_signup_issues_a_session_that_authenticates(service: AuthService) -> None:
    result = await service.signup(SignupRequest(email=_email(), password=_PASSWORD), _CLIENT)
    authenticated = await service.authenticate(result.session.token)
    assert authenticated is not None
    assert authenticated.user_id == result.user.id


async def test_duplicate_email_is_refused_case_insensitively(service: AuthService) -> None:
    local = uuid.uuid4().hex[:12]
    await service.signup(SignupRequest(email=f"{local}@example.com", password=_PASSWORD), _CLIENT)
    with pytest.raises(ConflictError):
        await service.signup(
            SignupRequest(email=f"{local.upper()}@example.com", password=_PASSWORD), _CLIENT
        )


async def test_unknown_email_and_wrong_password_fail_identically(service: AuthService) -> None:
    email = _email()
    await service.signup(SignupRequest(email=email, password=_PASSWORD), _CLIENT)
    with pytest.raises(UnauthenticatedError) as wrong_password:
        await service.login(LoginRequest(email=email, password="not the password"), _CLIENT)
    with pytest.raises(UnauthenticatedError) as unknown_email:
        await service.login(LoginRequest(email=_email(), password=_PASSWORD), _CLIENT)
    assert wrong_password.value.message == unknown_email.value.message


async def test_login_issues_a_new_distinct_session(service: AuthService) -> None:
    email = _email()
    signup = await service.signup(SignupRequest(email=email, password=_PASSWORD), _CLIENT)
    login = await service.login(LoginRequest(email=email, password=_PASSWORD), _CLIENT)
    assert login.user.id == signup.user.id
    assert login.session.token != signup.session.token


async def test_only_the_token_hash_is_stored(service: AuthService, app_database: Database) -> None:
    result = await service.signup(SignupRequest(email=_email(), password=_PASSWORD), _CLIENT)
    async with app_database.session() as session:
        row = await session.scalar(select(UserSession).where(UserSession.user_id == result.user.id))
    assert row is not None
    assert row.token_hash == hash_session_token(result.session.token)
    assert result.session.token.encode() not in row.token_hash
    assert row.user_agent == "pytest-agent"
    assert str(row.ip) == "203.0.113.7"
    assert result.session.token not in repr(result.session)


@pytest.mark.parametrize("token", ["", "x" * 65, "not-a-real-token-but-the-right-shape-xxxxxx"])
async def test_unknown_or_malformed_tokens_do_not_authenticate(
    service: AuthService, token: str
) -> None:
    assert await service.authenticate(token) is None


async def test_idle_session_expires_after_sliding_window(
    service: AuthService, clock: FakeClock
) -> None:
    result = await service.signup(SignupRequest(email=_email(), password=_PASSWORD), _CLIENT)
    clock.advance(timedelta(days=7, minutes=1))
    assert await service.authenticate(result.session.token) is None


async def test_activity_extends_the_sliding_window(service: AuthService, clock: FakeClock) -> None:
    result = await service.signup(SignupRequest(email=_email(), password=_PASSWORD), _CLIENT)
    clock.advance(timedelta(days=6))
    assert await service.authenticate(result.session.token) is not None
    clock.advance(timedelta(days=6))
    assert await service.authenticate(result.session.token) is not None


async def test_absolute_lifetime_caps_an_active_session(
    service: AuthService, clock: FakeClock
) -> None:
    result = await service.signup(SignupRequest(email=_email(), password=_PASSWORD), _CLIENT)
    for _ in range(4):
        clock.advance(timedelta(days=6))
        assert await service.authenticate(result.session.token) is not None
    # Day 30h+: the sliding window alone would allow it until day 31.
    clock.advance(timedelta(days=6, hours=1))
    assert await service.authenticate(result.session.token) is None


async def test_logout_revokes_only_that_session(service: AuthService) -> None:
    email = _email()
    first = await service.signup(SignupRequest(email=email, password=_PASSWORD), _CLIENT)
    second = await service.login(LoginRequest(email=email, password=_PASSWORD), _CLIENT)
    await service.logout(first.session.token)
    assert await service.authenticate(first.session.token) is None
    assert await service.authenticate(second.session.token) is not None


async def test_delete_account_removes_user_and_sessions(service: AuthService) -> None:
    result = await service.signup(SignupRequest(email=_email(), password=_PASSWORD), _CLIENT)
    await service.delete_account(result.user.id)
    assert await service.authenticate(result.session.token) is None
    with pytest.raises(NotFoundError):
        await service.get_user(result.user.id)
    with pytest.raises(NotFoundError):
        await service.delete_account(result.user.id)


async def test_weak_password_hash_is_upgraded_on_login(
    app_database: Database, clock: FakeClock, service: AuthService
) -> None:
    weak_hasher = PasswordHasher(Argon2Hasher(time_cost=1, memory_cost=8 * 1024, parallelism=1))
    weak_service = AuthService(
        app_database,
        weak_hasher,
        _POLICY,
        rate_limiter=InMemoryRateLimiter(),
        login_email_limit=_GENEROUS,
        clock=clock,
    )
    email = _email()
    result = await weak_service.signup(SignupRequest(email=email, password=_PASSWORD), _CLIENT)

    async def stored_hash() -> str:
        async with app_database.session() as session:
            user = await session.get(User, result.user.id)
        assert user is not None
        return user.password_hash

    weak_hash = await stored_hash()
    await service.login(LoginRequest(email=email, password=_PASSWORD), _CLIENT)
    upgraded = await stored_hash()
    assert upgraded != weak_hash
    assert (await _HASHER.verify(_PASSWORD, upgraded)).updated_hash is None
