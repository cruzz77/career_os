"""Password hashing, session tokens and constant-time secret comparison."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Final

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

SESSION_TOKEN_BYTES: Final = 32
# token_urlsafe(32) is 43 characters; anything longer is not one of ours and is rejected
# before hashing so an attacker cannot make us hash megabytes per request.
MAX_SESSION_TOKEN_LENGTH: Final = 64


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    valid: bool
    # Set when the stored hash used weaker parameters than the current ones and should be
    # replaced; the caller persists it.
    updated_hash: str | None


class PasswordHasher:
    """Argon2id via pwdlib. Hashing is CPU-bound (~tens of ms), so it runs in a worker thread
    instead of stalling the event loop for every other request."""

    def __init__(self, hasher: Argon2Hasher | None = None) -> None:
        self._hash = PasswordHash((hasher or Argon2Hasher(),))
        self._dummy_hash = self._hash.hash(secrets.token_urlsafe(16))

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hash.hash, password)

    async def verify(self, password: str, password_hash: str) -> PasswordCheck:
        valid, updated = await asyncio.to_thread(
            self._hash.verify_and_update, password, password_hash
        )
        return PasswordCheck(valid=valid, updated_hash=updated if valid else None)

    async def verify_against_dummy(self, password: str) -> None:
        # Unknown email: spend the same time as a real check, so response timing does not
        # reveal which emails are registered.
        await asyncio.to_thread(self._hash.verify, password, self._dummy_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> bytes:
    """Only this digest is stored, so a database leak does not hand out live sessions."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def secrets_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
