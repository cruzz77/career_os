from __future__ import annotations

import httpx
import pytest

from app.modules.ingestion.sources.http import (
    PoliteHttpClient,
    SourceUnavailableError,
    build_http_client,
    user_agent_for,
)
from tests.http_fakes import FakeTime, Script, polite_client

_URL = "https://api.example.test/v0/postings/acme"


async def test_success_is_returned_with_the_contact_user_agent() -> None:
    script, fake = Script(httpx.Response(200, json=[])), FakeTime()
    async with polite_client(script, fake) as client:
        response = await client.get(_URL)
    assert response.status_code == 200
    assert "contact: dev@example.com" in script.requests[0].headers["user-agent"]
    assert fake.sleeps == []


async def test_not_found_is_returned_without_retrying() -> None:
    script, fake = Script(httpx.Response(404)), FakeTime()
    async with polite_client(script, fake) as client:
        assert (await client.get(_URL)).status_code == 404
    assert len(script.requests) == 1


async def test_429_waits_for_retry_after_seconds() -> None:
    script = Script(httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200))
    fake = FakeTime()
    async with polite_client(script, fake) as client:
        assert (await client.get(_URL)).status_code == 200
    assert fake.sleeps == [3.0]
    assert len(script.requests) == 2


async def test_5xx_backs_off_exponentially_then_gives_up() -> None:
    script, fake = Script(*(httpx.Response(503) for _ in range(4))), FakeTime()
    async with polite_client(script, fake) as client:
        with pytest.raises(SourceUnavailableError, match="HTTP 503"):
            await client.get(_URL)
    assert len(script.requests) == 4
    assert fake.sleeps == [1.0, 2.0, 4.0]


async def test_transport_error_is_retried() -> None:
    script = Script(httpx.ConnectError("refused"), httpx.Response(200))
    fake = FakeTime()
    async with polite_client(script, fake) as client:
        assert (await client.get(_URL)).status_code == 200
    assert fake.sleeps == [1.0]


async def test_single_attempt_mode_does_not_retry() -> None:
    script, fake = Script(httpx.Response(503)), FakeTime()
    async with polite_client(script, fake) as client:
        with pytest.raises(SourceUnavailableError):
            await client.get(_URL, max_attempts=1)
    assert len(script.requests) == 1
    assert fake.sleeps == []


async def test_same_host_is_spaced_and_other_hosts_are_not() -> None:
    script = Script(httpx.Response(200), httpx.Response(200), httpx.Response(200))
    fake = FakeTime()
    async with polite_client(script, fake) as client:
        await client.get(_URL)
        await client.get(_URL)
        await client.get("https://other.example.test/jobs")
    assert fake.sleeps == [1.0]


async def test_retry_after_http_date_is_honoured() -> None:
    later = "Fri, 27 Nov 2026 00:00:10 GMT"
    script = Script(httpx.Response(429, headers={"Retry-After": later}), httpx.Response(200))
    fake = FakeTime()
    async with polite_client(script, fake) as client:
        await client.get(_URL)
    assert fake.sleeps == [10.0]


async def test_retry_after_in_the_past_still_respects_host_spacing() -> None:
    past = "Thu, 26 Nov 2026 23:00:00 GMT"
    script = Script(httpx.Response(429, headers={"Retry-After": past}), httpx.Response(200))
    fake = FakeTime()
    async with polite_client(script, fake) as client:
        await client.get(_URL)
    assert fake.sleeps == [0.0, 1.0]


async def test_retry_after_is_capped() -> None:
    script = Script(httpx.Response(503, headers={"Retry-After": "99999"}), httpx.Response(200))
    fake = FakeTime()
    async with polite_client(script, fake) as client:
        await client.get(_URL)
    assert fake.sleeps == [60.0]


async def test_faster_than_one_request_per_second_is_refused() -> None:
    async with build_http_client("ua", timeout_s=1.0) as raw:
        with pytest.raises(ValueError, match=r"at least 1\.0"):
            PoliteHttpClient(raw, min_interval_s=0.5)


def test_user_agent_carries_the_contact() -> None:
    assert "contact: ops@example.com" in user_agent_for(" ops@example.com ")


@pytest.mark.parametrize("contact", ["", "no-at-sign", "two words@example.com"])
def test_user_agent_requires_an_email_contact(contact: str) -> None:
    with pytest.raises(ValueError, match="email"):
        user_agent_for(contact)
