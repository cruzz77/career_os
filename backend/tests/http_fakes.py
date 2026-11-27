"""Scripted HTTP and a fake clock for source-adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx

from app.modules.ingestion.sources.http import PoliteHttpClient, build_http_client, user_agent_for

WALL = datetime(2026, 11, 27, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Script:
    """Answers requests in order with the given responses or transport errors."""

    def __init__(self, *steps: httpx.Response | Exception) -> None:
        self._steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@asynccontextmanager
async def polite_client(
    script: Script, fake: FakeTime, **kwargs: Any
) -> AsyncIterator[PoliteHttpClient]:
    raw = build_http_client(
        user_agent_for("dev@example.com"), timeout_s=5.0, transport=httpx.MockTransport(script)
    )
    async with raw:
        yield PoliteHttpClient(
            raw, sleep=fake.sleep, monotonic=fake.monotonic, now=lambda: WALL, **kwargs
        )
