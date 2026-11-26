from __future__ import annotations

import re

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.security import (
    MAX_SESSION_TOKEN_LENGTH,
    PasswordHasher,
    hash_session_token,
    new_session_token,
    secrets_match,
)

_PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher()


async def test_hash_is_argon2id_and_never_the_plaintext(hasher: PasswordHasher) -> None:
    hashed = await hasher.hash(_PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert _PASSWORD not in hashed


async def test_same_password_hashes_differently_each_time(hasher: PasswordHasher) -> None:
    assert await hasher.hash(_PASSWORD) != await hasher.hash(_PASSWORD)


async def test_correct_password_verifies(hasher: PasswordHasher) -> None:
    check = await hasher.verify(_PASSWORD, await hasher.hash(_PASSWORD))
    assert check.valid
    assert check.updated_hash is None


async def test_wrong_password_is_refused(hasher: PasswordHasher) -> None:
    check = await hasher.verify("wrong password entirely", await hasher.hash(_PASSWORD))
    assert not check.valid
    assert check.updated_hash is None


async def test_weaker_stored_hash_is_upgraded_on_successful_login(hasher: PasswordHasher) -> None:
    weak = PasswordHasher(Argon2Hasher(time_cost=1, memory_cost=8 * 1024, parallelism=1))
    check = await hasher.verify(_PASSWORD, await weak.hash(_PASSWORD))
    assert check.valid
    assert check.updated_hash is not None
    assert (await hasher.verify(_PASSWORD, check.updated_hash)).updated_hash is None


async def test_weaker_hash_is_not_upgraded_on_failed_login(hasher: PasswordHasher) -> None:
    weak = PasswordHasher(Argon2Hasher(time_cost=1, memory_cost=8 * 1024, parallelism=1))
    check = await hasher.verify("wrong password entirely", await weak.hash(_PASSWORD))
    assert not check.valid
    assert check.updated_hash is None


async def test_dummy_verification_completes(hasher: PasswordHasher) -> None:
    await hasher.verify_against_dummy(_PASSWORD)


def test_session_tokens_are_urlsafe_unique_and_short() -> None:
    tokens = {new_session_token() for _ in range(100)}
    assert len(tokens) == 100
    for token in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
        assert len(token) <= MAX_SESSION_TOKEN_LENGTH


def test_token_hash_is_32_bytes_deterministic_and_distinct() -> None:
    token = new_session_token()
    assert len(hash_session_token(token)) == 32
    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != hash_session_token(new_session_token())


@pytest.mark.parametrize(
    ("provided", "expected", "match"),
    [
        ("a" * 32, "a" * 32, True),
        ("a" * 32, "b" * 32, False),
        ("a" * 31, "a" * 32, False),
        ("", "a", False),
    ],
)
def test_secrets_match(provided: str, expected: str, match: bool) -> None:
    assert secrets_match(provided, expected) is match
