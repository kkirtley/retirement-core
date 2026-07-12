from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.enums import DatasetStatus


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publisher: str
    source_url: str | None = None
    retrieved_at: date
    effective_date: date | None = None


class RuleDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    dataset_type: str
    jurisdiction: str
    tax_year: int
    version: str
    status: DatasetStatus
    values: dict[str, Any]
    provenance: Provenance
    projection_assumptions: dict[str, Decimal] = Field(default_factory=dict)
