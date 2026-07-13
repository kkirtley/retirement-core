from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.enums import (
    AccountType,
    QcdAllocationMethod,
    QcdTargetMode,
    RmdObligationGroupType,
    WorkplacePlanStatus,
    WorkplaceRmdTimingRule,
)
from retirement_core.domain.models import QcdPolicyInput, WorkplacePlanRmdInput
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
    workplace_plan_rmd: WorkplacePlanRmdInput | None = None


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


class RmdObligationResult(BaseModel):
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
    rule_dataset_id: str
    timing_rule: str
    timing_provenance: str


class RmdObligationCalculationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    obligations: tuple[RmdObligationResult, ...]


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


def calculate_rmd_obligations(
    tax_year: int,
    owners: tuple[RmdOwnerInput, ...],
    accounts: tuple[RmdAccountInput, ...],
    rules: RmdQcdRules,
) -> RmdObligationCalculationResult:
    """Calculate compliance groups without generating transactions or applying QCDs."""
    owner_by_id = {owner.owner_id: owner for owner in owners}
    balance_date = date(tax_year - 1, 12, 31)
    obligations: list[RmdObligationResult] = []
    for owner in sorted(owners, key=lambda item: item.owner_id):
        owner_accounts = [account for account in accounts if account.owner_id == owner.owner_id]
        ira_accounts = [
            account
            for account in owner_accounts
            if _obligation_group_type(account, rules) is RmdObligationGroupType.IRA_OWNER_AGGREGATE
        ]
        if ira_accounts:
            obligations.append(_ira_obligation(tax_year, owner, ira_accounts, balance_date, rules))
        for account in sorted(owner_accounts, key=lambda item: item.account_id):
            if (
                _obligation_group_type(account, rules)
                is RmdObligationGroupType.TRADITIONAL_401K_PLAN
            ):
                obligations.append(
                    _workplace_plan_obligation(tax_year, owner, account, balance_date, rules)
                )
    unknown_owners = {account.owner_id for account in accounts} - set(owner_by_id)
    if unknown_owners:
        raise ValueError(
            f"RMD accounts reference unknown owners: {', '.join(sorted(unknown_owners))}"
        )
    return RmdObligationCalculationResult(tax_year=tax_year, obligations=tuple(obligations))


def _obligation_group_type(
    account: RmdAccountInput,
    rules: RmdQcdRules,
) -> RmdObligationGroupType | None:
    policy = rules.account_eligibility.account_policies.get(account.account_type)
    if policy is not None:
        return policy.obligation_group_type if policy.rmd_eligible else None
    if (
        account.account_type is AccountType.TRADITIONAL_IRA
        and account.account_type in rules.account_eligibility.supported_rmd_account_types
    ):
        return RmdObligationGroupType.IRA_OWNER_AGGREGATE
    if account.account_type is AccountType.TRADITIONAL_401K:
        raise ValueError(
            f"RMD/QCD dataset {rules.dataset_id} requires an explicit Traditional 401(k) "
            "account policy"
        )
    return None


def _ira_obligation(
    tax_year: int,
    owner: RmdOwnerInput,
    accounts: list[RmdAccountInput],
    balance_date: date,
    rules: RmdQcdRules,
) -> RmdObligationResult:
    first_year = _first_standard_distribution_year(owner.date_of_birth, tax_year, rules)
    due = tax_year >= first_year
    age, _ = _age_on(owner.date_of_birth, date(tax_year, 12, 31))
    divisor = _divisor_for_age(age, rules) if due else None
    required = (
        _money(
            sum(
                (_money(account.prior_year_end_balance / divisor) for account in accounts),
                ZERO,
            )
        )
        if divisor is not None
        else ZERO
    )
    return RmdObligationResult(
        group_id=f"ira:{owner.owner_id}",
        group_type=RmdObligationGroupType.IRA_OWNER_AGGREGATE,
        owner_id=owner.owner_id,
        account_ids=tuple(
            account.account_id for account in sorted(accounts, key=lambda item: item.account_id)
        ),
        prior_year_end_balances={
            account.account_id: account.prior_year_end_balance for account in accounts
        },
        balance_date=balance_date,
        distribution_year=tax_year if due else None,
        payment_deadline=_payment_deadline(tax_year, first_year) if due else None,
        divisor=divisor,
        required_amount=required,
        rule_dataset_id=rules.dataset_id,
        timing_rule="ira_standard_statutory_age",
        timing_provenance="versioned statutory RMD start-age schedule",
    )


def _workplace_plan_obligation(
    tax_year: int,
    owner: RmdOwnerInput,
    account: RmdAccountInput,
    balance_date: date,
    rules: RmdQcdRules,
) -> RmdObligationResult:
    standard_first_year = _first_standard_distribution_year(owner.date_of_birth, tax_year, rules)
    workplace = account.workplace_plan_rmd
    due = False
    first_year: int | None = None
    timing_rule = "unknown"
    provenance = "workplace-plan status is not configured"
    if tax_year >= standard_first_year and workplace is None:
        raise ValueError(
            f"Traditional 401(k) account {account.account_id} requires workplace-plan RMD status"
        )
    if workplace is not None:
        timing_rule = workplace.rmd_timing_rule.value
        if workplace.employer_status is WorkplacePlanStatus.UNKNOWN:
            if tax_year >= standard_first_year:
                raise ValueError(
                    f"Traditional 401(k) account {account.account_id} has unknown "
                    "workplace-plan status"
                )
            provenance = "workplace-plan status unknown; statutory age not yet reached"
        elif workplace.rmd_timing_rule is WorkplaceRmdTimingRule.STANDARD_STATUTORY_AGE:
            first_year = standard_first_year
            due = tax_year >= first_year
            provenance = "versioned statutory RMD start-age schedule"
        else:
            employment_end = workplace.employment_end_date
            if employment_end is None or employment_end > date(tax_year, 12, 31):
                provenance = "current-employer later-of-retirement rule; employment continues"
            else:
                first_year = max(standard_first_year, employment_end.year)
                due = tax_year >= first_year
                provenance = (
                    "current-employer later-of-retirement rule and explicit employment end date"
                )
    age, _ = _age_on(owner.date_of_birth, date(tax_year, 12, 31))
    divisor = _divisor_for_age(age, rules) if due else None
    required = _money(account.prior_year_end_balance / divisor) if divisor is not None else ZERO
    return RmdObligationResult(
        group_id=f"traditional-401k:{account.account_id}",
        group_type=RmdObligationGroupType.TRADITIONAL_401K_PLAN,
        owner_id=owner.owner_id,
        account_ids=(account.account_id,),
        prior_year_end_balances={account.account_id: account.prior_year_end_balance},
        balance_date=balance_date,
        distribution_year=tax_year if due else None,
        payment_deadline=_payment_deadline(tax_year, first_year) if due and first_year else None,
        divisor=divisor,
        required_amount=required,
        rule_dataset_id=rules.dataset_id,
        timing_rule=timing_rule,
        timing_provenance=provenance,
    )


def _first_standard_distribution_year(dob: date, tax_year: int, rules: RmdQcdRules) -> int:
    start_year = dob.year
    for year in range(start_year, tax_year + 1):
        if _meets_age(dob, date(year, 12, 31), _rmd_start_age(dob, rules)):
            return year
    return tax_year + 1


def _divisor_for_age(age: int, rules: RmdQcdRules) -> Decimal:
    divisor = rules.uniform_lifetime_table.get(age)
    if divisor is not None:
        return divisor
    max_age = max(rules.uniform_lifetime_table)
    if age > max_age:
        return rules.uniform_lifetime_table[max_age]
    raise ValueError(f"No Uniform Lifetime divisor for age {age}")


def _payment_deadline(distribution_year: int, first_distribution_year: int) -> date:
    return (
        date(distribution_year + 1, 4, 1)
        if distribution_year == first_distribution_year
        else date(distribution_year, 12, 31)
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
