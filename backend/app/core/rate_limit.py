"""Request rate limiting. In-process: the API runs as one container with one uvicorn worker, so
counts are exact. The RateLimiter protocol lets a Redis implementation replace it when scaling
out to several workers."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from app.core.config import Settings

_FIFTEEN_MINUTES: Final = 15 * 60
_ONE_HOUR: Final = 60 * 60


@dataclass(frozen=True, slots=True)
class RateLimit:
    name: str
    max_requests: int
    window_s: float


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after_s: int


class RateLimiter(Protocol):
    async def hit(self, subject: str, limit: RateLimit) -> RateDecision: ...


@dataclass(frozen=True, slots=True)
class RateLimits:
    signup_per_ip: RateLimit
    login_per_ip: RateLimit
    login_per_email: RateLimit

    @classmethod
    def from_settings(cls, settings: Settings) -> RateLimits:
        return cls(
            signup_per_ip=RateLimit(
                "signup_per_ip", settings.rate_limit_signup_per_ip_per_hour, _ONE_HOUR
            ),
            login_per_ip=RateLimit(
                "login_per_ip", settings.rate_limit_login_per_ip_per_15_min, _FIFTEEN_MINUTES
            ),
            login_per_email=RateLimit(
                "login_per_email",
                settings.rate_limit_login_per_email_per_15_min,
                _FIFTEEN_MINUTES,
            ),
        )


class InMemoryRateLimiter:
    """Sliding log per key: the timestamps of the last max_requests hits, nothing older than
    the window. hit() never awaits, so two concurrent requests cannot interleave inside it."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, sweep_every: int = 1000
    ) -> None:
        self._clock = clock
        self._sweep_every = sweep_every
        self._calls = 0
        self._log: dict[str, tuple[float, deque[float]]] = {}

    async def hit(self, subject: str, limit: RateLimit) -> RateDecision:
        now = self._clock()
        self._calls += 1
        if self._calls % self._sweep_every == 0:
            self._sweep(now)
        key = f"{limit.name}:{subject}"
        entry = self._log.get(key)
        if entry is None:
            timestamps: deque[float] = deque(maxlen=limit.max_requests)
            self._log[key] = (limit.window_s, timestamps)
        else:
            timestamps = entry[1]
        cutoff = now - limit.window_s
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if len(timestamps) >= limit.max_requests:
            retry_after = math.ceil(timestamps[0] + limit.window_s - now)
            return RateDecision(allowed=False, retry_after_s=max(1, retry_after))
        timestamps.append(now)
        return RateDecision(allowed=True, retry_after_s=0)

    def tracked_key_count(self) -> int:
        return len(self._log)

    def _sweep(self, now: float) -> None:
        # Without this, every IP that ever called would keep an entry for the process lifetime.
        stale = [
            key
            for key, (window_s, timestamps) in self._log.items()
            if not timestamps or timestamps[-1] <= now - window_s
        ]
        for key in stale:
            del self._log[key]
