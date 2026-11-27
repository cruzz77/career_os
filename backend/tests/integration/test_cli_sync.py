from __future__ import annotations

import pytest

from app.cli import COMMANDS, main
from app.core.config import get_settings
from tests.conftest import UNREACHABLE_DATABASE_URL

pytestmark = pytest.mark.integration


def test_sync_and_embed_are_registered() -> None:
    assert {"sync", "embed", "seed-skills", "purge"} <= set(COMMANDS)


def test_sync_refuses_without_a_contact_address(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "DATABASE_URL": UNREACHABLE_DATABASE_URL,
        "ENVIRONMENT": "test",
        "FRONTEND_ORIGIN": "http://localhost:3000",
        "PROXY_SECRET": "t" * 32,
        "SESSION_COOKIE_SECURE": "false",
        "LOG_JSON": "false",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SOURCE_CONTACT_EMAIL", raising=False)
    get_settings.cache_clear()
    try:
        assert main(["sync"]) == 2
    finally:
        get_settings.cache_clear()
