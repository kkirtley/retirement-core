from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    FilingStatus,
    IncomeType,
    SocialSecurityBenefitSubtype,
    TransactionType,
)

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
    owner_id: str | None = None
    annual_amount: NonNegativeMoney
    start_date: date
    end_date: date | None = None
    destination_account_id: str | None = None
    taxable_federal: bool = True
    taxable_state: bool = True


class GivingPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_rate_after_tax_income: Percent = Decimal("0.10")
    qcd_enabled: bool = True


class AnnualTransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    year: int
    transaction_type: TransactionType
    amount: NonNegativeMoney
    source_account_id: str | None = None
    destination_account_id: str | None = None
    charitable_method: CharitableGivingMethod | None = None


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
