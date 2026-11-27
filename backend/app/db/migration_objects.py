"""Schema objects that exist only in migrations, never in the SQLAlchemy models.

The ParadeDB BM25 index (0002_bm25_index) uses an access method and tokenizer casts that models
cannot declare. Alembic's env.py excludes these from `alembic check`, and the schema tests
expect exactly the models plus these, so any other drift is still caught.
"""

from __future__ import annotations

from typing import Final

MIGRATION_ONLY_INDEXES: Final[frozenset[str]] = frozenset({"ix_jobs_bm25"})
