"""Readiness checks behind GET /health/ready."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.logging import get_logger
from app.db.session import Database

_log = get_logger(__name__)


class ReadinessCheck(Protocol):
    @property
    def name(self) -> str: ...

    async def is_ready(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    checks: Mapping[str, bool]

    @property
    def ready(self) -> bool:
        # No registered checks means nothing was verified, which is not the same as healthy.
        return bool(self.checks) and all(self.checks.values())


class DatabaseReadiness:
    def __init__(self, database: Database) -> None:
        self._database = database

    @property
    def name(self) -> str:
        return "database"

    async def is_ready(self) -> bool:
        return await self._database.ping()


async def evaluate_readiness(checks: Sequence[ReadinessCheck]) -> ReadinessReport:
    results = await asyncio.gather(*(_run_check(check) for check in checks))
    return ReadinessReport(
        checks={check.name: result for check, result in zip(checks, results, strict=True)}
    )


async def _run_check(check: ReadinessCheck) -> bool:
    # A check that raises is a failed check, not a 500 from the endpoint that reports on it.
    try:
        return await check.is_ready()
    except Exception:
        _log.exception("readiness_check_failed", check=check.name)
        return False
