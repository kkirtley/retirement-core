from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from retirement_core.domain.enums import FederalAgiComponentType, FilingStatus

NonNegativeMoney = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class FederalAgiComponentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component_type: FederalAgiComponentType
    owner_id: str | None = None
    amount: NonNegativeMoney
    included_in_federal_agi: bool
    included_in_irmaa_magi: bool
    source_account_id: str | None = None
    source_transaction_ids: tuple[str, ...] = ()
    provenance: str


class AnnualSelfEmploymentTaxResult(BaseModel):
    """Authoritative owner-level regular SE-tax result consumed by AGI assembly."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    net_business_profit: NonNegativeMoney
    regular_self_employment_tax: NonNegativeMoney
    deductible_employer_equivalent_tax: NonNegativeMoney
    dataset_id: str
    rule_provenance: str

    @model_validator(mode="after")
    def validate_result(self) -> AnnualSelfEmploymentTaxResult:
        if not self.owner_id or not self.dataset_id or not self.rule_provenance:
            raise ValueError(
                "Self-employment AGI result requires owner, dataset ID, and provenance"
            )
        if self.deductible_employer_equivalent_tax > self.regular_self_employment_tax:
            raise ValueError("Deductible SE tax cannot exceed regular self-employment tax")
        return self


class SelfEmploymentAgiDetailResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    net_business_profit: NonNegativeMoney
    deductible_employer_equivalent_tax: NonNegativeMoney
    net_agi_contribution: Decimal
    se_tax_dataset_id: str
    rule_provenance: str


class AnnualFederalAgiResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    filing_status: FilingStatus
    taxable_wages: NonNegativeMoney = Decimal("0")
    taxable_pension: NonNegativeMoney = Decimal("0")
    taxable_rmd_distributions: NonNegativeMoney = Decimal("0")
    taxable_non_rmd_ira_distributions: NonNegativeMoney = Decimal("0")
    federally_taxable_roth_conversions: NonNegativeMoney = Decimal("0")
    federally_taxable_social_security: NonNegativeMoney = Decimal("0")
    taxable_interest: NonNegativeMoney = Decimal("0")
    taxable_self_employment_profit: NonNegativeMoney = Decimal("0")
    tax_exempt_interest: NonNegativeMoney = Decimal("0")
    other_supported_agi_components: NonNegativeMoney = Decimal("0")
    adjustments_to_income: NonNegativeMoney = Decimal("0")
    deductible_self_employment_tax: NonNegativeMoney = Decimal("0")
    self_employment_details: tuple[SelfEmploymentAgiDetailResult, ...] = ()
    components: tuple[FederalAgiComponentResult, ...] = ()
    unsupported_income_diagnostics: tuple[str, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def federal_adjusted_gross_income(self) -> Decimal:
        return (
            self.taxable_wages
            + self.taxable_pension
            + self.taxable_rmd_distributions
            + self.taxable_non_rmd_ira_distributions
            + self.federally_taxable_roth_conversions
            + self.federally_taxable_social_security
            + self.taxable_interest
            + self.taxable_self_employment_profit
            + self.other_supported_agi_components
            - self.adjustments_to_income
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def irmaa_magi(self) -> Decimal:
        return self.federal_adjusted_gross_income + self.tax_exempt_interest

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_self_employment_agi_contribution(self) -> Decimal:
        return self.taxable_self_employment_profit - self.deductible_self_employment_tax
