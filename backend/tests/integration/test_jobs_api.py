from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from app.db.models import Job
from app.db.session import Database
from app.domain.enums import PostingStatus
from app.main import create_app
from app.modules.jobs.freshness import PostingChecker
from tests.conftest import make_settings, serve
from tests.fakes import FakeChecker

pytestmark = pytest.mark.integration

_SECRET = "t" * 32
_PASSWORD = "correct horse battery staple"


@asynccontextmanager
async def _signed_in_client(
    database_url: str, checkers: Mapping[str, PostingChecker] | None = None
) -> AsyncIterator[AsyncClient]:
    app = create_app(make_settings(database_url), posting_checkers=checkers)
    async with serve(app) as client:
        client.headers["X-Proxy-Secret"] = _SECRET
        email = f"{uuid.uuid4().hex[:12]}@example.com"
        signup = await client.post("/v1/auth/signup", json={"email": email, "password": _PASSWORD})
        assert signup.status_code == 201
        yield client


async def _insert_job(
    db: Database, *, verified_ago: timedelta = timedelta(hours=1), expired: bool = False
) -> uuid.UUID:
    now = datetime.now(UTC)
    job = Job(
        source="lever",
        source_board="acme",
        external_id=uuid.uuid4().hex,
        title="Backend Engineer",
        description="Build APIs",
        content_hash=b"\x01" * 32,
        canonical_hash=b"\x02" * 32,
        last_verified_at=now - verified_ago,
    )
    if expired:
        job.status = "expired"
        job.expired_at = now - timedelta(days=1)
        job.expired_reason = "missing_from_source"
    async with db.session() as session, session.begin():
        session.add(job)
        await session.flush()
        return job.id


async def test_job_detail_requires_a_session(
    migrated_database_url: str, app_database: Database
) -> None:
    job_id = await _insert_job(app_database)
    async with serve(create_app(make_settings(migrated_database_url))) as client:
        response = await client.get(f"/v1/jobs/{job_id}", headers={"X-Proxy-Secret": _SECRET})
    assert response.status_code == 401


async def test_unknown_job_is_404(migrated_database_url: str) -> None:
    async with _signed_in_client(migrated_database_url) as client:
        response = await client.get(f"/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_malformed_job_id_is_422(migrated_database_url: str) -> None:
    async with _signed_in_client(migrated_database_url) as client:
        response = await client.get("/v1/jobs/not-a-uuid")
    assert response.status_code == 422


async def test_stale_job_confirmed_by_source_is_open(
    migrated_database_url: str, app_database: Database
) -> None:
    checker = FakeChecker(status=PostingStatus.OPEN)
    job_id = await _insert_job(app_database)
    async with _signed_in_client(migrated_database_url, {"lever": checker}) as client:
        response = await client.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["availability"]) == ("active", "open")
    assert body["last_verified_at"] is not None
    assert checker.calls == [("acme", checker.calls[0][1])]


async def test_expired_job_is_returned_as_closed_without_a_call(
    migrated_database_url: str, app_database: Database
) -> None:
    checker = FakeChecker()
    job_id = await _insert_job(app_database, expired=True)
    async with _signed_in_client(migrated_database_url, {"lever": checker}) as client:
        response = await client.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["availability"]) == ("expired", "closed")
    assert body["expired_at"] is not None
    assert checker.calls == []


async def test_source_not_found_closes_the_job(
    migrated_database_url: str, app_database: Database
) -> None:
    checker = FakeChecker(status=PostingStatus.NOT_FOUND)
    job_id = await _insert_job(app_database)
    async with _signed_in_client(migrated_database_url, {"lever": checker}) as client:
        response = await client.get(f"/v1/jobs/{job_id}")
    body = response.json()
    assert (body["status"], body["availability"]) == ("expired", "closed")


async def test_without_a_registered_source_stale_jobs_are_unverified(
    migrated_database_url: str, app_database: Database
) -> None:
    job_id = await _insert_job(app_database)
    async with _signed_in_client(migrated_database_url) as client:
        response = await client.get(f"/v1/jobs/{job_id}")
    body = response.json()
    assert (body["status"], body["availability"]) == ("active", "unverified")
