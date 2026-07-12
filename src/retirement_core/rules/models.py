from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.domain.enums import DatasetStatus, FilingStatus

NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
TaxRate = Annotated[Decimal, Field(ge=0, le=1)]


class SourceAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    publisher: str
    source_title: str
    source_url: str
    source_reference: str | None = None


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publisher: str
    source_title: str | None = None
    source_url: str | None = None
    source_reference: str | None = None
    retrieved_at: date
    effective_date: date | None = None
    additional_sources: tuple[SourceAttribution, ...] = ()


class RuleDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    dataset_type: str
    jurisdiction: str
    tax_year: int | None = None
    premium_year: int | None = None
    effective_from: date | None = None
    effective_to: date | None = None
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


class FederalSocialSecurityTaxRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_amount: NonNegativeDecimal
    adjusted_base_amount: NonNegativeDecimal
    first_tier_rate: TaxRate
    second_tier_rate: TaxRate
    maximum_taxable_rate: TaxRate

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        if self.adjusted_base_amount <= self.base_amount:
            raise ValueError("Social Security adjusted base must exceed its base amount")
        return self


class FederalTaxFilingStatusRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    standard_deduction: NonNegativeDecimal
    ordinary_income_brackets: tuple[FederalTaxBracket, ...]
    social_security_taxation: FederalSocialSecurityTaxRules


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
    social_security_taxation: FederalSocialSecurityTaxRules
    provenance: Provenance

    @classmethod
    def from_dataset(cls, dataset: RuleDataset, filing_status: FilingStatus) -> Self:
        if dataset.dataset_type != "federal_tax" or dataset.jurisdiction != "US-FED":
            raise ValueError("Expected a US-FED federal_tax rule dataset")
        if dataset.tax_year is None:
            raise ValueError("Federal tax dataset must define tax_year")
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
            social_security_taxation=status_rules.social_security_taxation,
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


class IrmaaTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower_bound: NonNegativeDecimal | None
    lower_inclusive: bool
    upper_bound: NonNegativeDecimal | None
    upper_inclusive: bool
    part_b_irmaa_monthly: NonNegativeDecimal
    part_d_irmaa_monthly: NonNegativeDecimal


class IrmaaFilingStatusRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tiers: tuple[IrmaaTier, ...]


class MedicareIrmaaDatasetValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    magi_lookback_years: int = Field(gt=0)
    part_b_standard_monthly_premium: NonNegativeDecimal
    filing_statuses: dict[FilingStatus, IrmaaFilingStatusRules]


class MedicareIrmaaRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: str
    premium_year: int
    effective_from: date
    effective_to: date
    magi_lookback_years: int
    part_b_standard_monthly_premium: Decimal
    filing_statuses: dict[FilingStatus, IrmaaFilingStatusRules]
    provenance: Provenance

    @classmethod
    def from_dataset(cls, dataset: RuleDataset) -> Self:
        if dataset.dataset_type != "medicare_irmaa" or dataset.jurisdiction != "US-FED":
            raise ValueError("Expected a US-FED medicare_irmaa rule dataset")
        if (
            dataset.premium_year is None
            or dataset.effective_from is None
            or dataset.effective_to is None
        ):
            raise ValueError("Medicare IRMAA dataset requires premium_year and effective dates")
        values = MedicareIrmaaDatasetValues.model_validate(dataset.values)
        return cls(
            dataset_id=dataset.dataset_id,
            premium_year=dataset.premium_year,
            effective_from=dataset.effective_from,
            effective_to=dataset.effective_to,
            magi_lookback_years=values.magi_lookback_years,
            part_b_standard_monthly_premium=values.part_b_standard_monthly_premium,
            filing_statuses=values.filing_statuses,
            provenance=dataset.provenance,
        )

    @model_validator(mode="after")
    def validate_tiers(self) -> Self:
        for status, status_rules in self.filing_statuses.items():
            if not status_rules.tiers:
                raise ValueError(f"IRMAA tiers are required for {status.value}")
            previous_upper: Decimal | None = None
            for index, tier in enumerate(status_rules.tiers):
                if index == 0 and tier.lower_bound is not None:
                    raise ValueError("First IRMAA tier must be unbounded below")
                if index > 0 and tier.lower_bound != previous_upper:
                    raise ValueError("IRMAA tier boundaries must be contiguous")
                if (
                    tier.upper_bound is not None
                    and tier.lower_bound is not None
                    and tier.upper_bound <= tier.lower_bound
                ):
                    raise ValueError("IRMAA tier upper bound must exceed lower bound")
                previous_upper = tier.upper_bound
            if status_rules.tiers[-1].upper_bound is not None:
                raise ValueError("Final IRMAA tier must be unbounded above")
        return self
