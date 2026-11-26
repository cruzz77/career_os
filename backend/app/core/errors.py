"""Error envelope: every non-2xx response is {"error": {code, message, details, request_id}}."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, ClassVar, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.core.logging import REQUEST_ID_HEADER, get_logger, get_request_id

ErrorDetails = dict[str, object] | list[dict[str, object]] | None

_log = get_logger(__name__)

_HTTP_STATUS_CODES: Final[Mapping[int, str]] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: ErrorDetails = None
    request_id: str | None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: ErrorBody


class AppError(Exception):
    status_code: ClassVar[int] = 500
    code: ClassVar[str] = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: ErrorDetails = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = dict(headers or {})


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthenticatedError(AppError):
    status_code = 401
    code = "unauthenticated"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AppError):
    """Input rejected by our own code, e.g. inside a dependency.

    FastAPI only turns validation that it performs itself into 422; anything raised from a
    dependency would otherwise surface as a 500.
    """

    status_code = 422
    code = "validation_error"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str = "Too many requests", *, retry_after_s: int) -> None:
        super().__init__(message, headers={"Retry-After": str(retry_after_s)})


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_request_validation)
    # A raw pydantic ValidationError from our own code is deliberately left to this handler:
    # it means we built a model wrongly, which is a 500, not the client's fault.
    app.add_exception_handler(Exception, _handle_unhandled)


async def _handle_app_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, AppError):
        raise exc
    return _envelope_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return _envelope_response(
        request,
        status_code=exc.status_code,
        code=_HTTP_STATUS_CODES.get(exc.status_code, "http_error"),
        message=message,
        headers=exc.headers,
    )


async def _handle_request_validation(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RequestValidationError):
        raise exc
    return _envelope_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=_sanitise_validation_errors(exc.errors()),
    )


async def _handle_unhandled(request: Request, exc: Exception) -> Response:
    request_id = _request_id_for(request)
    # The request-context contextvar is already reset here, so the id is bound explicitly.
    _log.error("unhandled_exception", request_id=request_id, exc_info=exc)
    # This response is sent by Starlette's outermost middleware, which sits outside
    # RequestContextMiddleware, so the header has to be set here or it is missing on 500s.
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return _envelope_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal server error",
        headers=headers,
    )


def _sanitise_validation_errors(errors: Sequence[Any]) -> list[dict[str, object]]:
    # "input" and "ctx" are dropped: "input" echoes the submitted value, which on /auth is a
    # password, and "ctx" can hold non-serialisable objects.
    return [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in errors
    ]


def _request_id_for(request: Request) -> str | None:
    from_scope = request.scope.get("state", {}).get("request_id")
    if isinstance(from_scope, str):
        return from_scope
    return get_request_id()


def _envelope_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: ErrorDetails = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=_request_id_for(request),
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=dict(headers) if headers else None,
    )
