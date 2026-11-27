"""Text embeddings (blueprint 4: fastembed ONNX, BAAI/bge-small-en-v1.5, 384-d).

bge models are trained with an instruction prefix on short queries and none on passages. The
prefix is added here, explicitly: fastembed 0.9.1's query_embed returns exactly the passage
vector for bge-small (verified by tests/unit/test_embedder.py), so relying on it would silently
drop the instruction, and a future version adding it there would apply it twice. The model card
says the better setting is task-dependent, so query_instruction is a MatchBench knob.
ONNX inference is CPU-bound, so every call runs in a worker thread instead of the event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Final, Protocol

import numpy as np
from fastembed import TextEmbedding
from numpy.typing import NDArray

from app.core.logging import get_logger
from app.db.models.jobs import EMBEDDING_DIMENSIONS

Vector = NDArray[np.float32]

# From the BAAI/bge-small-en-v1.5 model card, trailing space included.
BGE_QUERY_INSTRUCTION: Final = "Represent this sentence for searching relevant passages: "

_log = get_logger(__name__)


class Embedder(Protocol):
    @property
    def model_version(self) -> str: ...

    @property
    def loaded(self) -> bool: ...

    async def embed_query(self, text: str) -> Vector: ...

    async def embed_passages(self, texts: Sequence[str]) -> list[Vector]: ...


class EmbedderNotLoadedError(RuntimeError):
    pass


def as_vector(raw: NDArray[Any]) -> Vector:
    vector = np.asarray(raw, dtype=np.float32)
    if vector.shape != (EMBEDDING_DIMENSIONS,):
        raise ValueError(f"expected a {EMBEDDING_DIMENSIONS}-d vector, got shape {vector.shape}")
    return vector


class FastEmbedder:
    def __init__(
        self,
        model_name: str,
        *,
        cache_dir: str | None = None,
        threads: int | None = None,
        batch_size: int = 32,
        query_instruction: str = BGE_QUERY_INSTRUCTION,
    ) -> None:
        self._model_name = model_name
        self._query_instruction = query_instruction
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = batch_size
        self._model: TextEmbedding | None = None

    @property
    def model_version(self) -> str:
        # Stored in job_embeddings.model_version: a model change re-embeds instead of mixing spaces.
        return self._model_name

    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        if self._model is not None:
            return
        model = await asyncio.to_thread(
            TextEmbedding,
            model_name=self._model_name,
            cache_dir=self._cache_dir,
            threads=self._threads,
        )
        # Prove the model produces the dimension the vector(384) column stores before reporting
        # ready; a wrong model would otherwise fail on the first insert, not at startup.
        probe = await asyncio.to_thread(lambda: list(model.embed(["probe"])))
        as_vector(probe[0])
        self._model = model
        _log.info("embedder_loaded", model=self._model_name, dimensions=EMBEDDING_DIMENSIONS)

    async def embed_query(self, text: str) -> Vector:
        model = self._require()
        prefixed = f"{self._query_instruction}{text}"
        vectors = await asyncio.to_thread(lambda: list(model.embed([prefixed])))
        return as_vector(vectors[0])

    async def embed_passages(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        model = self._require()
        batch = list(texts)
        vectors = await asyncio.to_thread(
            lambda: list(model.embed(batch, batch_size=self._batch_size))
        )
        return [as_vector(vector) for vector in vectors]

    def _require(self) -> TextEmbedding:
        if self._model is None:
            raise EmbedderNotLoadedError("the embedder has not finished loading")
        return self._model


class EmbedderReadiness:
    """/health/ready stays 503 until the model is loaded (Backend handoff 3.6)."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    @property
    def name(self) -> str:
        return "embedder"

    async def is_ready(self) -> bool:
        return self._embedder.loaded
