from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient, Response

from app.main import create_app
from tests.conftest import make_settings, serve

pytestmark = pytest.mark.integration

_SECRET = "t" * 32
_PASSWORD = "correct horse battery staple"


def _email() -> str:
    return f"{uuid.uuid4().hex[:12]}@example.com"


async def _signup(client: AsyncClient, client_ip: str | None = None) -> Response:
    headers = {"X-Proxy-Secret": _SECRET}
    if client_ip is not None:
        headers["X-Client-IP"] = client_ip
    response = await client.post(
        "/v1/auth/signup", json={"email": _email(), "password": _PASSWORD}, headers=headers
    )
    client.cookies.clear()
    return response


async def test_signup_is_limited_per_client_ip(migrated_database_url: str) -> None:
    settings = make_settings(migrated_database_url, rate_limit_signup_per_ip_per_hour=2)
    async with serve(create_app(settings)) as client:
        responses = [await _signup(client) for _ in range(3)]
    assert [r.status_code for r in responses] == [201, 201, 429]
    refused = responses[-1]
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 3600


async def test_forwarded_client_ip_separates_callers(migrated_database_url: str) -> None:
    settings = make_settings(migrated_database_url, rate_limit_signup_per_ip_per_hour=1)
    async with serve(create_app(settings)) as client:
        first = await _signup(client, "198.51.100.1")
        other = await _signup(client, "198.51.100.2")
        repeat = await _signup(client, "198.51.100.1")
    assert (first.status_code, other.status_code, repeat.status_code) == (201, 201, 429)


async def test_malformed_client_ip_falls_back_to_the_peer(migrated_database_url: str) -> None:
    settings = make_settings(migrated_database_url, rate_limit_signup_per_ip_per_hour=1)
    async with serve(create_app(settings)) as client:
        first = await _signup(client, "not-an-ip")
        second = await _signup(client, "also; not an ip")
    assert (first.status_code, second.status_code) == (201, 429)


async def test_login_is_limited_per_email_across_ips(migrated_database_url: str) -> None:
    settings = make_settings(migrated_database_url, rate_limit_login_per_email_per_15_min=2)
    email = _email()
    async with serve(create_app(settings)) as client:
        client.headers["X-Proxy-Secret"] = _SECRET
        created = await client.post("/v1/auth/signup", json={"email": email, "password": _PASSWORD})
        assert created.status_code == 201
        client.cookies.clear()
        codes = []
        for octet in (1, 2, 3):
            attempt = await client.post(
                "/v1/auth/login",
                json={"email": email, "password": "wrong-password-here"},
                headers={"X-Client-IP": f"203.0.113.{octet}"},
            )
            codes.append(attempt.status_code)
        other_account = await client.post(
            "/v1/auth/login", json={"email": _email(), "password": "wrong-password-here"}
        )
    assert codes == [401, 401, 429]
    assert other_account.status_code == 401
