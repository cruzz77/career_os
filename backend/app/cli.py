"""Operational commands, run by the systemd timers: python -m app.cli <command>.

Each command is one entry in COMMANDS. The Retrieval chat registers sync and embed here.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Coroutine, Sequence
from datetime import timedelta
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import Database
from app.modules.ingestion.embed import EmbedService
from app.modules.ingestion.seed import SkillSeedService
from app.modules.ingestion.skills import SkillCatalogue
from app.modules.ingestion.sources.base import JobSource
from app.modules.ingestion.sources.config import load_greenhouse_config, load_lever_config
from app.modules.ingestion.sources.greenhouse import GreenhouseSource
from app.modules.ingestion.sources.http import (
    PoliteHttpClient,
    build_http_client,
    user_agent_for,
)
from app.modules.ingestion.sources.lever import LeverSource
from app.modules.ingestion.sync import SyncService
from app.modules.jobs.purge import PurgeService
from app.modules.retrieval.embedder import FastEmbedder

# asyncio.run needs a coroutine, not any awaitable; every command is an async def.
Command = Callable[[Settings], Coroutine[Any, Any, int]]


async def _purge(settings: Settings) -> int:
    database = Database.from_settings(settings)
    try:
        await PurgeService(
            database,
            absolute_session_ttl=timedelta(days=settings.session_absolute_ttl_days),
        ).run()
    finally:
        await database.dispose()
    return 0


async def _seed_skills(settings: Settings) -> int:
    database = Database.from_settings(settings)
    try:
        await SkillSeedService(database).run(SkillCatalogue.load())
    finally:
        await database.dispose()
    return 0


_SYNC_HTTP_TIMEOUT_S = 20.0
_log = get_logger(__name__)


async def _sync(settings: Settings) -> int:
    if not settings.source_contact_email:
        _log.error("sync_refused", reason="SOURCE_CONTACT_EMAIL is not set (blueprint 6.4.1)")
        return 2
    user_agent = user_agent_for(settings.source_contact_email)
    database = Database.from_settings(settings)
    try:
        async with build_http_client(user_agent, timeout_s=_SYNC_HTTP_TIMEOUT_S) as raw:
            http = PoliteHttpClient(raw)
            sources: list[JobSource] = [
                LeverSource(http, load_lever_config()),
                GreenhouseSource(http, load_greenhouse_config()),
            ]
            report = await SyncService(database, sources).run()
    finally:
        await database.dispose()
    incomplete = [f"{b.source}:{b.board}" for b in report.boards if not b.complete]
    _log.info(
        "sync_finished",
        boards=len(report.boards),
        inserted=sum(b.inserted for b in report.boards),
        updated=sum(b.updated for b in report.boards),
        expired=sum(b.expired for b in report.boards)
        + report.expired_past_deadline
        + report.expired_stale,
        incomplete=incomplete,
    )
    # 0 even with incomplete boards: the timer runs `sync && embed`, and one broken board must
    # not block embeddings. Failures are in ingestion_runs and covered by the stale sweep.
    return 0


async def _embed(settings: Settings) -> int:
    embedder = FastEmbedder(
        settings.embedding_model, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH")
    )
    await embedder.load()
    database = Database.from_settings(settings)
    try:
        await EmbedService(database, embedder).run()
    finally:
        await database.dispose()
    return 0


COMMANDS: dict[str, tuple[Command, str]] = {
    "purge": (
        _purge,
        "delete jobs expired over 30 days ago (except saved/judged) and dead sessions",
    ),
    "seed-skills": (
        _seed_skills,
        "upsert the skill catalogue into skills/skill_aliases (idempotent)",
    ),
    "sync": (_sync, "fetch every configured board, upsert jobs, expire closed ones"),
    "embed": (_embed, "embed active jobs with no vector or a stale one"),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name, (_, help_text) in COMMANDS.items():
        subcommands.add_parser(name, help=help_text)
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    command, _ = COMMANDS[args.command]
    return asyncio.run(command(settings))


if __name__ == "__main__":
    sys.exit(main())
