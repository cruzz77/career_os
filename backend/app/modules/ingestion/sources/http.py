"""Polite HTTP for job sources (blueprint 6.4.1): per-host spacing, bounded retries, contact UA."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger

_log = get_logger(__name__)

# Blueprint 6.4.1: at most one request per second per host.
MIN_INTERVAL_S: Final = 1.0

Sleep = Callable[[float], Awaitable[None]]


class SourceUnavailableError(Exception):
    """Retries exhausted. Callers treat it as "no answer", never as "posting gone"."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


def user_agent_for(contact: str) -> str:
    contact = contact.strip()
    if "@" not in contact or any(char.isspace() for char in contact):
        raise ValueError("the source contact must be an email address")
    return f"CareerOS/1.0 (non-commercial portfolio project; contact: {contact})"


def build_http_client(
    user_agent: str,
    *,
    timeout_s: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    # No redirects: a moved board must surface as a non-200, not silently become another URL.
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=False,
        transport=transport,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


class PoliteHttpClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        min_interval_s: float = MIN_INTERVAL_S,
        max_attempts: int = 4,
        base_backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        if min_interval_s < MIN_INTERVAL_S:
            raise ValueError(f"min_interval_s must be at least {MIN_INTERVAL_S}")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._client = client
        self._min_interval_s = min_interval_s
        self._max_attempts = max_attempts
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_start: dict[str, float] = {}

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        max_attempts: int | None = None,
    ) -> httpx.Response:
        """Any non-retryable response is returned as is, including 404; the caller decides."""
        attempts = self._max_attempts if max_attempts is None else max(1, max_attempts)
        reason = "no attempt made"
        for attempt in range(1, attempts + 1):
            await self._wait_turn(url)
            try:
                response = await self._client.get(url, params=params)
            except httpx.TransportError as exc:
                reason = type(exc).__name__
                delay = self._backoff(attempt)
            else:
                if not _is_retryable(response.status_code):
                    return response
                reason = f"HTTP {response.status_code}"
                retry_after = self._retry_after(response)
                delay = self._backoff(attempt) if retry_after is None else retry_after
            if attempt < attempts:
                _log.warning("source_retry", url=url, attempt=attempt, reason=reason, delay_s=delay)
                await self._sleep(delay)
        raise SourceUnavailableError(url, reason)

    async def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        # Spacing is by request start; holding the lock only while waiting lets concurrent
        # callers queue in order without serialising whole requests.
        async with lock:
            last = self._last_start.get(host)
            if last is not None:
                wait = last + self._min_interval_s - self._monotonic()
                if wait > 0:
                    await self._sleep(wait)
            self._last_start[host] = self._monotonic()

    def _backoff(self, attempt: int) -> float:
        return float(min(self._base_backoff_s * 2 ** (attempt - 1), self._max_backoff_s))

    def _retry_after(self, response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value is None:
            return None
        value = value.strip()
        if value.isdigit():
            seconds = float(value)
        else:
            try:
                when = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            seconds = (when - self._now()).total_seconds()
        return min(max(seconds, 0.0), self._max_backoff_s)
