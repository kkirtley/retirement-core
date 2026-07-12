from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.rules.models import Provenance, RuleDataset

NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
Rate = Annotated[Decimal, Field(ge=0, le=1)]


class RuleComponentStatus(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    status: str
    authority: str | None = None


class MissouriTaxBracket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower_bound: NonNegativeDecimal
    upper_bound: NonNegativeDecimal | None
    rate: Rate


class MissouriFederalDeductionBand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    upper_income: NonNegativeDecimal | None
    percentage: Rate


class MissouriRothConversionRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    included_in_missouri_agi: bool
    income_amount: str
    private_retirement_subtraction_eligible: bool
    classification: str
    status: str
    dor_conversion_specific_guidance: str
    review_trigger: str


class MissouriTaxRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: str
    dataset_version: str
    tax_year: int
    effective_from: date
    effective_to: date | None
    brackets: tuple[MissouriTaxBracket, ...]
    tax_rounding_increment: Decimal = Field(gt=0)
    standard_deduction: NonNegativeDecimal
    additional_age_threshold: int
    additional_age_amount: NonNegativeDecimal
    social_security_retirement_age: int
    social_security_subtraction_rate: Rate
    public_pension_maximum_per_owner: NonNegativeDecimal
    public_pension_reduced_by_owner_social_security: bool
    private_retirement_maximum_per_owner: NonNegativeDecimal
    private_retirement_combined_threshold: NonNegativeDecimal
    private_retirement_phaseout_rate: Rate
    roth_conversion: MissouriRothConversionRules
    federal_deduction_maximum: NonNegativeDecimal
    federal_deduction_bands: tuple[MissouriFederalDeductionBand, ...]
    component_statuses: dict[str, RuleComponentStatus]
    provenance: Provenance

    @classmethod
    def from_dataset(cls, dataset: RuleDataset) -> Self:
        if dataset.dataset_type != "state_individual_income_tax" or dataset.jurisdiction != "US-MO":
            raise ValueError("Expected a US-MO state individual income tax dataset")
        if dataset.tax_year is None or dataset.effective_from is None:
            raise ValueError("Missouri tax datasets require tax_year and effective_from")
        values = dataset.values
        return cls(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            tax_year=dataset.tax_year,
            effective_from=dataset.effective_from,
            effective_to=dataset.effective_to,
            provenance=dataset.provenance,
            **values,
        )

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        if not self.brackets or self.brackets[0].lower_bound != 0:
            raise ValueError("Missouri tax brackets must start at zero")
        expected = Decimal("0")
        for index, bracket in enumerate(self.brackets):
            if bracket.lower_bound != expected:
                raise ValueError("Missouri tax brackets must be contiguous")
            if bracket.upper_bound is None:
                if index != len(self.brackets) - 1:
                    raise ValueError("Only the final Missouri bracket may be unbounded")
            else:
                expected = bracket.upper_bound
        if self.brackets[-1].upper_bound is not None:
            raise ValueError("The final Missouri bracket must be unbounded")
        return self
