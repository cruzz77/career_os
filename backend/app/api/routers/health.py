"""Liveness and readiness, used by the Docker healthcheck, the deploy gate and uptime checks."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.deps import AppState, get_app_state
from app.core.errors import ErrorEnvelope, ServiceUnavailableError
from app.core.readiness import evaluate_readiness

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    checks: dict[str, bool]


@router.get("/health")
async def liveness() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get("/health/ready", responses={503: {"model": ErrorEnvelope}})
async def readiness(state: Annotated[AppState, Depends(get_app_state)]) -> ReadinessResponse:
    report = await evaluate_readiness(state.readiness_checks)
    if not report.ready:
        raise ServiceUnavailableError("Not ready", details={"checks": dict(report.checks)})
    return ReadinessResponse(status="ready", checks=dict(report.checks))
