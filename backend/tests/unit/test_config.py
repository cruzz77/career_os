from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_DB_PASSWORD = "hunter2-db-password"
_VALID: dict[str, object] = {
    "environment": "local",
    "database_url": f"postgresql+psycopg://careeros:{_DB_PASSWORD}@localhost:5432/careeros",
    "frontend_origin": "http://localhost:3000",
    "proxy_secret": "x" * 32,
    "session_cookie_secure": False,
}


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({**_VALID, **overrides})


def test_valid_local_settings_load() -> None:
    settings = _settings()
    assert settings.environment == "local"
    assert settings.session_sliding_ttl_days == 7
    assert settings.session_absolute_ttl_days == 30
    assert not settings.is_production


def test_valid_production_settings_load() -> None:
    settings = _settings(
        environment="production",
        frontend_origin="https://careeros.vercel.app",
        session_cookie_secure=True,
    )
    assert settings.is_production


def test_production_refuses_insecure_cookie() -> None:
    with pytest.raises(ValidationError, match="session_cookie_secure must be true"):
        _settings(environment="production", frontend_origin="https://careeros.vercel.app")


def test_production_refuses_http_origin() -> None:
    with pytest.raises(ValidationError, match="must be https in production"):
        _settings(environment="production", session_cookie_secure=True)


def test_non_psycopg_url_refused_without_leaking_password() -> None:
    with pytest.raises(ValidationError, match="must start with") as exc_info:
        _settings(database_url=f"postgresql://careeros:{_DB_PASSWORD}@localhost/careeros")
    assert _DB_PASSWORD not in str(exc_info.value)


def test_short_proxy_secret_refused() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _settings(proxy_secret="x" * 31)


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000/", "https://app.example.com/path", "app.example.com"],
)
def test_origin_with_path_or_no_scheme_refused(origin: str) -> None:
    with pytest.raises(ValidationError, match="bare origin"):
        _settings(frontend_origin=origin)


def test_sliding_ttl_longer_than_absolute_refused() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        _settings(session_sliding_ttl_days=31)


def test_secrets_not_in_repr() -> None:
    rendered = repr(_settings())
    assert _DB_PASSWORD not in rendered
    assert "x" * 32 not in rendered
