"""Rejects application traffic that did not come through the Vercel BFF proxy (blueprint 6.6)."""

from __future__ import annotations

from typing import Final

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import ErrorBody, ErrorEnvelope
from app.core.security import secrets_match

PROXY_HEADER_NAME: Final = "X-Proxy-Secret"
_HEADER_KEY: Final = PROXY_HEADER_NAME.lower().encode("latin-1")


class ProxySecretMiddleware:
    """Runs before routing, so a caller without the secret cannot even learn which routes
    exist: everything not exempt answers 403."""

    def __init__(self, app: ASGIApp, *, secret: str, exempt_paths: frozenset[str]) -> None:
        self._app = app
        self._secret = secret
        self._exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._exempt_paths:
            await self._app(scope, receive, send)
            return
        if self._carries_valid_secret(scope):
            await self._app(scope, receive, send)
            return
        request_id = scope.get("state", {}).get("request_id")
        envelope = ErrorEnvelope(
            error=ErrorBody(
                code="forbidden",
                message="Forbidden",
                request_id=request_id if isinstance(request_id, str) else None,
            )
        )
        response = JSONResponse(status_code=403, content=envelope.model_dump(mode="json"))
        await response(scope, receive, send)

    def _carries_valid_secret(self, scope: Scope) -> bool:
        for key, value in scope["headers"]:
            if key == _HEADER_KEY:
                provided: str = value.decode("latin-1")
                return secrets_match(provided, self._secret)
        return False
