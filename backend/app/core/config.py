"""Application settings, read from the process environment only."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_DB_SCHEME = "postgresql+psycopg://"
_MIN_PROXY_SECRET_LEN = 32


class Settings(BaseSettings):
    # No env_file on purpose: the process environment is the single source, so a stray .env on
    # disk can never override what compose or CI injected. Locally: `uv run --env-file .env ...`.
    # hide_input_in_errors keeps a malformed DATABASE_URL's password out of startup tracebacks.
    model_config = SettingsConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    environment: Environment = "local"
    log_level: LogLevel = "INFO"
    log_json: bool = True

    database_url: SecretStr
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_s: float = Field(default=10.0, gt=0)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    frontend_origin: str
    proxy_secret: SecretStr

    session_cookie_name: str = "careeros_session"
    session_cookie_secure: bool = True
    session_sliding_ttl_days: int = Field(default=7, ge=1)
    session_absolute_ttl_days: int = Field(default=30, ge=1)

    # Owned by the Retrieval chat; the Dockerfile bakes the same value in as a build arg.
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(_DB_SCHEME):
            raise ValueError(f"must start with {_DB_SCHEME} (async SQLAlchemy over psycopg 3)")
        return value

    @field_validator("frontend_origin")
    @classmethod
    def _require_bare_origin(cls, value: str) -> str:
        has_scheme = value.startswith(("https://", "http://"))
        if not has_scheme or value.endswith("/") or value.count("/") != 2:
            raise ValueError("must be a bare origin like https://app.example.com")
        return value

    @field_validator("proxy_secret")
    @classmethod
    def _require_long_proxy_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MIN_PROXY_SECRET_LEN:
            raise ValueError(f"must be at least {_MIN_PROXY_SECRET_LEN} characters")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.session_sliding_ttl_days > self.session_absolute_ttl_days:
            raise ValueError("session_sliding_ttl_days cannot exceed session_absolute_ttl_days")
        if self.environment == "production":
            if not self.session_cookie_secure:
                raise ValueError("session_cookie_secure must be true in production")
            if not self.frontend_origin.startswith("https://"):
                raise ValueError("frontend_origin must be https in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
