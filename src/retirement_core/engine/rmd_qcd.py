from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.enums import AccountType, QcdAllocationMethod, QcdTargetMode
from retirement_core.domain.models import QcdPolicyInput
from retirement_core.rules.rmd_qcd import AgeThreshold, RmdQcdRules

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class RmdOwnerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    date_of_birth: date


class RmdAccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    owner_id: str
    account_type: AccountType
    prior_year_end_balance: Decimal = Field(ge=0)


class AccountRmdResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    owner_id: str
    eligible: bool
    divisor: Decimal | None
    required_minimum_distribution: Decimal


class OwnerRmdResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    attained_age: int
    required_beginning_age: AgeThreshold
    required_minimum_distribution: Decimal
    qcd_satisfiable_rmd: Decimal
    accounts: tuple[AccountRmdResult, ...]


class RmdCalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    household_rmd: Decimal
    owners: tuple[OwnerRmdResult, ...]


class AccountQcdAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    owner_id: str
    amount: Decimal


class OwnerQcdResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    eligible: bool
    policy_allocation: Decimal
    actual_qcd: Decimal
    remaining_taxable_rmd: Decimal
    accounts: tuple[AccountQcdAllocation, ...]


class QcdCalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    configured_household_target: Decimal
    actual_qcd: Decimal
    unmet_target: Decimal
    remaining_taxable_rmd: Decimal
    owners: tuple[OwnerQcdResult, ...]


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _age_on(dob: date, on_date: date) -> tuple[int, int]:
    years = on_date.year - dob.year
    months = on_date.month - dob.month
    if on_date.day < dob.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    return years, months


def _meets_age(dob: date, on_date: date, threshold: AgeThreshold) -> bool:
    return _age_on(dob, on_date) >= (threshold.years, threshold.months)


def _rmd_start_age(dob: date, rules: RmdQcdRules) -> AgeThreshold:
    for item in rules.rmd_start_age_schedule:
        if (item.birth_date_start is None or dob >= item.birth_date_start) and (
            item.birth_date_end is None or dob <= item.birth_date_end
        ):
            return item.start_age
    raise ValueError(f"No RMD start-age rule applies to birth date {dob}")


def calculate_rmd(
    tax_year: int,
    owners: tuple[RmdOwnerInput, ...],
    accounts: tuple[RmdAccountInput, ...],
    rules: RmdQcdRules,
) -> RmdCalculationResult:
    year_end = date(tax_year, 12, 31)
    results: list[OwnerRmdResult] = []
    for owner in owners:
        age, _ = _age_on(owner.date_of_birth, year_end)
        start_age = _rmd_start_age(owner.date_of_birth, rules)
        due = _meets_age(owner.date_of_birth, year_end, start_age)
        divisor = rules.uniform_lifetime_table.get(age)
        if due and divisor is None:
            max_age = max(rules.uniform_lifetime_table)
            if age > max_age:
                divisor = rules.uniform_lifetime_table[max_age]
            else:
                raise ValueError(f"No Uniform Lifetime divisor for age {age}")
        account_results: list[AccountRmdResult] = []
        total = ZERO
        qcd_satisfiable = ZERO
        for account in sorted(
            (a for a in accounts if a.owner_id == owner.owner_id), key=lambda a: a.account_id
        ):
            eligible = account.account_type in rules.account_eligibility.supported_rmd_account_types
            amount = (
                _money(account.prior_year_end_balance / divisor)
                if due and eligible and divisor
                else ZERO
            )
            account_results.append(
                AccountRmdResult(
                    account_id=account.account_id,
                    owner_id=owner.owner_id,
                    eligible=eligible,
                    divisor=divisor if due and eligible else None,
                    required_minimum_distribution=amount,
                )
            )
            total += amount
            if account.account_type in rules.account_eligibility.qcd_eligible_account_types:
                qcd_satisfiable += amount
        results.append(
            OwnerRmdResult(
                owner_id=owner.owner_id,
                attained_age=age,
                required_beginning_age=start_age,
                required_minimum_distribution=_money(total),
                qcd_satisfiable_rmd=_money(qcd_satisfiable),
                accounts=tuple(account_results),
            )
        )
    return RmdCalculationResult(
        tax_year=tax_year,
        household_rmd=_money(sum((o.required_minimum_distribution for o in results), ZERO)),
        owners=tuple(results),
    )


def _policy_target(policy: QcdPolicyInput, rmd: RmdCalculationResult) -> Decimal:
    override = policy.annual_overrides.get(rmd.tax_year)
    if not policy.enabled or rmd.tax_year in policy.paused_years or (override and override.paused):
        return ZERO
    if override and override.target_amount is not None:
        return _money(override.target_amount)
    if policy.target_mode is QcdTargetMode.FIXED_FLOOR:
        return _money(policy.annual_qcd_floor)
    if policy.target_mode is QcdTargetMode.HOUSEHOLD_RMD:
        return rmd.household_rmd
    if policy.target_mode is QcdTargetMode.MAX_OF_FLOOR_AND_HOUSEHOLD_RMD:
        return max(_money(policy.annual_qcd_floor), rmd.household_rmd)
    return ZERO


def calculate_qcd(
    policy: QcdPolicyInput,
    owners: tuple[RmdOwnerInput, ...],
    accounts: tuple[RmdAccountInput, ...],
    rmd: RmdCalculationResult,
    rules: RmdQcdRules,
    owner_exclusion_reductions: dict[str, Decimal] | None = None,
) -> QcdCalculationResult:
    target = _policy_target(policy, rmd)
    reductions = owner_exclusion_reductions or {}
    year_end = date(rmd.tax_year, 12, 31)
    owner_inputs = {owner.owner_id: owner for owner in owners}
    rmd_by_owner = {owner.owner_id: owner for owner in rmd.owners}
    eligible_accounts = [
        a
        for a in accounts
        if a.account_type in rules.account_eligibility.qcd_eligible_account_types
        and a.prior_year_end_balance > 0
    ]
    capacity: dict[str, Decimal] = {}
    eligible_owner_ids: set[str] = set()
    for owner_id, owner in owner_inputs.items():
        eligible = _meets_age(owner.date_of_birth, year_end, rules.qcd_eligibility_age)
        if eligible:
            eligible_owner_ids.add(owner_id)
        balance = sum(
            (a.prior_year_end_balance for a in eligible_accounts if a.owner_id == owner_id), ZERO
        )
        legal_limit = max(
            ZERO, rules.statutory_qcd_maximum_per_owner - reductions.get(owner_id, ZERO)
        )
        capacity[owner_id] = _money(min(balance, legal_limit)) if eligible else ZERO

    allocations = {owner_id: ZERO for owner_id in owner_inputs}
    remaining = target
    if policy.allocation_method is QcdAllocationMethod.PROPORTIONAL_TO_OWNER_RMD:
        weights = {
            oid: rmd_by_owner[oid].required_minimum_distribution if oid in rmd_by_owner else ZERO
            for oid in capacity
            if capacity[oid] > 0
        }
        total_weight = sum(weights.values(), ZERO)
        if total_weight == 0:
            weights = {oid: amount for oid, amount in capacity.items() if amount > 0}
            total_weight = sum(weights.values(), ZERO)
        if total_weight > 0:
            weighted_owner_ids = sorted(weights)
            for index, oid in enumerate(weighted_owner_ids):
                proposed = (
                    remaining
                    if index == len(weighted_owner_ids) - 1
                    else _money(target * weights[oid] / total_weight)
                )
                allocations[oid] = min(proposed, capacity[oid])
                remaining = max(ZERO, remaining - allocations[oid])
            for oid in sorted(capacity):
                extra = min(remaining, capacity[oid] - allocations[oid])
                allocations[oid] += extra
                remaining -= extra
    else:
        if policy.allocation_method is QcdAllocationMethod.OWNER_PRIORITY:
            order = list(policy.owner_priority) + sorted(set(capacity) - set(policy.owner_priority))
        else:
            account_order = list(policy.account_priority) + sorted(
                {a.account_id for a in eligible_accounts} - set(policy.account_priority)
            )
            account_owner = {a.account_id: a.owner_id for a in eligible_accounts}
            order = list(
                dict.fromkeys(account_owner[aid] for aid in account_order if aid in account_owner)
            )
        for oid in order:
            amount = min(remaining, capacity.get(oid, ZERO))
            allocations[oid] = amount
            remaining -= amount

    owner_results: list[OwnerQcdResult] = []
    for oid in sorted(owner_inputs):
        owner_amount = _money(allocations[oid])
        candidates = [a for a in eligible_accounts if a.owner_id == oid]
        if policy.allocation_method is QcdAllocationMethod.ACCOUNT_PRIORITY:
            ids = list(policy.account_priority) + sorted(
                {a.account_id for a in candidates} - set(policy.account_priority)
            )
            candidates = sorted(candidates, key=lambda a: ids.index(a.account_id))
        else:
            candidates = sorted(candidates, key=lambda a: a.account_id)
        account_allocations: list[AccountQcdAllocation] = []
        left = owner_amount
        for account in candidates:
            amount = _money(min(left, account.prior_year_end_balance))
            if amount:
                account_allocations.append(
                    AccountQcdAllocation(account_id=account.account_id, owner_id=oid, amount=amount)
                )
            left -= amount
        owner_rmd = rmd_by_owner.get(oid)
        gross_rmd = owner_rmd.required_minimum_distribution if owner_rmd else ZERO
        satisfiable = owner_rmd.qcd_satisfiable_rmd if owner_rmd else ZERO
        offset = (
            min(owner_amount, satisfiable)
            if rules.tax_treatment.qcd_counts_toward_same_owner_rmd
            else ZERO
        )
        owner_results.append(
            OwnerQcdResult(
                owner_id=oid,
                eligible=oid in eligible_owner_ids,
                policy_allocation=owner_amount,
                actual_qcd=owner_amount,
                remaining_taxable_rmd=_money(max(ZERO, gross_rmd - offset)),
                accounts=tuple(account_allocations),
            )
        )
    actual = _money(sum((o.actual_qcd for o in owner_results), ZERO))
    taxable = _money(sum((o.remaining_taxable_rmd for o in owner_results), ZERO))
    return QcdCalculationResult(
        tax_year=rmd.tax_year,
        configured_household_target=target,
        actual_qcd=actual,
        unmet_target=_money(max(ZERO, target - actual)),
        remaining_taxable_rmd=taxable,
        owners=tuple(owner_results),
    )
