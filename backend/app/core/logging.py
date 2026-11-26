"""Structured JSON logging and the per-request context (request id, access log)."""

from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Final

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.tracebacks import ExceptionDictTransformer

from app.core.config import LogLevel

REQUEST_ID_HEADER: Final = "X-Request-ID"
# The proxy's id is accepted so one request can be traced across Vercel and the API, but only in
# a shape that cannot smuggle newlines or fake key=value pairs into a log line.
_INBOUND_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9._-]{8,64}")
# Docker and the uptime monitor poll these constantly; at INFO they would drown real traffic.
_QUIET_PATHS: Final = frozenset({"/health", "/health/ready"})

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


def configure_logging(level: LogLevel, *, json_output: bool) -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter(shared, json_output=json_output))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _route_uvicorn_loggers_to_root()


def _build_formatter(
    shared: list[structlog.typing.Processor], *, json_output: bool
) -> structlog.stdlib.ProcessorFormatter:
    rendering: list[structlog.typing.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta
    ]
    if json_output:
        rendering += [_exception_dict_renderer(), structlog.processors.JSONRenderer()]
    else:
        rendering.append(
            structlog.dev.ConsoleRenderer(
                colors=sys.stdout.isatty(), exception_formatter=structlog.dev.plain_traceback
            )
        )
    return structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processors=rendering)


def _exception_dict_renderer() -> structlog.processors.ExceptionRenderer:
    # structlog's stock dict_tracebacks captures every frame's local variables. On this API
    # those include passwords, session tokens and resume text, which must never be logged.
    return structlog.processors.ExceptionRenderer(
        ExceptionDictTransformer(show_locals=False, use_rich=False)
    )


def _route_uvicorn_loggers_to_root() -> None:
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # uvicorn's access line would duplicate RequestContextMiddleware's, minus the request id.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.disabled = True


def _resolve_request_id(scope: Scope) -> str:
    for key, value in scope["headers"]:
        if key == b"x-request-id":
            candidate: str = value.decode("latin-1")
            if _INBOUND_REQUEST_ID.fullmatch(candidate):
                return candidate
            break
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._access_log = get_logger("app.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        # Stored on the scope as well as the contextvar: the unhandled-exception handler runs in
        # Starlette's outermost middleware, after this one has already reset the contextvar.
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            self._log_access(scope, status_code, started)
            structlog.contextvars.clear_contextvars()
            _request_id.reset(token)

    def _log_access(self, scope: Scope, status_code: int, started: float) -> None:
        path: str = scope["path"]
        log = self._access_log.debug if path in _QUIET_PATHS else self._access_log.info
        log(
            "request",
            method=scope["method"],
            path=path,
            status=status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
