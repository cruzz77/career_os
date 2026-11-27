"""ParadeDB BM25 index on jobs (blueprint 4, 5, 5.1).

Syntax checked against the pinned image (paradedb/paradedb:0.27.1-pg17) on 2026-11-27:
access methods `paradedb` and `bm25` (alias) exist; key_field is deprecated and ignored since
0.26; tokenizers are casts (pdb.simple, pdb.literal, ...); search is `|||` / `&&&` and ranking
is pdb.score(id). Title and description use English stemming; skills_text keeps the default
tokenizer (canonical names like "Node.js" must not be stemmed); filter columns are pdb.literal
so they index as one exact value each. status is indexed because every retriever filters on it.

The image installs pg_search itself; the extension is never dropped on downgrade.

Revision ID: 0002_bm25_index
Revises: 0001_core
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_bm25_index"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_INDEX = """
CREATE INDEX ix_jobs_bm25 ON jobs USING paradedb (
    id,
    (title::pdb.simple('stemmer=english')),
    skills_text,
    (description::pdb.simple('stemmer=english')),
    (status::pdb.literal),
    (work_mode::pdb.literal),
    (employment_type::pdb.literal),
    (seniority::pdb.literal),
    (location_city::pdb.literal),
    (location_country::pdb.literal)
)
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    op.execute(_CREATE_INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_bm25")
