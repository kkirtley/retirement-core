from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.domain.enums import AccountType
from retirement_core.rules.models import Provenance, RuleDataset

NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class AgeThreshold(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    years: int = Field(ge=0)
    months: int = Field(default=0, ge=0, le=11)


class RmdStartAgeRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    birth_date_start: date | None
    birth_date_end: date | None
    start_age: AgeThreshold


class RmdQcdAccountEligibilityRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    supported_rmd_account_types: frozenset[AccountType]
    qcd_eligible_account_types: frozenset[AccountType]
    qcd_accounts_can_satisfy_owner_rmd: bool


class RmdQcdTaxTreatmentRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    gross_rmd_is_ordinary_income: bool
    qcd_excluded_from_ordinary_income: bool
    qcd_counts_toward_same_owner_rmd: bool
    qcd_cannot_satisfy_another_owner_rmd: bool
    direct_trustee_to_charity_required: bool
    deductible_contribution_offset_applies: bool


class RmdQcdDatasetValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rmd_start_age_schedule: tuple[RmdStartAgeRule, ...]
    qcd_eligibility_age: AgeThreshold
    statutory_qcd_maximum_per_owner: NonNegativeDecimal
    uniform_lifetime_table: dict[int, NonNegativeDecimal]
    account_eligibility: RmdQcdAccountEligibilityRules
    tax_treatment: RmdQcdTaxTreatmentRules


class RmdQcdRules(RmdQcdDatasetValues):
    dataset_id: str
    tax_year: int
    effective_from: date
    effective_to: date | None
    provenance: Provenance

    @classmethod
    def from_dataset(cls, dataset: RuleDataset) -> Self:
        if dataset.dataset_type != "rmd_qcd" or dataset.jurisdiction != "US-FED":
            raise ValueError("Expected a US-FED rmd_qcd rule dataset")
        values = RmdQcdDatasetValues.model_validate(dataset.values)
        if dataset.tax_year is None or dataset.effective_from is None:
            raise ValueError("RMD/QCD datasets require tax_year and effective_from")
        return cls(
            dataset_id=dataset.dataset_id,
            tax_year=dataset.tax_year,
            effective_from=dataset.effective_from,
            effective_to=dataset.effective_to,
            provenance=dataset.provenance,
            **values.model_dump(),
        )

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        if not self.rmd_start_age_schedule:
            raise ValueError("RMD start-age schedule cannot be empty")
        if not self.uniform_lifetime_table:
            raise ValueError("Uniform Lifetime Table cannot be empty")
        if any(divisor <= 0 for divisor in self.uniform_lifetime_table.values()):
            raise ValueError("Uniform Lifetime divisors must be positive")
        return self
