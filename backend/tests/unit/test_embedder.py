from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from app.modules.retrieval.embedder import (
    EmbedderNotLoadedError,
    EmbedderReadiness,
    FastEmbedder,
    Vector,
    as_vector,
)


class _StubEmbedder:
    def __init__(self, *, loaded: bool) -> None:
        self._loaded = loaded

    @property
    def model_version(self) -> str:
        return "stub"

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def embed_query(self, text: str) -> Vector:
        return np.zeros(384, dtype=np.float32)

    async def embed_passages(self, texts: Sequence[str]) -> list[Vector]:
        return [np.zeros(384, dtype=np.float32) for _ in texts]


@pytest.mark.parametrize("loaded", [False, True])
async def test_readiness_follows_the_model_load(loaded: bool) -> None:
    check = EmbedderReadiness(_StubEmbedder(loaded=loaded))
    assert check.name == "embedder"
    assert await check.is_ready() is loaded


def test_as_vector_converts_to_float32() -> None:
    vector = as_vector(np.ones(384, dtype=np.float64))
    assert vector.dtype == np.float32
    assert vector.shape == (384,)


def test_as_vector_rejects_the_wrong_dimension() -> None:
    with pytest.raises(ValueError, match="384-d"):
        as_vector(np.ones(768, dtype=np.float32))


async def test_embedding_before_load_is_refused() -> None:
    embedder = FastEmbedder("BAAI/bge-small-en-v1.5")
    assert not embedder.loaded
    with pytest.raises(EmbedderNotLoadedError):
        await embedder.embed_query("python developer")
    assert await embedder.embed_passages([]) == []


def _cosine(a: Vector, b: Vector) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


@pytest.mark.external
async def test_real_model_loads_and_ranks_related_text_higher() -> None:
    """Downloads the model on first run (about 67 MB); run with -m external."""
    embedder = FastEmbedder("BAAI/bge-small-en-v1.5")
    await embedder.load()
    assert embedder.loaded
    query = await embedder.embed_query("python backend developer")
    related, unrelated = await embedder.embed_passages(
        ["Backend engineer building APIs in Python and PostgreSQL", "Pastry chef for a bakery"]
    )
    as_passage = (await embedder.embed_passages(["python backend developer"]))[0]
    assert _cosine(query, related) > _cosine(query, unrelated) + 0.1
    # The query prefix must change the vector; identical vectors would mean it is not applied.
    assert not np.allclose(query, as_passage)
