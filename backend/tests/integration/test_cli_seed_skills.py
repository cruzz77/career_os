from __future__ import annotations

import psycopg
import pytest

from app.cli import main
from app.core.config import get_settings
from app.modules.ingestion.skills import SkillCatalogue

pytestmark = pytest.mark.integration


def test_seed_skills_command_seeds_and_is_idempotent(
    fresh_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        "DATABASE_URL": fresh_database_url,
        "ENVIRONMENT": "test",
        "FRONTEND_ORIGIN": "http://localhost:3000",
        "PROXY_SECRET": "t" * 32,
        "SESSION_COOKIE_SECURE": "false",
        "LOG_JSON": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        assert main(["seed-skills"]) == 0
        assert main(["seed-skills"]) == 0
    finally:
        get_settings.cache_clear()

    catalogue = SkillCatalogue.load()
    plain_url = fresh_database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(plain_url) as conn:
        skills = conn.execute("SELECT count(*) FROM skills").fetchone()
        aliases = conn.execute("SELECT count(*) FROM skill_aliases").fetchone()
    assert skills == (len(catalogue.skills),)
    assert aliases == (sum(len(s.aliases) for s in catalogue.skills),)
