"""The session cookie. The BFF proxy re-emits it on the frontend origin, so it stays first-party."""

from __future__ import annotations

from typing import Final

from fastapi import Response

from app.core.config import Settings

_SECONDS_PER_DAY: Final = 86_400


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    # Max-Age is the absolute lifetime; the server enforces the shorter sliding window itself.
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_absolute_ttl_days * _SECONDS_PER_DAY,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
