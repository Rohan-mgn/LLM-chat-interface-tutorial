"""Shared bounded schemas for deterministic derived table operations."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

Label = Annotated[str, Field(min_length=1, max_length=200)]


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    aggregate: int = Field(ge=0, le=7, description="Zero-based index in aggregates")
    baseline: dict[Label, Label] = Field(min_length=1, max_length=3)
    current: dict[Label, Label] = Field(min_length=1, max_length=3)
    metrics: list[Literal["difference", "percent_change", "ratio"]] = Field(min_length=1, max_length=3)


class Ranking(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    aggregate: int = Field(ge=0, le=7, description="Zero-based index in aggregates")
    direction: Literal["ascending", "descending"]
    limit: int = Field(ge=1, le=200)
