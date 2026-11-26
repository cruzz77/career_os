from __future__ import annotations

from dataclasses import dataclass

from app.core.rate_limit import InMemoryRateLimiter, RateLimit

_LIMIT = RateLimit(name="test", max_requests=2, window_s=60)


@dataclass
class _Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


async def test_allows_up_to_the_limit_then_refuses_with_retry_after() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiter(clock=clock)
    assert (await limiter.hit("a", _LIMIT)).allowed
    clock.now = 10
    assert (await limiter.hit("a", _LIMIT)).allowed
    clock.now = 20
    refused = await limiter.hit("a", _LIMIT)
    assert not refused.allowed
    assert refused.retry_after_s == 40


async def test_window_slides_rather_than_resetting() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiter(clock=clock)
    await limiter.hit("a", _LIMIT)
    clock.now = 30
    await limiter.hit("a", _LIMIT)
    clock.now = 60.5
    assert (await limiter.hit("a", _LIMIT)).allowed
    assert not (await limiter.hit("a", _LIMIT)).allowed


async def test_subjects_and_limit_names_are_independent() -> None:
    limiter = InMemoryRateLimiter(clock=_Clock())
    other = RateLimit(name="other", max_requests=2, window_s=60)
    for _ in range(2):
        assert (await limiter.hit("a", _LIMIT)).allowed
    assert (await limiter.hit("b", _LIMIT)).allowed
    assert (await limiter.hit("a", other)).allowed
    assert not (await limiter.hit("a", _LIMIT)).allowed


async def test_idle_keys_are_swept() -> None:
    clock = _Clock()
    limiter = InMemoryRateLimiter(clock=clock, sweep_every=1)
    for subject in ("a", "b", "c"):
        await limiter.hit(subject, _LIMIT)
    assert limiter.tracked_key_count() == 3
    clock.now = 61
    await limiter.hit("d", _LIMIT)
    assert limiter.tracked_key_count() == 1
