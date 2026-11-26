from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import errors, sql
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.models import Base

pytestmark = pytest.mark.integration

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_MODEL_TABLES = sorted(Base.metadata.tables)
_NAMED_OBJECT = re.compile(r"(?:CONSTRAINT|INDEX) (\w+)")


def _sync_url(async_url: str) -> str:
    return async_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _alembic_config(async_url: str) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", async_url)
    return config


@pytest.fixture
def fresh_database(postgres_url: str) -> Iterator[str]:
    """An empty database in the shared container; yields its async URL."""
    name = f"careeros_mig_{uuid.uuid4().hex[:10]}"
    admin_url = _sync_url(postgres_url)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield postgres_url.rsplit("/", 1)[0] + f"/{name}"
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def migrated(fresh_database: str) -> Iterator[psycopg.Connection]:
    command.upgrade(_alembic_config(fresh_database), "head")
    with psycopg.connect(_sync_url(fresh_database), autocommit=True) as conn:
        yield conn


def _model_object_names() -> set[str]:
    # create_engine is typed and never connects here; it only supplies the dialect.
    dialect = create_engine("postgresql+psycopg://").dialect
    names: set[str] = set()
    for table in Base.metadata.sorted_tables:
        names.update(_NAMED_OBJECT.findall(str(CreateTable(table).compile(dialect=dialect))))
        for index in table.indexes:
            names.update(_NAMED_OBJECT.findall(str(CreateIndex(index).compile(dialect=dialect))))
    return names


def _public_tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    ).fetchall()
    return {row[0] for row in rows}


def _insert_user(conn: psycopg.Connection, email: str | None = None) -> uuid.UUID:
    row = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (%s, 'hash') RETURNING id",
        (email or f"{uuid.uuid4().hex[:12]}@example.com",),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def _insert_job(conn: psycopg.Connection, **overrides: object) -> uuid.UUID:
    values: dict[str, object] = {
        "source": "test",
        "source_board": "test-board",
        "external_id": uuid.uuid4().hex,
        "title": "Backend Engineer",
        "description": "Build APIs",
        "content_hash": b"\x01" * 32,
        "canonical_hash": b"\x02" * 32,
        **overrides,
    }
    statement = sql.SQL("INSERT INTO jobs ({}) VALUES ({}) RETURNING id").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in values),
        sql.SQL(", ").join(sql.Placeholder() for _ in values),
    )
    row = conn.execute(statement, tuple(values.values())).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def test_every_model_table_exists(migrated: psycopg.Connection) -> None:
    assert _public_tables(migrated) == {*_MODEL_TABLES, "alembic_version"}


def test_every_model_constraint_and_index_exists_by_name(migrated: psycopg.Connection) -> None:
    constraints = migrated.execute(
        "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "WHERE t.relname = ANY(%s) AND c.contype IN ('c', 'f', 'p', 'u')",
        (_MODEL_TABLES,),
    ).fetchall()
    indexes = migrated.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (_MODEL_TABLES,),
    ).fetchall()
    in_database = {row[0] for row in constraints} | {row[0] for row in indexes}
    assert in_database == _model_object_names()


@pytest.mark.parametrize("column", ["work_mode", "employment_type", "seniority", "status"])
def test_job_enum_columns_reject_unknown_values(migrated: psycopg.Connection, column: str) -> None:
    with pytest.raises(errors.CheckViolation, match=f"ck_jobs_{column}"):
        _insert_job(migrated, **{column: "teleport"})


def test_job_enum_columns_accept_known_values(migrated: psycopg.Connection) -> None:
    _insert_job(migrated, work_mode="remote", employment_type="internship", seniority="intern")


def test_hash_must_be_exactly_32_bytes(migrated: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation, match="ck_jobs_content_hash_length"):
        _insert_job(migrated, content_hash=b"\x01" * 31)


def test_only_one_active_single_valued_preference(migrated: psycopg.Connection) -> None:
    user_id = _insert_user(migrated)
    insert = (
        "INSERT INTO user_preferences (user_id, dimension, operator, value, strength, source) "
        "VALUES (%s, %s, 'include', %s, 'hard', 'chat')"
    )
    migrated.execute(insert, (user_id, "location", '"Pune"'))
    migrated.execute(insert, (user_id, "location", '"Remote"'))
    migrated.execute(insert, (user_id, "seniority", '"junior"'))
    with pytest.raises(errors.UniqueViolation, match="uq_user_preferences_active_single_valued"):
        migrated.execute(insert, (user_id, "seniority", '"mid"'))
    migrated.execute(
        "UPDATE user_preferences SET superseded_at = now() "
        "WHERE user_id = %s AND dimension = 'seniority'",
        (user_id,),
    )
    migrated.execute(insert, (user_id, "seniority", '"mid"'))


def test_invalid_tool_args_cannot_be_recorded_as_ok(migrated: psycopg.Connection) -> None:
    user_id = _insert_user(migrated)
    conversation = migrated.execute(
        "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (user_id,)
    ).fetchone()
    assert conversation is not None
    message = migrated.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (%s, 'user', 'hi') "
        "RETURNING id",
        (conversation[0],),
    ).fetchone()
    assert message is not None
    run = migrated.execute(
        "INSERT INTO agent_runs (message_id, model, steps, status) "
        "VALUES (%s, 'm', 1, 'failed') RETURNING id",
        (message[0],),
    ).fetchone()
    assert run is not None
    with pytest.raises(errors.CheckViolation, match="ck_tool_calls_invalid_args_rejected"):
        migrated.execute(
            "INSERT INTO tool_calls (agent_run_id, tool_name, args, args_valid, status) "
            "VALUES (%s, 'search_jobs', '{}', false, 'ok')",
            (run[0],),
        )


def test_updated_at_trigger_bumps_on_update(migrated: psycopg.Connection) -> None:
    user_id = _insert_user(migrated)
    migrated.execute("SELECT pg_sleep(0.01)")
    migrated.execute("UPDATE users SET password_hash = 'changed' WHERE id = %s", (user_id,))
    row = migrated.execute(
        "SELECT updated_at > created_at FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    assert row == (True,)


def test_deleting_a_user_removes_all_of_their_data(migrated: psycopg.Connection) -> None:
    user_id = _insert_user(migrated)
    other_user_id = _insert_user(migrated)
    job_id = _insert_job(migrated)
    migrated.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) "
        "VALUES (%s, %s, now() + interval '1 day')",
        (user_id, b"\x03" * 32),
    )
    migrated.execute(
        "INSERT INTO user_preferences (user_id, dimension, operator, value, strength, source) "
        "VALUES (%s, 'location', 'include', '\"Pune\"', 'soft', 'ui')",
        (user_id,),
    )
    migrated.execute("INSERT INTO conversations (user_id) VALUES (%s)", (user_id,))
    migrated.execute("INSERT INTO saved_jobs (user_id, job_id) VALUES (%s, %s)", (user_id, job_id))
    migrated.execute(
        "INSERT INTO saved_jobs (user_id, job_id) VALUES (%s, %s)", (other_user_id, job_id)
    )
    migrated.execute("DELETE FROM users WHERE id = %s", (user_id,))
    for table in ("sessions", "user_preferences", "conversations", "saved_jobs"):
        count = migrated.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE user_id = %s").format(sql.Identifier(table)),
            (user_id,),
        ).fetchone()
        assert count == (0,), table
    other = migrated.execute(
        "SELECT count(*) FROM saved_jobs WHERE user_id = %s", (other_user_id,)
    ).fetchone()
    assert other == (1,)


def test_search_tsv_is_generated_from_the_title(migrated: psycopg.Connection) -> None:
    job_id = _insert_job(migrated, title="Kubernetes Platform Engineer")
    row = migrated.execute(
        "SELECT search_tsv @@ to_tsquery('english', 'kubernetes') FROM jobs WHERE id = %s",
        (job_id,),
    ).fetchone()
    assert row == (True,)


def test_upgrade_downgrade_upgrade_round_trip(fresh_database: str) -> None:
    config = _alembic_config(fresh_database)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    with psycopg.connect(_sync_url(fresh_database), autocommit=True) as conn:
        assert _public_tables(conn) == {"alembic_version"}
    command.upgrade(config, "head")
    with psycopg.connect(_sync_url(fresh_database), autocommit=True) as conn:
        assert _public_tables(conn) == {*_MODEL_TABLES, "alembic_version"}


def test_models_and_migrations_agree(fresh_database: str) -> None:
    config = _alembic_config(fresh_database)
    command.upgrade(config, "head")
    command.check(config)


def test_active_job_cannot_carry_expiry_fields(migrated: psycopg.Connection) -> None:
    job_id = _insert_job(migrated)
    with pytest.raises(errors.CheckViolation, match="ck_jobs_expired_fields_iff_expired"):
        migrated.execute(
            "UPDATE jobs SET expired_at = now(), expired_reason = 'takedown' WHERE id = %s",
            (job_id,),
        )


def test_expired_job_must_say_when_and_why(migrated: psycopg.Connection) -> None:
    job_id = _insert_job(migrated)
    with pytest.raises(errors.CheckViolation, match="ck_jobs_expired_fields_iff_expired"):
        migrated.execute("UPDATE jobs SET status = 'expired' WHERE id = %s", (job_id,))
    with pytest.raises(
        errors.CheckViolation, match=r"ck_jobs_expired_(fields_iff_expired|at_with_reason)"
    ):
        migrated.execute(
            "UPDATE jobs SET status = 'expired', expired_at = now() WHERE id = %s", (job_id,)
        )


def test_expire_and_reactivate_round_trip(migrated: psycopg.Connection) -> None:
    job_id = _insert_job(migrated)
    migrated.execute(
        "UPDATE jobs SET status = 'expired', expired_at = now(), "
        "expired_reason = 'missing_from_source' WHERE id = %s",
        (job_id,),
    )
    migrated.execute(
        "UPDATE jobs SET status = 'active', expired_at = NULL, expired_reason = NULL WHERE id = %s",
        (job_id,),
    )
    row = migrated.execute(
        "SELECT status, last_verified_at IS NOT NULL FROM jobs WHERE id = %s", (job_id,)
    ).fetchone()
    assert row == ("active", True)


def test_unknown_expiry_reason_is_rejected(migrated: psycopg.Connection) -> None:
    job_id = _insert_job(migrated)
    with pytest.raises(errors.CheckViolation, match="ck_jobs_expired_reason"):
        migrated.execute(
            "UPDATE jobs SET status = 'expired', expired_at = now(), expired_reason = 'bored' "
            "WHERE id = %s",
            (job_id,),
        )


def test_blank_source_board_is_rejected(migrated: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation, match="ck_jobs_source_board_not_blank"):
        _insert_job(migrated, source_board="  ")


def test_ingestion_run_counts_cannot_go_negative(migrated: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation, match="ck_ingestion_runs_counts_non_negative"):
        migrated.execute("INSERT INTO ingestion_runs (source, purged) VALUES ('purge', -1)")
