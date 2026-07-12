from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    FilingStatus,
    IncomeType,
    PensionType,
    QcdAllocationMethod,
    QcdTargetMode,
    ResidencyStatus,
    RothConversionMethod,
    SocialSecurityBenefitSubtype,
    TaxableRmdAllocationMethod,
    TransactionType,
)
from retirement_core.domain.medicare import MedicarePlanInput

NonNegativeMoney = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=1)]


class PersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    date_of_birth: date
    retirement_date: date | None = None


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    owner_id: str
    account_type: AccountType
    starting_balance: NonNegativeMoney
    annual_return: Percent = Decimal("0")


class SocialSecurityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    owner_id: str
    benefit_subtype: SocialSecurityBenefitSubtype | None = None
    claim_date: date
    monthly_benefit: NonNegativeMoney
    annual_cola: Percent = Decimal("0")
    destination_account_id: str


class IncomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    income_type: IncomeType = IncomeType.UNSPECIFIED
    pension_type: PensionType | None = None
    owner_id: str | None = None
    annual_amount: NonNegativeMoney
    start_date: date
    end_date: date | None = None
    destination_account_id: str | None = None
    taxable_federal: bool = True
    taxable_state: bool = True


class AnnualQcdOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    paused: bool = False
    target_amount: NonNegativeMoney | None = None
    reason: str | None = None


class QcdPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    annual_qcd_floor: NonNegativeMoney = Decimal("0")
    target_mode: QcdTargetMode = QcdTargetMode.NONE
    allocation_method: QcdAllocationMethod = QcdAllocationMethod.PROPORTIONAL_TO_OWNER_RMD
    owner_priority: list[str] = Field(default_factory=list)
    account_priority: list[str] = Field(default_factory=list)
    paused_years: set[int] = Field(default_factory=set)
    annual_overrides: dict[int, AnnualQcdOverride] = Field(default_factory=dict)


class GivingPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_rate_after_tax_income: Percent = Decimal("0.10")
    qcd_policy: QcdPolicyInput = Field(default_factory=QcdPolicyInput)
    qcd_enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_qcd_policy(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy = data.get("qcd_enabled")
        nested = data.get("qcd_policy")
        if legacy is not None and nested is not None:
            nested_enabled = (
                nested.get("enabled", False)
                if isinstance(nested, dict)
                else getattr(nested, "enabled", None)
            )
            if nested_enabled is None or bool(legacy) != bool(nested_enabled):
                raise ValueError("qcd_enabled conflicts with qcd_policy.enabled")
        elif legacy is not None:
            data["qcd_policy"] = {
                "enabled": bool(legacy),
                "annual_qcd_floor": "0.00",
                "target_mode": "none",
            }
        return data


class TaxableRmdSourcePolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allocation_method: TaxableRmdAllocationMethod
    account_priority: list[str] = Field(default_factory=list)
    explicit_account_amounts: dict[int, dict[str, dict[str, NonNegativeMoney]]] = Field(
        default_factory=dict
    )


class StateResidencyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state_code: str
    status: ResidencyStatus


class AnnualTransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    year: int
    transaction_type: TransactionType
    amount: NonNegativeMoney
    source_account_id: str | None = None
    destination_account_id: str | None = None
    charitable_method: CharitableGivingMethod | None = None
    taxable_amount: NonNegativeMoney | None = None
    roth_conversion_method: RothConversionMethod | None = None

    @model_validator(mode="after")
    def validate_tax_classification(self) -> AnnualTransactionInput:
        if self.transaction_type is TransactionType.ROTH_CONVERSION:
            if self.taxable_amount is not None and self.taxable_amount > self.amount:
                raise ValueError("Roth conversion taxable amount cannot exceed converted amount")
            return self
        if self.taxable_amount is not None or self.roth_conversion_method is not None:
            raise ValueError("Conversion tax fields are only valid for Roth conversions")
        return self


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    household_name: str
    filing_status: FilingStatus
    start_date: date
    end_date: date
    people: list[PersonInput]
    accounts: list[AccountInput]
    social_security: list[SocialSecurityInput] = Field(default_factory=list)
    income: list[IncomeInput] = Field(default_factory=list)
    transactions: list[AnnualTransactionInput] = Field(default_factory=list)
    giving_policy: GivingPolicyInput = Field(default_factory=GivingPolicyInput)
    federal_tax_payment_account_id: str | None = None
    state_residency: StateResidencyInput | None = None
    missouri_tax_payment_account_id: str | None = None
    medicare: MedicarePlanInput | None = None
    taxable_rmd_destination_account_by_owner: dict[str, str] = Field(default_factory=dict)
    taxable_rmd_source_policy: TaxableRmdSourcePolicyInput | None = None
    allow_negative_cash_balance: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reporting_frequency: str = "annual"
    persist_results: bool = True
    scenario_id: str = "baseline"


class ProjectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: PlanInput
    options: RunOptions = Field(default_factory=RunOptions)


class AnnualAccountResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    account_id: str
    beginning_balance: Decimal
    investment_return: Decimal
    contributions: Decimal = Decimal("0")
    transfers_in: Decimal = Decimal("0")
    withdrawals: Decimal = Decimal("0")
    transfers_out: Decimal = Decimal("0")
    roth_conversions: Decimal = Decimal("0")
    qcd: Decimal = Decimal("0")
    ending_balance: Decimal


class AnnualHouseholdResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    gross_income: Decimal
    taxes: Decimal
    after_tax_income: Decimal
    giving_target: Decimal
    spending: Decimal
    contributions: Decimal = Decimal("0")
    cash_withdrawals: Decimal = Decimal("0")
    cash_surplus: Decimal
    federal_tax_result: FederalIncomeTaxResult | None = None
    social_security_benefits: tuple[AnnualSocialSecurityBenefit, ...] = ()
    social_security_taxation: SocialSecurityTaxationResult | None = None
    rmd_qcd_result: AnnualRmdQcdResult | None = None
    missouri_tax_result: MissouriTaxResult | None = None


class AnnualRmdAccountResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    source_account_id: str
    prior_year_end_balance: Decimal
    divisor: Decimal | None
    gross_rmd: Decimal
    qcd: Decimal
    taxable_rmd: Decimal
    destination_account_id: str | None


class AnnualRmdOwnerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    gross_rmd: Decimal
    qcd: Decimal
    taxable_rmd: Decimal
    destination_account_id: str | None
    accounts: tuple[AnnualRmdAccountResult, ...]


class AnnualRmdQcdResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    year: int
    rule_dataset_id: str
    configured_qcd_target: Decimal
    gross_rmd: Decimal
    qcd: Decimal
    taxable_rmd: Decimal
    qcd_capacity_shortfall: Decimal
    owners: tuple[AnnualRmdOwnerResult, ...]
    warnings: tuple[str, ...] = ()


class AnnualSocialSecurityBenefit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str
    owner_id: str
    benefit_subtype: SocialSecurityBenefitSubtype | None
    monthly_benefit: Decimal
    months_received: int
    gross_benefit: Decimal


class SocialSecurityTaxationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provisional_income_scope: Literal["currently_modeled_supported_sources_only"] = (
        "currently_modeled_supported_sources_only"
    )
    gross_social_security: Decimal
    half_social_security: Decimal
    other_provisional_income: Decimal
    provisional_income: Decimal
    base_amount: Decimal
    adjusted_base_amount: Decimal
    taxable_social_security: Decimal
    maximum_taxable_social_security: Decimal


class FederalBracketTax(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower_bound: Decimal
    upper_bound: Decimal | None
    rate: Decimal
    income_taxed: Decimal
    tax: Decimal


class FederalMarginalBracket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    lower_bound: Decimal
    upper_bound: Decimal | None
    rate: Decimal


class FederalIncomeTaxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    gross_income: Decimal
    standard_deduction: Decimal
    taxable_income: Decimal
    tax_by_bracket: tuple[FederalBracketTax, ...]
    total_federal_tax: Decimal
    marginal_bracket: FederalMarginalBracket | None


class MissouriOwnerTaxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    missouri_agi: Decimal
    income_percentage: Decimal
    taxable_income: Decimal
    tax: Decimal


class MissouriTaxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: str
    dataset_version: str
    gross_income_basis: Decimal
    social_security_subtraction: Decimal
    public_pension_subtraction: Decimal
    private_retirement_subtraction: Decimal
    federal_tax_deduction: Decimal
    standard_deduction: Decimal
    total_adjustments_and_subtractions: Decimal
    taxable_income: Decimal
    total_tax: Decimal
    owners: tuple[MissouriOwnerTaxResult, ...]


class TransactionLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    transaction_id: str
    year: int
    transaction_type: TransactionType
    amount: NonNegativeMoney
    source_account_id: str | None = None
    destination_account_id: str | None = None
    charitable_method: CharitableGivingMethod | None = None
    spendable_income: Decimal = Decimal("0")
    cash_withdrawal: Decimal = Decimal("0")
    spending: Decimal = Decimal("0")
    contribution: Decimal = Decimal("0")
    federal_tax_payment: Decimal = Decimal("0")
    taxable_ordinary_income: Decimal = Decimal("0")
    missouri_tax_payment: Decimal = Decimal("0")
    taxable_amount: Decimal | None = None
    roth_conversion_method: RothConversionMethod | None = None


class ProjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_version: str
    plan_schema_version: str
    scenario_id: str
    annual_accounts: list[AnnualAccountResult]
    annual_household: list[AnnualHouseholdResult]
    transactions: list[TransactionLedgerEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)
