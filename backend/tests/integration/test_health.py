from __future__ import annotations

import dataclasses

import pytest

from app.core.deps import AppState
from app.main import create_app
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings, serve

pytestmark = pytest.mark.integration


class _ExplodingCheck:
    @property
    def name(self) -> str:
        return "exploding"

    async def is_ready(self) -> bool:
        raise RuntimeError("probe crashed")


async def test_liveness_is_ok(postgres_url: str) -> None:
    async with serve(create_app(make_settings(postgres_url))) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_database_reachable(postgres_url: str) -> None:
    async with serve(create_app(make_settings(postgres_url))) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": True}}


async def test_not_ready_when_database_unreachable() -> None:
    async with serve(create_app(make_settings(UNREACHABLE_DATABASE_URL))) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "service_unavailable"
    assert error["details"] == {"checks": {"database": False}}


async def test_liveness_does_not_depend_on_database() -> None:
    async with serve(create_app(make_settings(UNREACHABLE_DATABASE_URL))) as client:
        response = await client.get("/health")
    assert response.status_code == 200


async def test_crashing_check_is_reported_as_not_ready(postgres_url: str) -> None:
    app = create_app(make_settings(postgres_url))
    async with serve(app) as client:
        state: AppState = app.state.services
        app.state.services = dataclasses.replace(
            state, readiness_checks=(*state.readiness_checks, _ExplodingCheck())
        )
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["details"] == {"checks": {"database": True, "exploding": False}}


async def test_no_registered_checks_is_not_ready(postgres_url: str) -> None:
    app = create_app(make_settings(postgres_url))
    async with serve(app) as client:
        app.state.services = dataclasses.replace(app.state.services, readiness_checks=())
        response = await client.get("/health/ready")
    assert response.status_code == 503


async def test_docs_enabled_outside_production(postgres_url: str) -> None:
    async with serve(create_app(make_settings(postgres_url))) as client:
        assert (await client.get("/openapi.json")).status_code == 200


async def test_docs_disabled_in_production(postgres_url: str) -> None:
    settings = make_settings(
        postgres_url,
        environment="production",
        frontend_origin="https://careeros.example.com",
        session_cookie_secure=True,
    )
    async with serve(create_app(settings)) as client:
        # With the proxy secret, so this proves the docs are disabled, not merely gated.
        client.headers["X-Proxy-Secret"] = "t" * 32
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


async def test_cors_allows_frontend_origin_without_credentials(postgres_url: str) -> None:
    async with serve(create_app(make_settings(postgres_url))) as client:
        response = await client.options(
            "/health",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in response.headers


async def test_cors_refuses_other_origins(postgres_url: str) -> None:
    async with serve(create_app(make_settings(postgres_url))) as client:
        response = await client.options(
            "/health",
            headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
