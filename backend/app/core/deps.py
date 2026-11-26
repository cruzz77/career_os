"""Typed application state and the dependencies that expose it to routers."""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Request

from app.core.config import Settings
from app.core.errors import RateLimitedError, UnauthenticatedError
from app.core.rate_limit import RateLimit, RateLimiter, RateLimits
from app.core.readiness import ReadinessCheck
from app.db.session import Database
from app.domain.auth import AuthenticatedUser, ClientInfo
from app.modules.auth.service import AuthService
from app.modules.jobs.freshness import JobFreshnessService

CLIENT_IP_HEADER: Final = "X-Client-IP"


@dataclass(frozen=True, slots=True)
class AppState:
    settings: Settings
    database: Database
    readiness_checks: tuple[ReadinessCheck, ...]
    rate_limiter: RateLimiter
    rate_limits: RateLimits
    auth_service: AuthService
    job_freshness: JobFreshnessService


def get_app_state(request: Request) -> AppState:
    # app.state is untyped; this is the single place it is read, and it fails loudly if the
    # lifespan never ran instead of letting a route hit an AttributeError.
    state = getattr(request.app.state, "services", None)
    if not isinstance(state, AppState):
        raise RuntimeError("application state missing: the lifespan did not run")
    return state


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(user_agent=request.headers.get("user-agent"), ip=_client_ip(request))


def _client_ip(request: Request) -> str | None:
    # Every /v1 request has passed ProxySecretMiddleware, so only the BFF proxy can have set
    # this header, and the proxy overwrites it with the browser's address. The direct peer is
    # the proxy itself (Vercel, then Caddy), which would put every user in one rate-limit bucket.
    forwarded = request.headers.get(CLIENT_IP_HEADER)
    if forwarded:
        try:
            return str(ipaddress.ip_address(forwarded.strip()))
        except ValueError:
            pass
    return request.client.host if request.client else None


async def get_current_user(
    request: Request, state: Annotated[AppState, Depends(get_app_state)]
) -> AuthenticatedUser:
    token = request.cookies.get(state.settings.session_cookie_name)
    user = await state.auth_service.authenticate(token) if token else None
    if user is None:
        raise UnauthenticatedError("Not signed in")
    return user


def limit_by_client_ip(
    select: Callable[[RateLimits], RateLimit],
) -> Callable[..., Awaitable[None]]:
    async def enforce(
        state: Annotated[AppState, Depends(get_app_state)],
        client: Annotated[ClientInfo, Depends(get_client_info)],
    ) -> None:
        decision = await state.rate_limiter.hit(
            f"ip:{client.ip or 'unknown'}", select(state.rate_limits)
        )
        if not decision.allowed:
            raise RateLimitedError(retry_after_s=decision.retry_after_s)

    return enforce
