from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient, Response

from app.main import create_app
from tests.conftest import make_settings, serve

pytestmark = pytest.mark.integration

_SECRET = "t" * 32
_COOKIE = "careeros_session"
_PASSWORD = "correct horse battery staple"


def _email() -> str:
    return f"{uuid.uuid4().hex[:12]}@example.com"


def _session_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{_COOKIE}={token}"}


@pytest.fixture
async def client(migrated_database_url: str) -> AsyncIterator[AsyncClient]:
    async with serve(create_app(make_settings(migrated_database_url))) as c:
        c.headers["X-Proxy-Secret"] = _SECRET
        yield c


async def _token_from(client: AsyncClient, response: Response) -> str:
    token = response.cookies[_COOKIE]
    # Tests send the cookie explicitly, so the jar must not send it behind their back.
    client.cookies.clear()
    return token


async def _signup(client: AsyncClient, email: str) -> str:
    response = await client.post("/v1/auth/signup", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 201, response.text
    return await _token_from(client, response)


async def test_requests_without_the_proxy_secret_are_forbidden(
    migrated_database_url: str,
) -> None:
    async with serve(create_app(make_settings(migrated_database_url))) as bare:
        missing = await bare.get("/v1/me")
        wrong = await bare.get("/v1/me", headers={"X-Proxy-Secret": "w" * 32})
        health = await bare.get("/health")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "forbidden"
    assert missing.json()["error"]["request_id"] == missing.headers["X-Request-ID"]
    assert wrong.status_code == 403
    assert health.status_code == 200


async def test_signup_returns_user_and_sets_hardened_cookie(client: AsyncClient) -> None:
    email = _email()
    response = await client.post("/v1/auth/signup", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 201
    assert response.json()["email"] == email
    assert "password" not in response.text
    cookie = response.headers["set-cookie"].lower()
    for attribute in ("httponly", "samesite=lax", "path=/", "max-age=2592000"):
        assert attribute in cookie
    assert "secure" not in cookie


async def test_cookie_is_secure_when_configured(migrated_database_url: str) -> None:
    settings = make_settings(migrated_database_url, session_cookie_secure=True)
    async with serve(create_app(settings)) as secure_client:
        response = await secure_client.post(
            "/v1/auth/signup",
            json={"email": _email(), "password": _PASSWORD},
            headers={"X-Proxy-Secret": _SECRET},
        )
    assert response.status_code == 201
    assert "secure" in response.headers["set-cookie"].lower()


async def test_me_requires_a_session(client: AsyncClient) -> None:
    response = await client.get("/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_me_returns_the_signed_in_user(client: AsyncClient) -> None:
    email = _email()
    token = await _signup(client, email)
    response = await client.get("/v1/me", headers=_session_header(token))
    assert response.status_code == 200
    assert response.json()["email"] == email


async def test_tampered_session_cookie_is_rejected(client: AsyncClient) -> None:
    token = await _signup(client, _email())
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    response = await client.get("/v1/me", headers=_session_header(tampered))
    assert response.status_code == 401


async def test_duplicate_signup_is_409(client: AsyncClient) -> None:
    email = _email()
    await _signup(client, email)
    response = await client.post("/v1/auth/signup", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_short_password_is_422_and_never_echoed(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/signup", json={"email": _email(), "password": "tooshort123"}
    )
    assert response.status_code == 422
    assert "tooshort123" not in response.text


async def test_unknown_fields_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/signup",
        json={"email": _email(), "password": _PASSWORD, "is_admin": True},
    )
    assert response.status_code == 422


async def test_login_failures_are_indistinguishable(client: AsyncClient) -> None:
    email = _email()
    await _signup(client, email)
    wrong = await client.post("/v1/auth/login", json={"email": email, "password": "nope-nope-nope"})
    unknown = await client.post("/v1/auth/login", json={"email": _email(), "password": _PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert "set-cookie" not in wrong.headers


async def test_login_issues_a_working_session(client: AsyncClient) -> None:
    email = _email()
    await _signup(client, email)
    response = await client.post("/v1/auth/login", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 200
    token = await _token_from(client, response)
    assert (await client.get("/v1/me", headers=_session_header(token))).status_code == 200


async def test_logout_clears_cookie_and_revokes_session(client: AsyncClient) -> None:
    token = await _signup(client, _email())
    response = await client.post("/v1/auth/logout", headers=_session_header(token))
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    client.cookies.clear()
    assert (await client.get("/v1/me", headers=_session_header(token))).status_code == 401


async def test_logout_without_a_session_is_still_204(client: AsyncClient) -> None:
    assert (await client.post("/v1/auth/logout")).status_code == 204


async def test_delete_me_removes_the_account(client: AsyncClient) -> None:
    email = _email()
    token = await _signup(client, email)
    response = await client.delete("/v1/me", headers=_session_header(token))
    assert response.status_code == 204
    client.cookies.clear()
    assert (await client.get("/v1/me", headers=_session_header(token))).status_code == 401
    login = await client.post("/v1/auth/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 401
