"""Auth request/response schemas and the value objects the auth service returns."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# The upper bound stops a multi-megabyte "password" from tying up an Argon2 worker.
MAX_PASSWORD_LENGTH: Final = 128
MIN_PASSWORD_LENGTH: Final = 12


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH, repr=False
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH, repr=False)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    email: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClientInfo:
    user_agent: str | None
    ip: str | None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    # repr=False keeps the live token out of any log line or traceback that prints this.
    token: str = field(repr=False)
    expires_at: datetime = field()


@dataclass(frozen=True, slots=True)
class AuthResult:
    user: UserPublic
    session: IssuedSession


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: uuid.UUID
    session_id: uuid.UUID
