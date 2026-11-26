from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.logging import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    configure_logging,
    get_logger,
    get_request_id,
)


async def _echo(request: Request) -> JSONResponse:
    get_logger("test").info("inside_handler")
    return JSONResponse({"context_id": get_request_id(), "state_id": request.state.request_id})


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = Starlette(
        routes=[Route("/echo", _echo), Route("/health", _echo)],
        middleware=[Middleware(RequestContextMiddleware)],
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _log_lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


async def test_generates_request_id_when_none_sent(client: AsyncClient) -> None:
    response = await client.get("/echo")
    request_id = response.headers[REQUEST_ID_HEADER]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert response.json() == {"context_id": request_id, "state_id": request_id}


async def test_keeps_well_formed_inbound_request_id(client: AsyncClient) -> None:
    response = await client.get("/echo", headers={REQUEST_ID_HEADER: "vercel-abc.123_XYZ"})
    assert response.headers[REQUEST_ID_HEADER] == "vercel-abc.123_XYZ"
    assert response.json()["context_id"] == "vercel-abc.123_XYZ"


@pytest.mark.parametrize("bad", ["short", "a" * 65, "has spaces in it", "key=value;x=1"])
async def test_replaces_malformed_inbound_request_id(client: AsyncClient, bad: str) -> None:
    response = await client.get("/echo", headers={REQUEST_ID_HEADER: bad})
    assert response.headers[REQUEST_ID_HEADER] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


async def test_request_id_does_not_leak_past_the_request(client: AsyncClient) -> None:
    await client.get("/echo")
    assert get_request_id() is None


async def test_handler_and_access_logs_carry_the_request_id(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO", json_output=True)
    response = await client.get("/echo")
    request_id = response.headers[REQUEST_ID_HEADER]
    lines = _log_lines(capsys.readouterr().out)
    handler_line = next(line for line in lines if line["event"] == "inside_handler")
    access_line = next(line for line in lines if line["event"] == "request")
    assert handler_line["request_id"] == request_id
    assert access_line["request_id"] == request_id
    assert access_line["path"] == "/echo"
    assert access_line["status"] == 200
    assert access_line["level"] == "info"


async def test_health_access_log_is_below_info(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO", json_output=True)
    await client.get("/health")
    events = [line["event"] for line in _log_lines(capsys.readouterr().out)]
    assert "inside_handler" in events
    assert "request" not in events
