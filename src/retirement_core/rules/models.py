from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.domain.enums import DatasetStatus, FilingStatus

NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
TaxRate = Annotated[Decimal, Field(ge=0, le=1)]


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publisher: str
    source_title: str | None = None
    source_url: str | None = None
    source_reference: str | None = None
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


class FederalTaxBracket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower_bound: NonNegativeDecimal
    upper_bound: NonNegativeDecimal | None
    rate: TaxRate


class FederalTaxFilingStatusRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    standard_deduction: NonNegativeDecimal
    ordinary_income_brackets: tuple[FederalTaxBracket, ...]


class FederalTaxDatasetValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    filing_statuses: dict[FilingStatus, FederalTaxFilingStatusRules]


class FederalTaxRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: str
    tax_year: int
    filing_status: FilingStatus
    standard_deduction: NonNegativeDecimal
    ordinary_income_brackets: tuple[FederalTaxBracket, ...]
    provenance: Provenance

    @classmethod
    def from_dataset(cls, dataset: RuleDataset, filing_status: FilingStatus) -> Self:
        if dataset.dataset_type != "federal_tax" or dataset.jurisdiction != "US-FED":
            raise ValueError("Expected a US-FED federal_tax rule dataset")
        values = FederalTaxDatasetValues.model_validate(dataset.values)
        try:
            status_rules = values.filing_statuses[filing_status]
        except KeyError as error:
            raise ValueError(
                f"Dataset {dataset.dataset_id} has no rules for {filing_status.value}"
            ) from error
        return cls(
            dataset_id=dataset.dataset_id,
            tax_year=dataset.tax_year,
            filing_status=filing_status,
            standard_deduction=status_rules.standard_deduction,
            ordinary_income_brackets=status_rules.ordinary_income_brackets,
            provenance=dataset.provenance,
        )

    @model_validator(mode="after")
    def validate_brackets(self) -> Self:
        if not self.ordinary_income_brackets:
            raise ValueError("At least one ordinary-income bracket is required")
        expected_lower = Decimal("0")
        previous_rate = Decimal("-1")
        for index, bracket in enumerate(self.ordinary_income_brackets):
            if bracket.lower_bound != expected_lower:
                raise ValueError("Ordinary-income brackets must be contiguous and start at zero")
            if bracket.rate < previous_rate:
                raise ValueError("Ordinary-income bracket rates must not decrease")
            if bracket.upper_bound is None:
                if index != len(self.ordinary_income_brackets) - 1:
                    raise ValueError("Only the final ordinary-income bracket may be unbounded")
            else:
                if bracket.upper_bound <= bracket.lower_bound:
                    raise ValueError("Bracket upper bound must exceed its lower bound")
                expected_lower = bracket.upper_bound
            previous_rate = bracket.rate
        if self.ordinary_income_brackets[-1].upper_bound is not None:
            raise ValueError("The final ordinary-income bracket must be unbounded")
        return self
