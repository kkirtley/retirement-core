from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    FilingStatus,
    IncomeStopRule,
    IncomeType,
    PensionType,
    QcdAllocationMethod,
    QcdTargetMode,
    ResidencyStatus,
    RmdFirstPaymentTiming,
    RmdObligationGroupType,
    RothConversionMethod,
    SocialSecurityBenefitSubtype,
    TaxableRmdAllocationMethod,
    TransactionType,
    WorkplacePlanStatus,
    WorkplaceRmdTimingRule,
)
from retirement_core.domain.federal_liability import FederalTaxLiabilityResult
from retirement_core.domain.medicare import AnnualIrmaaResult, MedicarePlanInput
from retirement_core.domain.tax import AnnualFederalAgiResult

NonNegativeMoney = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=1)]
InvestmentReturnRate = Annotated[Decimal, Field(ge=-1)]


class PersonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    date_of_birth: date
    retirement_date: date | None = None


class WorkplacePlanRmdInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    employer_status: WorkplacePlanStatus
    rmd_timing_rule: WorkplaceRmdTimingRule
    is_five_percent_owner: bool | None = None
    employment_end_date: date | None = None
    taxable_rmd_destination_account_id: str | None = None
    first_rmd_payment_timing: RmdFirstPaymentTiming = RmdFirstPaymentTiming.DISTRIBUTION_YEAR

    @model_validator(mode="after")
    def validate_timing_configuration(self) -> WorkplacePlanRmdInput:
        if self.rmd_timing_rule is WorkplaceRmdTimingRule.LATER_OF_RETIREMENT:
            if self.employer_status is not WorkplacePlanStatus.CURRENT_EMPLOYER:
                raise ValueError("LATER_OF_RETIREMENT requires CURRENT_EMPLOYER status")
            if self.is_five_percent_owner is not False:
                raise ValueError("LATER_OF_RETIREMENT requires is_five_percent_owner=false")
        if (
            self.is_five_percent_owner is True
            and self.rmd_timing_rule is not WorkplaceRmdTimingRule.STANDARD_STATUTORY_AGE
        ):
            raise ValueError("A 5% owner must use STANDARD_STATUTORY_AGE")
        if (
            self.employer_status is WorkplacePlanStatus.FORMER_EMPLOYER
            and self.rmd_timing_rule is not WorkplaceRmdTimingRule.STANDARD_STATUTORY_AGE
        ):
            raise ValueError("FORMER_EMPLOYER plans must use STANDARD_STATUTORY_AGE")
        return self


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    owner_id: str
    account_type: AccountType
    starting_balance: NonNegativeMoney
    annual_return: InvestmentReturnRate = Decimal("0")
    annual_return_overrides: dict[int, InvestmentReturnRate] = Field(default_factory=dict)
    workplace_plan_rmd: WorkplacePlanRmdInput | None = None

    @model_validator(mode="after")
    def validate_workplace_plan_rmd(self) -> AccountInput:
        if (
            self.workplace_plan_rmd is not None
            and self.account_type is not AccountType.TRADITIONAL_401K
        ):
            raise ValueError("workplace_plan_rmd is only valid for TRADITIONAL_401K accounts")
        return self


class SocialSecurityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    owner_id: str
    benefit_subtype: SocialSecurityBenefitSubtype | None = None
    claim_date: date
    monthly_benefit: NonNegativeMoney
    annual_cola: Percent = Decimal("0")
    destination_account_id: str


class AssumptionSource(BaseModel):
    """Lightweight provenance for household-provided planning assumptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: str | None = None
    description: str | None = None
    as_of_date: date | None = None


class AnnualIncomeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    taxable_amount: NonNegativeMoney
    spendable_cash_amount: NonNegativeMoney
    federal_income_tax_withholding: NonNegativeMoney = Decimal("0")
    state_income_tax_withholding: NonNegativeMoney = Decimal("0")
    payroll_deductions_embedded_in_cash: str | None = None
    assumption_source: AssumptionSource | None = None


class ResolvedAnnualIncome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    income_id: str
    year: int
    owner_id: str | None = None
    income_type: IncomeType
    taxable_amount: Decimal
    spendable_cash_amount: Decimal
    federal_income_tax_withholding: Decimal
    state_income_tax_withholding: Decimal
    payroll_deductions_embedded_in_cash: str | None = None
    assumption_source: AssumptionSource | None = None
    destination_account_id: str


class IncomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    income_type: IncomeType = IncomeType.UNSPECIFIED
    pension_type: PensionType | None = None
    owner_id: str | None = None
    annual_amount: NonNegativeMoney | None = None
    annual_taxable_amount: NonNegativeMoney | None = None
    annual_spendable_cash_amount: NonNegativeMoney | None = None
    annual_federal_income_tax_withholding: NonNegativeMoney = Decimal("0")
    annual_state_income_tax_withholding: NonNegativeMoney = Decimal("0")
    payroll_deductions_embedded_in_cash: str | None = None
    assumption_source: AssumptionSource | None = None
    annual_overrides: dict[int, AnnualIncomeOverride] = Field(default_factory=dict)
    start_date: date
    stop_rule: IncomeStopRule | None = None
    end_date: date | None = None
    destination_account_id: str | None = None
    taxable_federal: bool | None = None
    taxable_state: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_income(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        income_type = data.get("income_type", IncomeType.UNSPECIFIED)
        if data.get("stop_rule") is None:
            data["stop_rule"] = (
                IncomeStopRule.EXPLICIT_END_DATE
                if data.get("end_date") is not None
                else IncomeStopRule.CONTINUES_FOR_LIFE
            )
        if income_type in {
            IncomeType.PENSION.value,
            IncomeType.PENSION,
            IncomeType.W2_WAGES.value,
            IncomeType.W2_WAGES,
        }:
            data.setdefault("taxable_federal", True)
            data.setdefault("taxable_state", True)
        elif income_type in {IncomeType.VA_DISABILITY.value, IncomeType.VA_DISABILITY}:
            data.setdefault("taxable_federal", False)
            data.setdefault("taxable_state", False)
        else:
            data.setdefault("taxable_federal", True)
            data.setdefault("taxable_state", True)
        legacy_amount = data.get("annual_amount")
        if legacy_amount is not None:
            if income_type == IncomeType.TAX_EXEMPT_INTEREST.value:
                data.setdefault("annual_taxable_amount", "0")
                data.setdefault("annual_spendable_cash_amount", legacy_amount)
            elif income_type not in {IncomeType.W2_WAGES.value, IncomeType.VA_DISABILITY.value}:
                data.setdefault(
                    "annual_taxable_amount",
                    legacy_amount if data["taxable_federal"] else "0",
                )
                data.setdefault("annual_spendable_cash_amount", legacy_amount)
        return data

    @model_validator(mode="after")
    def validate_income_type(self) -> IncomeInput:
        if self.stop_rule is IncomeStopRule.EXPLICIT_END_DATE and self.end_date is None:
            raise ValueError("EXPLICIT_END_DATE requires end_date")
        if self.stop_rule is IncomeStopRule.CONTINUES_FOR_LIFE and self.end_date is not None:
            raise ValueError("CONTINUES_FOR_LIFE forbids end_date")
        if self.annual_taxable_amount is None or self.annual_spendable_cash_amount is None:
            raise ValueError(f"Income {self.id} requires taxable and spendable annual amounts")
        if self.income_type is IncomeType.W2_WAGES and (
            self.taxable_federal is not True or self.taxable_state is not True
        ):
            raise ValueError("W2_WAGES must be federally and Missouri taxable")
        if self.income_type is IncomeType.VA_DISABILITY:
            if self.taxable_federal is not False or self.taxable_state is not False:
                raise ValueError("VA_DISABILITY must be federally and Missouri exempt")
            if self.annual_taxable_amount != 0:
                raise ValueError("VA_DISABILITY taxable amount must be zero")
            if (
                self.annual_federal_income_tax_withholding != 0
                or self.annual_state_income_tax_withholding != 0
            ):
                raise ValueError("VA_DISABILITY withholding must be zero")
            for year, override in self.annual_overrides.items():
                if override.taxable_amount != 0:
                    raise ValueError(
                        f"VA_DISABILITY override for {year} taxable amount must be zero"
                    )
                if (
                    override.federal_income_tax_withholding != 0
                    or override.state_income_tax_withholding != 0
                ):
                    raise ValueError(f"VA_DISABILITY override for {year} withholding must be zero")
        return self


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
    income_taxable_amount: NonNegativeMoney | None = None
    federal_income_tax_withholding: NonNegativeMoney = Decimal("0")
    state_income_tax_withholding: NonNegativeMoney = Decimal("0")
    payroll_deductions_embedded_in_cash: str | None = None
    assumption_source: AssumptionSource | None = None
    rmd_obligation_group_id: str | None = None
    rmd_obligation_group_type: RmdObligationGroupType | None = None

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

    @model_validator(mode="after")
    def validate_plan_schedule(self) -> PlanInput:
        people = {person.id: person for person in self.people}
        for account in self.accounts:
            invalid_years = sorted(
                year
                for year in account.annual_return_overrides
                if year < self.start_date.year or year > self.end_date.year
            )
            if invalid_years:
                raise ValueError(
                    f"Account {account.id} annual_return_overrides outside projection range: "
                    f"{', '.join(str(year) for year in invalid_years)}"
                )
        for income in self.income:
            if income.stop_rule is IncomeStopRule.OWNER_RETIREMENT_DATE:
                owner = people.get(income.owner_id or "")
                if owner is None or owner.retirement_date is None:
                    raise ValueError(
                        f"Income {income.id} OWNER_RETIREMENT_DATE requires "
                        "an owner retirement_date"
                    )
            if (
                (self.start_date.month != 1 or self.start_date.day != 1)
                and income.start_date < self.start_date
                and _income_active_on(income, self.start_date, people)
                and self.start_date.year not in income.annual_overrides
            ):
                raise ValueError(
                    f"Income {income.id} requires an annual override for partial "
                    f"first projection year {self.start_date.year}"
                )
        return self


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
    growth_period_start: date | None = None
    growth_period_end: date | None = None
    growth_fraction: Decimal | None = None
    annual_return_applied: Decimal | None = None
    annual_return_source: Literal["default", "annual_override"] | None = None
    ending_balance: Decimal


class AnnualHouseholdResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    gross_income: Decimal
    taxes: Decimal = Field(
        description="Additional cash tax payments only; excludes withholding and refunds."
    )
    total_federal_liability: Decimal = Decimal("0")
    total_missouri_liability: Decimal = Decimal("0")
    federal_withholding: Decimal = Decimal("0")
    missouri_withholding: Decimal = Decimal("0")
    federal_tax_payment: Decimal = Decimal("0")
    missouri_tax_payment: Decimal = Decimal("0")
    federal_tax_refund: Decimal = Decimal("0")
    missouri_tax_refund: Decimal = Decimal("0")
    after_tax_income: Decimal
    giving_target: Decimal
    spending: Decimal
    contributions: Decimal = Decimal("0")
    cash_withdrawals: Decimal = Decimal("0")
    medicare_costs: Decimal = Decimal("0")
    cash_surplus: Decimal
    federal_agi_result: AnnualFederalAgiResult | None = None
    federal_tax_result: FederalIncomeTaxResult | None = None
    federal_tax_liability_result: FederalTaxLiabilityResult | None = None
    social_security_benefits: tuple[AnnualSocialSecurityBenefit, ...] = ()
    social_security_taxation: SocialSecurityTaxationResult | None = None
    rmd_qcd_result: AnnualRmdQcdResult | None = None
    missouri_tax_result: MissouriTaxResult | None = None
    irmaa_result: AnnualIrmaaResult | None = None
    resolved_income: tuple[ResolvedAnnualIncome, ...] = ()


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
    gross_ira_rmd: Decimal = Decimal("0")
    taxable_ira_rmd: Decimal = Decimal("0")
    gross_traditional_401k_rmd: Decimal = Decimal("0")
    taxable_traditional_401k_rmd: Decimal = Decimal("0")
    aggregate_gross_rmd: Decimal = Decimal("0")
    aggregate_taxable_rmd: Decimal = Decimal("0")
    obligation_groups: tuple[AnnualRmdObligationGroupResult, ...] = ()


class AnnualRmdObligationGroupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    group_id: str
    group_type: RmdObligationGroupType
    owner_id: str
    account_ids: tuple[str, ...]
    prior_year_end_balances: dict[str, Decimal]
    balance_date: date
    distribution_year: int | None
    payment_deadline: date | None
    divisor: Decimal | None
    required_amount: Decimal
    taxable_amount: Decimal
    rule_dataset_id: str
    timing_rule: str
    timing_provenance: str


class AnnualSocialSecurityBenefit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str
    owner_id: str
    benefit_subtype: SocialSecurityBenefitSubtype | None
    monthly_benefit: Decimal
    months_received: int
    gross_benefit: Decimal
    benefit_period_start: date | None = None
    benefit_period_end: date | None = None
    applied_cola_years: int = 0


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
    federal_tax_refund: Decimal = Decimal("0")
    federal_income_tax_withholding: Decimal = Decimal("0")
    taxable_ordinary_income: Decimal = Decimal("0")
    missouri_tax_payment: Decimal = Decimal("0")
    missouri_tax_refund: Decimal = Decimal("0")
    state_income_tax_withholding: Decimal = Decimal("0")
    medicare_payment: Decimal = Decimal("0")
    taxable_amount: Decimal | None = None
    roth_conversion_method: RothConversionMethod | None = None
    rmd_obligation_group_id: str | None = None
    rmd_obligation_group_type: RmdObligationGroupType | None = None


def _income_active_on(
    income: IncomeInput,
    on_date: date,
    people: dict[str, PersonInput],
) -> bool:
    if on_date < income.start_date:
        return False
    if income.stop_rule is IncomeStopRule.EXPLICIT_END_DATE:
        return income.end_date is not None and on_date <= income.end_date
    if income.stop_rule is IncomeStopRule.OWNER_RETIREMENT_DATE:
        owner = people.get(income.owner_id or "")
        return (
            owner is not None
            and owner.retirement_date is not None
            and on_date <= owner.retirement_date
        )
    return True


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
