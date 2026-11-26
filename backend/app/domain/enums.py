"""Allowed values for every enumerated column. The database CHECK constraints are built from
these, so the API layer and the schema cannot disagree."""

from __future__ import annotations

from enum import StrEnum


class ExperienceLevel(StrEnum):
    STUDENT = "student"
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class UserSkillSource(StrEnum):
    RESUME = "resume"
    CHAT = "chat"
    MANUAL = "manual"


class WorkMode(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    INTERNSHIP = "internship"
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


class SkillRequirement(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class PreferenceDimension(StrEnum):
    ROLE = "role"
    LOCATION = "location"
    WORK_MODE = "work_mode"
    EMPLOYMENT_TYPE = "employment_type"
    SENIORITY = "seniority"
    COMPANY_EXCLUDE = "company_exclude"
    SKILL_INTEREST = "skill_interest"
    DOMAIN_INTEREST = "domain_interest"


class PreferenceOperator(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class PreferenceStrength(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class PreferenceSource(StrEnum):
    RESUME = "resume"
    CHAT = "chat"
    UI = "ui"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    REJECTED = "rejected"


class AgentRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"


# At most one active preference per user for these; enforced by a partial unique index.
SINGLE_VALUED_DIMENSIONS: frozenset[PreferenceDimension] = frozenset(
    {PreferenceDimension.SENIORITY}
)
