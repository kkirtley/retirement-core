from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.enums import AccountType, FilingStatus

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
    owner_id: str
    claim_date: date
    monthly_benefit: NonNegativeMoney
    annual_cola: Percent = Decimal("0")


class IncomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    owner_id: str | None = None
    annual_amount: NonNegativeMoney
    start_date: date
    end_date: date | None = None
    taxable_federal: bool = True
    taxable_state: bool = True


class GivingPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_rate_after_tax_income: Percent = Decimal("0.10")
    qcd_enabled: bool = True


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
    giving_policy: GivingPolicyInput = Field(default_factory=GivingPolicyInput)
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
    beginning_balance: NonNegativeMoney
    investment_return: Decimal
    contributions: Decimal = Decimal("0")
    inbound_transfers: Decimal = Decimal("0")
    withdrawals: Decimal = Decimal("0")
    roth_conversions: Decimal = Decimal("0")
    qcd: Decimal = Decimal("0")
    ending_balance: NonNegativeMoney


class AnnualHouseholdResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    gross_income: Decimal
    taxes: Decimal
    after_tax_income: Decimal
    giving_target: Decimal
    spending: Decimal
    cash_surplus: Decimal


class ProjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_version: str
    plan_schema_version: str
    scenario_id: str
    annual_accounts: list[AnnualAccountResult]
    annual_household: list[AnnualHouseholdResult]
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)
