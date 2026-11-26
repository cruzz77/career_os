"""Job value objects shared between Retrieval (sources) and Backend (freshness, jobs API)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    Availability,
    EmploymentType,
    JobStatus,
    PostingStatus,
    Seniority,
    SkillRequirement,
    WorkMode,
)


@dataclass(frozen=True, slots=True)
class PostingCheck:
    """What JobSource.check_posting returns (Retrieval implements it, blueprint 6.4.2)."""

    status: PostingStatus


class JobSkillView(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    requirement: SkillRequirement


class JobView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    company: str | None
    description: str
    location_city: str | None
    location_country: str | None
    work_mode: WorkMode
    employment_type: EmploymentType
    seniority: Seniority
    posted_at: datetime | None
    source: str
    source_url: str | None
    skills: list[JobSkillView]
    status: JobStatus
    availability: Availability
    last_verified_at: datetime
    expired_at: datetime | None
