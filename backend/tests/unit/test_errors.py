from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from app.core.errors import (
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
    register_exception_handlers,
)
from app.core.logging import REQUEST_ID_HEADER, RequestContextMiddleware

_SUBMITTED_PASSWORD = "short-pass"


class Credentials(BaseModel):
    email: str
    password: str = Field(min_length=12)


def require_positive(n: int) -> int:
    if n <= 0:
        raise ValidationFailedError("n must be positive", details={"field": "n"})
    return n


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    @app.get("/thing")
    async def thing() -> None:
        raise NotFoundError("Job not found")

    @app.get("/limited")
    async def limited() -> None:
        raise RateLimitedError(retry_after_s=7)

    @app.post("/login")
    async def login(body: Credentials) -> dict[str, str]:
        return {"email": body.email}

    @app.get("/positive")
    async def positive(n: Annotated[int, Depends(require_positive)]) -> dict[str, int]:
        return {"n": n}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: Starlette re-raises after sending the 500, and the test needs
    # the response the client would actually receive.
    transport = ASGITransport(app=_build_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_app_error_renders_full_envelope(client: AsyncClient) -> None:
    response = await client.get("/thing")
    assert response.status_code == 404
    assert response.headers.get_list(REQUEST_ID_HEADER) == [response.headers[REQUEST_ID_HEADER]]
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Job not found",
            "details": None,
            "request_id": response.headers[REQUEST_ID_HEADER],
        }
    }


async def test_rate_limited_sets_retry_after(client: AsyncClient) -> None:
    response = await client.get("/limited")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.json()["error"]["code"] == "rate_limited"


async def test_unknown_route_uses_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_wrong_method_keeps_allow_header(client: AsyncClient) -> None:
    response = await client.post("/thing")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["Allow"]


async def test_body_validation_never_echoes_input(client: AsyncClient) -> None:
    response = await client.post(
        "/login", json={"email": "a@example.com", "password": _SUBMITTED_PASSWORD}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert [d["loc"] for d in error["details"]] == [["body", "password"]]
    assert _SUBMITTED_PASSWORD not in response.text


async def test_validation_raised_in_dependency_is_422_not_500(client: AsyncClient) -> None:
    response = await client.get("/positive", params={"n": 0})
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"field": "n"}


async def test_framework_validation_of_dependency_param_is_422(client: AsyncClient) -> None:
    response = await client.get("/positive", params={"n": "abc"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unhandled_error_is_generic_500_with_request_id(client: AsyncClient) -> None:
    response = await client.get("/boom")
    assert response.status_code == 500
    request_ids = response.headers.get_list(REQUEST_ID_HEADER)
    assert len(request_ids) == 1
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == request_ids[0]
    assert "secret internal detail" not in response.text
