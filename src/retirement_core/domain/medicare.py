from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from retirement_core.domain.enums import FilingStatus, MedicareBasePremiumMode

NonNegativeMoney = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class MedicarePersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: str
    part_b_enrollment_date: date | None = None
    part_d_enrollment_date: date | None = None
    part_d_plan_monthly_premium: NonNegativeMoney | None = None

    @model_validator(mode="after")
    def require_part_d_plan_premium(self) -> MedicarePersonInput:
        if self.part_d_enrollment_date is not None and self.part_d_plan_monthly_premium is None:
            raise ValueError("Part D enrollment requires an explicit plan premium")
        return self


class IrmaaTaxRecordInput(BaseModel):
    """Auditable tax-year inputs for IRMAA MAGI, not federal taxable income."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    filing_status: FilingStatus
    federal_adjusted_gross_income: NonNegativeMoney
    tax_exempt_interest: NonNegativeMoney = Decimal("0")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def irmaa_magi(self) -> Decimal:
        return self.federal_adjusted_gross_income + self.tax_exempt_interest


class IrmaaMagiComposition(BaseModel):
    """Currently supported components used to construct federal AGI for IRMAA."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    filing_status: FilingStatus
    taxable_pension: NonNegativeMoney = Decimal("0")
    taxable_social_security: NonNegativeMoney = Decimal("0")
    taxable_roth_conversions: NonNegativeMoney = Decimal("0")
    taxable_rmd: NonNegativeMoney = Decimal("0")
    taxable_interest: NonNegativeMoney = Decimal("0")
    other_supported_agi: NonNegativeMoney = Decimal("0")
    tax_exempt_interest: NonNegativeMoney = Decimal("0")
    qcd: NonNegativeMoney = Decimal("0")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def federal_adjusted_gross_income(self) -> Decimal:
        return sum(
            (
                self.taxable_pension,
                self.taxable_social_security,
                self.taxable_roth_conversions,
                self.taxable_rmd,
                self.taxable_interest,
                self.other_supported_agi,
            ),
            Decimal("0"),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def irmaa_magi(self) -> Decimal:
        return self.federal_adjusted_gross_income + self.tax_exempt_interest


class MedicarePlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    premium_payment_account_id: str
    base_premium_mode: MedicareBasePremiumMode
    people: list[MedicarePersonInput] = Field(default_factory=list)
    historical_tax_records: list[IrmaaTaxRecordInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_records(self) -> MedicarePlanInput:
        owner_ids = [person.owner_id for person in self.people]
        if len(owner_ids) != len(set(owner_ids)):
            raise ValueError("Medicare people must have unique owner IDs")
        years = [record.tax_year for record in self.historical_tax_records]
        if len(years) != len(set(years)):
            raise ValueError("Historical IRMAA tax records must have unique tax years")
        return self


class IrmaaMagiResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    filing_status: FilingStatus
    source: Literal["completed_projection", "historical_tax_record"]
    federal_adjusted_gross_income: Decimal
    tax_exempt_interest: Decimal
    irmaa_magi: Decimal


class MedicarePersonPremiumResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    part_b_months: int
    part_d_months: int
    part_b_base_monthly: Decimal
    part_b_irmaa_monthly: Decimal
    part_d_plan_monthly: Decimal
    part_d_irmaa_monthly: Decimal
    annual_part_b_base: Decimal
    annual_part_b_irmaa: Decimal
    annual_part_d_plan: Decimal
    annual_part_d_irmaa: Decimal
    annual_total: Decimal


class AnnualIrmaaResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    premium_year: int
    magi_tax_year: int
    rule_dataset_id: str
    magi: IrmaaMagiResult
    tier_index: int
    people: tuple[MedicarePersonPremiumResult, ...]
    household_part_b_base: Decimal
    household_part_b_irmaa: Decimal
    household_part_d_plan: Decimal
    household_part_d_irmaa: Decimal
    household_total: Decimal
