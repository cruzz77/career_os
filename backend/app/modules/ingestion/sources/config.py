"""Typed, validated views of lever_companies.yaml and greenhouse_boards.yaml (blueprint 6.4.1)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import EmploymentType, Seniority, WorkMode
from app.modules.ingestion.mapping import normalise

_DIR: Final = Path(__file__).parent
LEVER_CONFIG_PATH: Final = _DIR / "lever_companies.yaml"
GREENHOUSE_CONFIG_PATH: Final = _DIR / "greenhouse_boards.yaml"

_FROZEN = ConfigDict(frozen=True, extra="forbid")


def _check_table[E: StrEnum](table: Mapping[str, E]) -> Mapping[str, E]:
    for key, value in table.items():
        if key != normalise(key):
            raise ValueError(f"key {key!r} is not normalised")
        if value == "unknown":
            raise ValueError(f"{key!r}: 'unknown' is the fallback, not a table value")
    return table


class LeverCompany(BaseModel):
    model_config = _FROZEN

    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    instance: Literal["global", "eu"]


class LeverConfig(BaseModel):
    model_config = _FROZEN

    version: int = Field(ge=1)
    validated_on: date
    companies: tuple[LeverCompany, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        slugs = [company.slug for company in self.companies]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate Lever slug")
        return self

    def company(self, slug: str) -> LeverCompany:
        for company in self.companies:
            if company.slug == slug:
                return company
        raise KeyError(f"Lever slug {slug!r} is not configured")


class EmploymentTypeField(BaseModel):
    model_config = _FROZEN

    field: str = Field(min_length=1)
    values: dict[str, EmploymentType]

    _check = field_validator("values")(_check_table)


class WorkModeField(BaseModel):
    model_config = _FROZEN

    field: str = Field(min_length=1)
    values: dict[str, WorkMode]

    _check = field_validator("values")(_check_table)


class SeniorityField(BaseModel):
    model_config = _FROZEN

    field: str = Field(min_length=1)
    values: dict[str, Seniority]

    _check = field_validator("values")(_check_table)


class CountryField(BaseModel):
    model_config = _FROZEN

    field: str = Field(min_length=1)


class GreenhouseMetadata(BaseModel):
    model_config = _FROZEN

    employment_type: EmploymentTypeField | None = None
    work_mode: WorkModeField | None = None
    seniority: SeniorityField | None = None
    country: CountryField | None = None


class GreenhouseBoard(BaseModel):
    model_config = _FROZEN

    token: str = Field(min_length=1)
    name: str = Field(min_length=1)
    metadata: GreenhouseMetadata = GreenhouseMetadata()
    location_work_mode: dict[str, WorkMode] = {}

    _check = field_validator("location_work_mode")(_check_table)


class GreenhouseConfig(BaseModel):
    model_config = _FROZEN

    version: int = Field(ge=1)
    validated_on: date
    boards: tuple[GreenhouseBoard, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        tokens = [board.token for board in self.boards]
        if len(tokens) != len(set(tokens)):
            raise ValueError("duplicate Greenhouse board token")
        return self

    def board(self, token: str) -> GreenhouseBoard:
        for board in self.boards:
            if board.token == token:
                return board
        raise KeyError(f"Greenhouse board {token!r} is not configured")


def load_lever_config(path: Path = LEVER_CONFIG_PATH) -> LeverConfig:
    return LeverConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_greenhouse_config(path: Path = GREENHOUSE_CONFIG_PATH) -> GreenhouseConfig:
    return GreenhouseConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
