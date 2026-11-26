"""Signup, login, logout and the signed-in user's own account."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.api.session_cookie import clear_session_cookie, set_session_cookie
from app.core.deps import (
    AppState,
    get_app_state,
    get_client_info,
    get_current_user,
    limit_by_client_ip,
)
from app.core.errors import ErrorEnvelope
from app.domain.auth import (
    AuthenticatedUser,
    ClientInfo,
    LoginRequest,
    SignupRequest,
    UserPublic,
)

router = APIRouter(prefix="/v1", tags=["auth"])

State = Annotated[AppState, Depends(get_app_state)]
Client = Annotated[ClientInfo, Depends(get_client_info)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 409, 422, 429)
}


@router.post(
    "/auth/signup",
    status_code=201,
    responses=_ERRORS,
    dependencies=[Depends(limit_by_client_ip(lambda limits: limits.signup_per_ip))],
)
async def signup(
    body: SignupRequest, response: Response, state: State, client: Client
) -> UserPublic:
    result = await state.auth_service.signup(body, client)
    set_session_cookie(response, result.session.token, state.settings)
    return result.user


@router.post(
    "/auth/login",
    responses=_ERRORS,
    dependencies=[Depends(limit_by_client_ip(lambda limits: limits.login_per_ip))],
)
async def login(body: LoginRequest, response: Response, state: State, client: Client) -> UserPublic:
    result = await state.auth_service.login(body, client)
    set_session_cookie(response, result.session.token, state.settings)
    return result.user


@router.post("/auth/logout", status_code=204, response_class=Response)
async def logout(request: Request, state: State) -> Response:
    token = request.cookies.get(state.settings.session_cookie_name)
    if token:
        await state.auth_service.logout(token)
    response = Response(status_code=204)
    clear_session_cookie(response, state.settings)
    return response


@router.get("/me", responses=_ERRORS)
async def read_me(user: CurrentUser, state: State) -> UserPublic:
    return await state.auth_service.get_user(user.user_id)


@router.delete("/me", status_code=204, response_class=Response, responses=_ERRORS)
async def delete_me(user: CurrentUser, state: State) -> Response:
    await state.auth_service.delete_account(user.user_id)
    response = Response(status_code=204)
    clear_session_cookie(response, state.settings)
    return response
