"""Embed active jobs whose vector is missing or stale (blueprint 6.4: `cli embed`)."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from app.core.logging import get_logger
from app.db.session import Database
from app.repositories import job_sync as repo

DEFAULT_BATCH_SIZE: Final = 64
# Bounds the loop if rows keep reappearing (content changing faster than it is embedded).
_MAX_BATCHES: Final = 10_000

_log = get_logger(__name__)


class PassageEmbedder(Protocol):
    @property
    def model_version(self) -> str: ...

    async def embed_passages(self, texts: Sequence[str]) -> list[NDArray[np.float32]]: ...


@dataclass(frozen=True, slots=True)
class EmbedResult:
    embedded: int
    reembedded: int
    batches: int


def utc_now() -> datetime:
    return datetime.now(UTC)


class EmbedService:
    def __init__(
        self,
        database: Database,
        embedder: PassageEmbedder,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._embedder = embedder
        self._batch_size = batch_size
        self._clock = clock

    async def run(self) -> EmbedResult:
        started = self._clock()
        model = self._embedder.model_version
        embedded = reembedded = batches = 0
        while batches < _MAX_BATCHES:
            async with self._database.session() as session:
                todo = await repo.jobs_needing_embedding(
                    session, model_version=model, limit=self._batch_size
                )
            if not todo:
                break
            # Embedding runs outside any transaction; the write records the content_hash that
            # was read, so a job edited meanwhile is simply picked up again next batch.
            vectors = await self._embedder.embed_passages([c.text for c in todo])
            async with self._database.session() as session, session.begin():
                await repo.upsert_embeddings(
                    session,
                    model_version=model,
                    rows=[
                        (c.job_id, v, c.content_hash) for c, v in zip(todo, vectors, strict=True)
                    ],
                    now=self._clock(),
                )
            reembedded += sum(1 for c in todo if c.has_vector)
            embedded += sum(1 for c in todo if not c.has_vector)
            batches += 1
            if len(todo) < self._batch_size:
                break
        async with self._database.session() as session, session.begin():
            await repo.record_run(
                session,
                source="embed",
                started_at=started,
                finished_at=self._clock(),
                fetched=embedded + reembedded,
                inserted=embedded,
                updated=reembedded,
                notes=json.dumps({"model_version": model, "batches": batches}),
            )
        _log.info("embed_finished", model=model, embedded=embedded, reembedded=reembedded)
        return EmbedResult(embedded, reembedded, batches)
