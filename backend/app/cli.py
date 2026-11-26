"""Operational commands, run by the systemd timers: python -m app.cli <command>.

Each command is one entry in COMMANDS. The Retrieval chat registers sync and embed here.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine, Sequence
from datetime import timedelta
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.modules.jobs.purge import PurgeService

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


COMMANDS: dict[str, tuple[Command, str]] = {
    "purge": (
        _purge,
        "delete jobs expired over 30 days ago (except saved/judged) and dead sessions",
    ),
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
