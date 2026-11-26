"""Application factory. Run with: uvicorn app.main:create_app --factory"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import auth, health
from app.core.config import Settings, get_settings
from app.core.deps import AppState
from app.core.errors import register_exception_handlers
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.proxy_gate import ProxySecretMiddleware
from app.core.rate_limit import InMemoryRateLimiter, RateLimits
from app.core.readiness import DatabaseReadiness
from app.core.security import PasswordHasher
from app.db.session import Database
from app.modules.auth.service import AuthService, SessionPolicy

_HEALTH_PATHS: Final = frozenset({"/health", "/health/ready"})
_DOCS_PATHS: Final = frozenset({"/docs", "/docs/oauth2-redirect", "/openapi.json"})


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, json_output=resolved.log_json)
    docs_enabled = not resolved.is_production
    app = FastAPI(
        title="CareerOS API",
        lifespan=_lifespan(resolved),
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    register_exception_handlers(app)
    # Application traffic arrives server-to-server from the Vercel proxy and never needs CORS.
    # This only lets the frontend origin read the health endpoints, and never with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[resolved.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=[],
        max_age=600,
    )
    app.add_middleware(
        ProxySecretMiddleware,
        secret=resolved.proxy_secret.get_secret_value(),
        exempt_paths=_HEALTH_PATHS | (_DOCS_PATHS if docs_enabled else frozenset()),
    )
    # Added last so it is outermost: gate rejections, preflights and errors all carry the
    # request id.
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    return app


def _lifespan(settings: Settings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database.from_settings(settings)
        rate_limiter = InMemoryRateLimiter()
        rate_limits = RateLimits.from_settings(settings)
        app.state.services = AppState(
            settings=settings,
            database=database,
            readiness_checks=(DatabaseReadiness(database),),
            rate_limiter=rate_limiter,
            rate_limits=rate_limits,
            auth_service=AuthService(
                database,
                PasswordHasher(),
                SessionPolicy.from_settings(settings),
                rate_limiter=rate_limiter,
                login_email_limit=rate_limits.login_per_email,
            ),
        )
        try:
            yield
        finally:
            await database.dispose()

    return lifespan
