"""A single job, checked against its source before it is shown (blueprint 6.4.2)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import AppState, get_app_state, get_current_user
from app.core.errors import ErrorEnvelope
from app.domain.jobs import JobView

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])

State = Annotated[AppState, Depends(get_app_state)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 404, 422)
}


# Signed-in only: an open job page can trigger an outbound call to the source, and an
# anonymous endpoint would let anyone with the proxy secret drive those calls in bulk.
@router.get("/{job_id}", responses=_ERRORS, dependencies=[Depends(get_current_user)])
async def get_job(job_id: uuid.UUID, state: State) -> JobView:
    return await state.job_freshness.ensure_fresh(job_id)
