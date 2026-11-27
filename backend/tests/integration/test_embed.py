from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import pytest
from numpy.typing import NDArray
from sqlalchemy import func, select, update

from app.db.models import IngestionRun, Job, JobEmbedding
from app.db.session import Database
from app.modules.ingestion.embed import EmbedService

pytestmark = pytest.mark.integration


class FakeEmbedder:
    def __init__(self, model_version: str = "fake-v1") -> None:
        self._model_version = model_version
        self.calls: list[int] = []

    @property
    def model_version(self) -> str:
        return self._model_version

    async def embed_passages(self, texts: Sequence[str]) -> list[NDArray[np.float32]]:
        self.calls.append(len(texts))
        return [np.full(384, 0.1, dtype=np.float32) for _ in texts]


async def _insert_jobs(db: Database, active: int, expired: int = 0) -> None:
    async with db.session() as session, session.begin():
        for i in range(active + expired):
            job = Job(
                source="fake",
                source_board="b",
                external_id=f"e{i}",
                title=f"Engineer {i}",
                description="Build services",
                content_hash=bytes([i + 1]) * 32,
                canonical_hash=b"\x02" * 32,
            )
            if i >= active:
                job.status = "expired"
                job.expired_at = datetime(2026, 11, 27, tzinfo=UTC)
                job.expired_reason = "missing_from_source"
            session.add(job)


async def _count(db: Database, model_version: str = "fake-v1") -> int:
    async with db.session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(JobEmbedding)
            .where(JobEmbedding.model_version == model_version)
        )
    return int(total or 0)


async def test_embeds_active_jobs_only_and_records_a_run(fresh_db: Database) -> None:
    await _insert_jobs(fresh_db, active=2, expired=1)
    result = await EmbedService(fresh_db, FakeEmbedder()).run()
    assert (result.embedded, result.reembedded) == (2, 0)
    assert await _count(fresh_db) == 2
    async with fresh_db.session() as session:
        mismatched = await session.scalar(
            select(func.count())
            .select_from(JobEmbedding)
            .join(Job, Job.id == JobEmbedding.job_id)
            .where(JobEmbedding.content_hash != Job.content_hash)
        )
        runs = await session.scalar(
            select(func.count()).select_from(IngestionRun).where(IngestionRun.source == "embed")
        )
    assert mismatched == 0
    assert runs == 1


async def test_second_run_embeds_nothing(fresh_db: Database) -> None:
    await _insert_jobs(fresh_db, active=3)
    await EmbedService(fresh_db, FakeEmbedder()).run()
    again = await EmbedService(fresh_db, FakeEmbedder()).run()
    assert (again.embedded, again.reembedded, again.batches) == (0, 0, 0)


async def test_changed_content_is_reembedded(fresh_db: Database) -> None:
    await _insert_jobs(fresh_db, active=2)
    await EmbedService(fresh_db, FakeEmbedder()).run()
    async with fresh_db.session() as session, session.begin():
        await session.execute(
            update(Job).where(Job.external_id == "e0").values(content_hash=b"\x09" * 32)
        )
    result = await EmbedService(fresh_db, FakeEmbedder()).run()
    assert (result.embedded, result.reembedded) == (0, 1)
    assert await _count(fresh_db) == 2


async def test_a_new_model_version_embeds_into_its_own_rows(fresh_db: Database) -> None:
    await _insert_jobs(fresh_db, active=2)
    await EmbedService(fresh_db, FakeEmbedder("fake-v1")).run()
    result = await EmbedService(fresh_db, FakeEmbedder("fake-v2")).run()
    assert result.embedded == 2
    assert (await _count(fresh_db, "fake-v1"), await _count(fresh_db, "fake-v2")) == (2, 2)


async def test_batches_are_bounded(fresh_db: Database) -> None:
    await _insert_jobs(fresh_db, active=5)
    embedder = FakeEmbedder()
    result = await EmbedService(fresh_db, embedder, batch_size=2).run()
    assert result.embedded == 5
    assert embedder.calls == [2, 2, 1]
