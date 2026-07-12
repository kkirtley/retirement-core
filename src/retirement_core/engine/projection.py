from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from retirement_core import __version__
from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    FilingStatus,
    IncomeType,
    PensionType,
    SocialSecurityBenefitSubtype,
    TaxableRmdAllocationMethod,
    TransactionType,
)
from retirement_core.domain.models import (
    AccountInput,
    AnnualAccountResult,
    AnnualHouseholdResult,
    AnnualRmdAccountResult,
    AnnualRmdOwnerResult,
    AnnualRmdQcdResult,
    AnnualSocialSecurityBenefit,
    AnnualTransactionInput,
    FederalIncomeTaxResult,
    MissouriTaxResult,
    ProjectionRequest,
    ProjectionResult,
    SocialSecurityTaxationResult,
    TransactionLedgerEntry,
)
from retirement_core.engine.federal_tax import calculate_federal_income_tax
from retirement_core.engine.ledger import (
    calculate_growth,
    reconcile_account,
    reconcile_household_cash,
)
from retirement_core.engine.missouri_tax import MissouriOwnerIncome, calculate_missouri_income_tax
from retirement_core.engine.rmd_qcd import (
    AccountRmdResult,
    RmdAccountInput,
    RmdOwnerInput,
    calculate_qcd,
    calculate_rmd,
)
from retirement_core.engine.social_security_tax import calculate_taxable_social_security
from retirement_core.engine.transactions import AccountActivity, apply_transaction
from retirement_core.rules.missouri_tax import MissouriTaxRules
from retirement_core.rules.models import FederalTaxRules
from retirement_core.rules.rmd_qcd import RmdQcdRules

_PRETAX_ACCOUNT_TYPES = {AccountType.TRADITIONAL_IRA, AccountType.TRADITIONAL_401K}
_CENT = Decimal("0.01")


def run_projection(
    request: ProjectionRequest,
    federal_tax_rules: FederalTaxRules | None = None,
    rmd_qcd_rules_by_year: dict[int, RmdQcdRules] | None = None,
    missouri_tax_rules_by_year: dict[int, MissouriTaxRules] | None = None,
) -> ProjectionResult:
    plan = request.plan
    accounts = {account.id: account for account in plan.accounts}
    if len(accounts) != len(plan.accounts):
        raise ValueError("Account IDs must be unique")

    balances = {account.id: account.starting_balance for account in plan.accounts}
    annual_accounts: list[AnnualAccountResult] = []
    annual_household: list[AnnualHouseholdResult] = []
    ledger_entries: list[TransactionLedgerEntry] = []
    rmd_qcd_rules_by_year = rmd_qcd_rules_by_year or {}
    missouri_tax_rules_by_year = missouri_tax_rules_by_year or {}

    first_year = plan.start_date.year
    last_year = plan.end_date.year
    invalid_years = [
        transaction.id
        for transaction in plan.transactions
        if not first_year <= transaction.year <= last_year
    ]
    if invalid_years:
        raise ValueError(f"Transactions outside the projection period: {', '.join(invalid_years)}")
    _validate_projection_generated_transaction_types(plan.transactions)

    for year in range(first_year, last_year + 1):
        beginning_balances = balances.copy()
        growth_by_account: dict[str, Decimal] = {}
        activity = {account_id: AccountActivity() for account_id in accounts}

        # Temporary deterministic timing convention: all annual growth is applied to
        # beginning-of-year balances before any transaction for that year is applied.
        for account_id, account in accounts.items():
            growth = calculate_growth(beginning_balances[account_id], account.annual_return)
            growth_by_account[account_id] = growth
            balances[account_id] += growth

        rmd_qcd_result: AnnualRmdQcdResult | None = None
        year_entries: list[TransactionLedgerEntry] = []
        if _requires_rmd_qcd(plan.people, plan.accounts):
            try:
                rmd_qcd_rules = rmd_qcd_rules_by_year[year]
            except KeyError as error:
                raise ValueError(
                    f"No applicable RMD/QCD rule dataset exists for projection year {year}"
                ) from error
            rmd_qcd_result, generated_entries = _apply_annual_rmd_qcd(
                request,
                year,
                beginning_balances,
                accounts,
                balances,
                activity,
                rmd_qcd_rules,
            )
            year_entries.extend(generated_entries)
            ledger_entries.extend(generated_entries)

        plan_transactions = [
            transaction for transaction in plan.transactions if transaction.year == year
        ]
        if year == 2026:
            _validate_2026_transaction_tax_treatment(plan_transactions, accounts)

        social_security_benefits, social_security_transactions = _social_security_transactions(
            request, year
        )
        annual_transactions = _income_transactions(request, year)
        annual_transactions.extend(social_security_transactions)
        annual_transactions.extend(plan_transactions)
        for transaction in annual_transactions:
            entry = apply_transaction(
                transaction,
                accounts,
                balances,
                activity,
                allow_negative_cash_balance=plan.allow_negative_cash_balance,
            )
            year_entries.append(entry)
            ledger_entries.append(entry)

        federal_tax_result, social_security_taxation = _calculate_annual_federal_tax(
            request,
            year,
            plan_transactions,
            sum((entry.taxable_ordinary_income for entry in year_entries), Decimal("0")),
            sum(
                (benefit.gross_benefit for benefit in social_security_benefits),
                Decimal("0"),
            ),
            federal_tax_rules,
        )
        missouri_tax_result = _calculate_annual_missouri_tax(
            request,
            year,
            plan_transactions,
            social_security_benefits,
            social_security_taxation,
            rmd_qcd_result,
            federal_tax_result,
            missouri_tax_rules_by_year.get(year),
        )
        if federal_tax_result is not None and federal_tax_result.total_federal_tax > 0:
            payment_account_id = plan.federal_tax_payment_account_id
            if payment_account_id is None:
                raise ValueError("A federal_tax_payment_account_id is required for 2026 tax")
            tax_payment = AnnualTransactionInput(
                id=f"federal-tax:{year}",
                year=year,
                transaction_type=TransactionType.FEDERAL_TAX_PAYMENT,
                amount=federal_tax_result.total_federal_tax,
                source_account_id=payment_account_id,
            )
            entry = apply_transaction(
                tax_payment,
                accounts,
                balances,
                activity,
                allow_negative_cash_balance=plan.allow_negative_cash_balance,
            )
            year_entries.append(entry)
            ledger_entries.append(entry)
        if missouri_tax_result is not None and missouri_tax_result.total_tax > 0:
            payment_account_id = plan.missouri_tax_payment_account_id
            if payment_account_id is None:
                raise ValueError("A missouri_tax_payment_account_id is required")
            payment = AnnualTransactionInput(
                id=f"missouri-tax:{year}",
                year=year,
                transaction_type=TransactionType.MISSOURI_TAX_PAYMENT,
                amount=missouri_tax_result.total_tax,
                source_account_id=payment_account_id,
            )
            entry = apply_transaction(
                payment,
                accounts,
                balances,
                activity,
                allow_negative_cash_balance=plan.allow_negative_cash_balance,
            )
            year_entries.append(entry)
            ledger_entries.append(entry)

        for account_id in accounts:
            account_activity = activity[account_id]
            row = AnnualAccountResult(
                year=year,
                account_id=account_id,
                beginning_balance=beginning_balances[account_id],
                investment_return=growth_by_account[account_id],
                contributions=account_activity.contributions,
                transfers_in=account_activity.transfers_in,
                withdrawals=account_activity.withdrawals,
                transfers_out=account_activity.transfers_out,
                roth_conversions=account_activity.roth_conversions,
                qcd=account_activity.qcd,
                ending_balance=balances[account_id],
            )
            reconcile_account(row)
            annual_accounts.append(row)

        spendable_income = sum((entry.spendable_income for entry in year_entries), Decimal("0"))
        cash_withdrawals = sum((entry.cash_withdrawal for entry in year_entries), Decimal("0"))
        spending = sum((entry.spending for entry in year_entries), Decimal("0"))
        contributions = sum((entry.contribution for entry in year_entries), Decimal("0"))
        federal_tax = sum((entry.federal_tax_payment for entry in year_entries), Decimal("0"))
        missouri_tax = sum((entry.missouri_tax_payment for entry in year_entries), Decimal("0"))
        cash_surplus = (
            spendable_income
            + cash_withdrawals
            - spending
            - contributions
            - federal_tax
            - missouri_tax
        )
        reconcile_household_cash(
            spendable_income,
            cash_withdrawals,
            spending,
            contributions,
            cash_surplus,
            federal_tax=federal_tax,
            missouri_tax=missouri_tax,
        )

        total_taxes = federal_tax + missouri_tax
        after_tax = spendable_income - total_taxes
        giving_target = (
            max(after_tax, Decimal("0")) * plan.giving_policy.target_rate_after_tax_income
        ).quantize(Decimal("0.01"))
        annual_household.append(
            AnnualHouseholdResult(
                year=year,
                gross_income=spendable_income,
                taxes=total_taxes,
                after_tax_income=after_tax,
                giving_target=giving_target,
                spending=spending,
                contributions=contributions,
                cash_withdrawals=cash_withdrawals,
                cash_surplus=cash_surplus,
                federal_tax_result=federal_tax_result,
                social_security_benefits=tuple(social_security_benefits),
                social_security_taxation=social_security_taxation,
                rmd_qcd_result=rmd_qcd_result,
                missouri_tax_result=missouri_tax_result,
            )
        )

    provenance = {
        "rules_mode": "external_versioned_datasets",
        "transaction_timing": "beginning_balance_growth_then_transactions_then_tax",
    }
    if federal_tax_rules is not None and first_year <= 2026 <= last_year:
        provenance["federal_tax_dataset_id"] = federal_tax_rules.dataset_id
    for year, rules in sorted(rmd_qcd_rules_by_year.items()):
        if first_year <= year <= last_year:
            provenance[f"rmd_qcd_dataset_id:{year}"] = rules.dataset_id
    for year, missouri_rules in sorted(missouri_tax_rules_by_year.items()):
        if first_year <= year <= last_year:
            provenance[f"missouri_tax_dataset_id:{year}"] = missouri_rules.dataset_id

    return ProjectionResult(
        engine_version=__version__,
        plan_schema_version=plan.schema_version,
        scenario_id=request.options.scenario_id,
        annual_accounts=annual_accounts,
        annual_household=annual_household,
        transactions=ledger_entries,
        warnings=[
            "Federal tax is limited to 2026 MFJ ordinary pension income, Roth conversions, "
            "taxable Social Security, and generated taxable RMDs from modeled sources. "
            "Missouri tax uses a projected 2026 return rate based on the official withholding "
            "formula. Medicare, IRMAA, other-state tax, inherited-IRA, and survivor engines "
            "are not implemented."
        ],
        provenance=provenance,
    )


def _requires_rmd_qcd(people: Sequence[object], accounts: list[AccountInput]) -> bool:
    return bool(people) and any(
        account.account_type in _PRETAX_ACCOUNT_TYPES for account in accounts
    )


def _apply_annual_rmd_qcd(
    request: ProjectionRequest,
    year: int,
    prior_year_end_balances: dict[str, Decimal],
    accounts: dict[str, AccountInput],
    balances: dict[str, Decimal],
    activity: dict[str, AccountActivity],
    rules: RmdQcdRules,
) -> tuple[AnnualRmdQcdResult, list[TransactionLedgerEntry]]:
    if not (
        rules.effective_from <= date(year, 12, 31)
        and (rules.effective_to is None or date(year, 12, 31) <= rules.effective_to)
    ):
        raise ValueError(f"RMD/QCD dataset {rules.dataset_id} is not effective for {year}")
    owner_inputs = tuple(
        RmdOwnerInput(owner_id=person.id, date_of_birth=person.date_of_birth)
        for person in request.plan.people
    )
    account_inputs = tuple(
        RmdAccountInput(
            account_id=account.id,
            owner_id=account.owner_id,
            account_type=account.account_type,
            prior_year_end_balance=prior_year_end_balances[account.id],
        )
        for account in request.plan.accounts
    )
    rmd = calculate_rmd(year, owner_inputs, account_inputs, rules)
    qcd = calculate_qcd(
        request.plan.giving_policy.qcd_policy, owner_inputs, account_inputs, rmd, rules
    )
    entries: list[TransactionLedgerEntry] = []
    qcd_by_account: dict[str, Decimal] = {}
    for owner_result in qcd.owners:
        for allocation in owner_result.accounts:
            transaction = AnnualTransactionInput(
                id=f"qcd:{owner_result.owner_id}:{allocation.account_id}:{year}",
                year=year,
                transaction_type=TransactionType.CHARITABLE_GIVING,
                amount=allocation.amount,
                source_account_id=allocation.account_id,
                charitable_method=CharitableGivingMethod.QCD,
            )
            entry = apply_transaction(
                transaction,
                accounts,
                balances,
                activity,
                allow_negative_cash_balance=request.plan.allow_negative_cash_balance,
            )
            if not rules.tax_treatment.qcd_excluded_from_ordinary_income:
                entry = entry.model_copy(update={"taxable_ordinary_income": allocation.amount})
            entries.append(entry)
            qcd_by_account[allocation.account_id] = (
                qcd_by_account.get(allocation.account_id, Decimal("0")) + allocation.amount
            )

    qcd_by_owner = {item.owner_id: item for item in qcd.owners}
    annual_owners: list[AnnualRmdOwnerResult] = []
    for owner_rmd in rmd.owners:
        owner_qcd = qcd_by_owner[owner_rmd.owner_id]
        destination_id = request.plan.taxable_rmd_destination_account_by_owner.get(
            owner_rmd.owner_id
        )
        if owner_qcd.remaining_taxable_rmd > 0 and destination_id is None:
            raise ValueError(
                f"Owner {owner_rmd.owner_id} requires an explicit taxable RMD destination"
            )
        source_policy = request.plan.taxable_rmd_source_policy
        if owner_qcd.remaining_taxable_rmd > 0 and source_policy is None:
            raise ValueError(
                f"Owner {owner_rmd.owner_id} requires an explicit taxable RMD source policy"
            )
        eligible_account_results = [item for item in owner_rmd.accounts if item.eligible]
        taxable_by_account = (
            _allocate_taxable_rmd_sources(
                year,
                owner_rmd.owner_id,
                owner_qcd.remaining_taxable_rmd,
                eligible_account_results,
                balances,
                source_policy.allocation_method,
                source_policy.account_priority,
                source_policy.explicit_account_amounts,
            )
            if source_policy is not None
            else {item.account_id: Decimal("0") for item in eligible_account_results}
        )
        for account_id, amount in taxable_by_account.items():
            if amount == 0:
                continue
            transaction = AnnualTransactionInput(
                id=f"taxable-rmd:{owner_rmd.owner_id}:{account_id}:{year}",
                year=year,
                transaction_type=TransactionType.RMD_DISTRIBUTION,
                amount=amount,
                source_account_id=account_id,
                destination_account_id=destination_id,
            )
            entry = apply_transaction(
                transaction,
                accounts,
                balances,
                activity,
                allow_negative_cash_balance=request.plan.allow_negative_cash_balance,
            )
            if not rules.tax_treatment.gross_rmd_is_ordinary_income:
                entry = entry.model_copy(update={"taxable_ordinary_income": Decimal("0")})
            entries.append(entry)
        reported_account_results = [
            item
            for item in owner_rmd.accounts
            if item.eligible or qcd_by_account.get(item.account_id, Decimal("0")) > 0
        ]
        annual_accounts = tuple(
            AnnualRmdAccountResult(
                owner_id=owner_rmd.owner_id,
                source_account_id=item.account_id,
                prior_year_end_balance=prior_year_end_balances[item.account_id],
                divisor=item.divisor,
                gross_rmd=item.required_minimum_distribution,
                qcd=qcd_by_account.get(item.account_id, Decimal("0")),
                taxable_rmd=taxable_by_account.get(item.account_id, Decimal("0")),
                destination_account_id=(
                    destination_id
                    if taxable_by_account.get(item.account_id, Decimal("0")) > 0
                    else None
                ),
            )
            for item in reported_account_results
        )
        annual_owners.append(
            AnnualRmdOwnerResult(
                owner_id=owner_rmd.owner_id,
                gross_rmd=owner_rmd.required_minimum_distribution,
                qcd=owner_qcd.actual_qcd,
                taxable_rmd=owner_qcd.remaining_taxable_rmd,
                destination_account_id=(
                    destination_id if owner_qcd.remaining_taxable_rmd > 0 else None
                ),
                accounts=annual_accounts,
            )
        )
    warnings = (
        (f"QCD target capacity shortfall: {qcd.unmet_target}",) if qcd.unmet_target > 0 else ()
    )
    return (
        AnnualRmdQcdResult(
            year=year,
            rule_dataset_id=rules.dataset_id,
            configured_qcd_target=qcd.configured_household_target,
            gross_rmd=rmd.household_rmd,
            qcd=qcd.actual_qcd,
            taxable_rmd=qcd.remaining_taxable_rmd,
            qcd_capacity_shortfall=qcd.unmet_target,
            owners=tuple(annual_owners),
            warnings=warnings,
        ),
        entries,
    )


def _allocate_taxable_rmd_sources(
    year: int,
    owner_id: str,
    total: Decimal,
    account_results: list[AccountRmdResult],
    live_balances: dict[str, Decimal],
    method: TaxableRmdAllocationMethod,
    account_priority: list[str],
    explicit_account_amounts: dict[int, dict[str, dict[str, Decimal]]],
) -> dict[str, Decimal]:
    account_ids = [item.account_id for item in account_results]
    gross_rmd = {item.account_id: item.required_minimum_distribution for item in account_results}
    if total == 0:
        return {account_id: Decimal("0") for account_id in account_ids}
    if method is TaxableRmdAllocationMethod.EXPLICIT_ACCOUNT_AMOUNTS:
        configured = explicit_account_amounts.get(year, {}).get(owner_id)
        if configured is None:
            raise ValueError(f"Explicit taxable RMD source amounts are required for {year}")
        unknown = set(configured) - set(account_ids)
        if unknown:
            raise ValueError(
                f"Explicit taxable RMD accounts do not belong to owner {owner_id}: "
                f"{', '.join(sorted(unknown))}"
            )
        allocated = {
            account_id: configured.get(account_id, Decimal("0")) for account_id in account_ids
        }
        if sum(allocated.values(), Decimal("0")) != total:
            raise ValueError(
                f"Explicit taxable RMD source amounts for owner {owner_id} must total {total}"
            )
        _validate_live_capacity(owner_id, allocated, live_balances)
        return allocated
    if method is TaxableRmdAllocationMethod.ACCOUNT_PRIORITY:
        ordered = [account_id for account_id in account_priority if account_id in account_ids]
        if not ordered:
            raise ValueError(f"Taxable RMD account_priority is required for owner {owner_id}")
        return _allocate_sequential(total, ordered, live_balances, owner_id)
    if method is TaxableRmdAllocationMethod.STABLE_ACCOUNT_ID:
        return _allocate_sequential(total, sorted(account_ids), live_balances, owner_id)
    weights = {account_id: gross_rmd[account_id] for account_id in account_ids}
    return _allocate_proportionally(total, account_ids, weights, live_balances, owner_id)


def _allocate_sequential(
    total: Decimal,
    account_ids: list[str],
    live_balances: dict[str, Decimal],
    owner_id: str,
) -> dict[str, Decimal]:
    allocated = {account_id: Decimal("0") for account_id in account_ids}
    remaining = total
    for account_id in account_ids:
        amount = min(remaining, live_balances[account_id])
        allocated[account_id] = amount
        remaining -= amount
    if remaining > 0:
        raise ValueError(f"Insufficient eligible IRA balance for owner {owner_id} taxable RMD")
    return allocated


def _allocate_proportionally(
    total: Decimal,
    account_ids: list[str],
    weights: dict[str, Decimal],
    live_balances: dict[str, Decimal],
    owner_id: str,
) -> dict[str, Decimal]:
    allocated = {account_id: Decimal("0") for account_id in account_ids}
    remaining = total
    available = list(account_ids)
    while remaining > 0 and available:
        total_weight = sum((weights[item] for item in available), Decimal("0"))
        if total_weight == 0:
            raise ValueError(f"No account RMD weight is available for owner {owner_id}")
        before = remaining
        for index, account_id in enumerate(available):
            share = (
                remaining
                if index == len(available) - 1
                else (before * weights[account_id] / total_weight).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
            )
            capacity = live_balances[account_id] - allocated[account_id]
            amount = min(share, capacity, remaining)
            allocated[account_id] += amount
            remaining -= amount
        available = [item for item in available if allocated[item] < live_balances[item]]
    if remaining > 0:
        raise ValueError(f"Insufficient eligible IRA balance for owner {owner_id} taxable RMD")
    return allocated


def _validate_live_capacity(
    owner_id: str,
    allocated: dict[str, Decimal],
    live_balances: dict[str, Decimal],
) -> None:
    if any(amount > live_balances[account_id] for account_id, amount in allocated.items()):
        raise ValueError(f"Insufficient eligible IRA balance for owner {owner_id} taxable RMD")


def _calculate_annual_missouri_tax(
    request: ProjectionRequest,
    year: int,
    plan_transactions: list[AnnualTransactionInput],
    social_security_benefits: list[AnnualSocialSecurityBenefit],
    social_security_taxation: SocialSecurityTaxationResult | None,
    rmd_qcd_result: AnnualRmdQcdResult | None,
    federal_tax_result: FederalIncomeTaxResult | None,
    rules: MissouriTaxRules | None,
) -> MissouriTaxResult | None:
    residency = request.plan.state_residency
    if residency is None or residency.state_code != "MO":
        return None
    if residency.status.value != "full_year_resident":
        raise ValueError("Only full-year Missouri residency is implemented")
    if request.plan.filing_status is not FilingStatus.MARRIED_FILING_JOINTLY:
        raise ValueError("Only Missouri married filing combined is implemented")
    if rules is None:
        raise ValueError(f"No applicable Missouri tax rule dataset exists for {year}")
    if federal_tax_result is None:
        raise ValueError("Missouri tax requires a federal tax result")
    people = {person.id: person for person in request.plan.people}
    components = {
        person.id: {
            "public": Decimal("0"),
            "private": Decimal("0"),
            "rmd": Decimal("0"),
            "gross_ss": Decimal("0"),
            "retirement_ss": Decimal("0"),
            "disability_ss": Decimal("0"),
        }
        for person in request.plan.people
    }
    for income in request.plan.income:
        if not (
            income.start_date.year <= year
            and (income.end_date is None or income.end_date.year >= year)
            and income.income_type is IncomeType.PENSION
            and income.taxable_federal
            and income.taxable_state
        ):
            continue
        if income.owner_id not in people or income.pension_type is None:
            raise ValueError(
                f"Missouri pension {income.id} requires an owner and public/private pension_type"
            )
        key = "public" if income.pension_type is PensionType.PUBLIC else "private"
        components[income.owner_id][key] += income.annual_amount
    if rmd_qcd_result is not None:
        for owner in rmd_qcd_result.owners:
            components[owner.owner_id]["rmd"] += owner.taxable_rmd
    taxable_social_security = (
        social_security_taxation.taxable_social_security
        if social_security_taxation is not None
        else Decimal("0")
    )
    total_gross_ss = sum(
        (benefit.gross_benefit for benefit in social_security_benefits), Decimal("0")
    )
    for benefit in social_security_benefits:
        if benefit.benefit_subtype not in {
            SocialSecurityBenefitSubtype.RETIREMENT,
            SocialSecurityBenefitSubtype.DISABILITY,
        }:
            raise ValueError(
                "Missouri Social Security treatment requires retirement or disability subtype"
            )
        components[benefit.owner_id]["gross_ss"] += benefit.gross_benefit
        allocated_taxable = (
            taxable_social_security * benefit.gross_benefit / total_gross_ss
            if total_gross_ss > 0
            else Decimal("0")
        )
        key = (
            "disability_ss"
            if benefit.benefit_subtype is SocialSecurityBenefitSubtype.DISABILITY
            else "retirement_ss"
        )
        components[benefit.owner_id][key] += allocated_taxable
    owners = tuple(
        MissouriOwnerIncome(
            owner_id=person.id,
            date_of_birth=person.date_of_birth,
            public_pension=components[person.id]["public"],
            private_pension=components[person.id]["private"],
            taxable_rmd=components[person.id]["rmd"],
            gross_social_security=components[person.id]["gross_ss"],
            taxable_social_security_retirement=components[person.id]["retirement_ss"],
            taxable_social_security_disability=components[person.id]["disability_ss"],
        )
        for person in request.plan.people
    )
    roth_conversions = sum(
        (
            transaction.amount
            for transaction in plan_transactions
            if transaction.transaction_type is TransactionType.ROTH_CONVERSION
        ),
        Decimal("0"),
    )
    return calculate_missouri_income_tax(
        owners,
        federal_tax_result.total_federal_tax,
        roth_conversions,
        rules,
    )


def _calculate_annual_federal_tax(
    request: ProjectionRequest,
    year: int,
    plan_transactions: list[AnnualTransactionInput],
    taxable_rmd: Decimal,
    gross_social_security: Decimal,
    federal_tax_rules: FederalTaxRules | None,
) -> tuple[FederalIncomeTaxResult | None, SocialSecurityTaxationResult | None]:
    if year != 2026:
        return None, None
    if federal_tax_rules is None:
        raise ValueError("2026 federal tax rules are required for a 2026 projection")
    if request.plan.filing_status is not FilingStatus.MARRIED_FILING_JOINTLY:
        raise ValueError("Only married-filing-jointly 2026 federal tax is implemented")

    ordinary_income = Decimal("0")
    for income in request.plan.income:
        if not (
            income.start_date.year <= year
            and (income.end_date is None or income.end_date.year >= year)
            and income.taxable_federal
        ):
            continue
        if income.income_type is not IncomeType.PENSION:
            raise ValueError(
                f"Federal tax treatment is unsupported for income {income.id} "
                f"of type {income.income_type.value}"
            )
        ordinary_income += income.annual_amount

    ordinary_income += sum(
        (
            transaction.amount
            for transaction in plan_transactions
            if transaction.transaction_type is TransactionType.ROTH_CONVERSION
        ),
        Decimal("0"),
    )
    ordinary_income += taxable_rmd
    social_security_taxation = calculate_taxable_social_security(
        gross_social_security,
        ordinary_income,
        federal_tax_rules.social_security_taxation,
    )
    federal_tax = calculate_federal_income_tax(
        ordinary_income + social_security_taxation.taxable_social_security,
        federal_tax_rules,
    )
    return federal_tax, social_security_taxation


def _validate_2026_transaction_tax_treatment(
    transactions: list[AnnualTransactionInput],
    accounts: dict[str, AccountInput],
) -> None:
    for transaction in transactions:
        if transaction.transaction_type is TransactionType.INCOME:
            raise ValueError(
                f"Federal tax treatment is unsupported for manual income transaction "
                f"{transaction.id}; use a typed scheduled income source"
            )
        if transaction.transaction_type is not TransactionType.WITHDRAWAL:
            continue
        source = accounts.get(transaction.source_account_id or "")
        if source is not None and source.account_type in _PRETAX_ACCOUNT_TYPES:
            raise ValueError(
                f"Taxable-distribution treatment is not implemented for 2026 pretax "
                f"withdrawal {transaction.id}"
            )
        if source is not None:
            raise ValueError(
                f"Federal tax treatment is unsupported for 2026 withdrawal "
                f"{transaction.id} from {source.account_type.value}"
            )


def _validate_projection_generated_transaction_types(
    transactions: list[AnnualTransactionInput],
) -> None:
    for transaction in transactions:
        if transaction.transaction_type is TransactionType.FEDERAL_TAX_PAYMENT:
            raise ValueError("Federal tax payment transactions are generated by the projection")
        if transaction.transaction_type is TransactionType.SOCIAL_SECURITY_INCOME:
            raise ValueError("Social Security income transactions are generated by the projection")
        if transaction.transaction_type is TransactionType.RMD_DISTRIBUTION:
            raise ValueError("RMD distribution transactions are generated by the projection")
        if transaction.transaction_type is TransactionType.MISSOURI_TAX_PAYMENT:
            raise ValueError("Missouri tax payment transactions are generated by the projection")
        if (
            transaction.transaction_type is TransactionType.CHARITABLE_GIVING
            and transaction.charitable_method is CharitableGivingMethod.QCD
        ):
            raise ValueError("QCD transactions are generated by the projection")


def _social_security_transactions(
    request: ProjectionRequest,
    year: int,
) -> tuple[list[AnnualSocialSecurityBenefit], list[AnnualTransactionInput]]:
    benefits: list[AnnualSocialSecurityBenefit] = []
    transactions: list[AnnualTransactionInput] = []
    for source in request.plan.social_security:
        if year < source.claim_date.year:
            continue
        if year != 2026:
            raise ValueError("Modeled Social Security projection is currently limited to 2026")
        years_after_claim = year - source.claim_date.year
        monthly_benefit = (
            source.monthly_benefit * (Decimal("1") + source.annual_cola) ** years_after_claim
        ).quantize(_CENT, rounding=ROUND_HALF_UP)
        months_received = 12 if years_after_claim > 0 else 13 - source.claim_date.month
        gross_benefit = monthly_benefit * months_received
        benefits.append(
            AnnualSocialSecurityBenefit(
                source_id=source.id,
                owner_id=source.owner_id,
                benefit_subtype=source.benefit_subtype,
                monthly_benefit=monthly_benefit,
                months_received=months_received,
                gross_benefit=gross_benefit,
            )
        )
        if gross_benefit > 0:
            transactions.append(
                AnnualTransactionInput(
                    id=f"social-security:{source.id}:{year}",
                    year=year,
                    transaction_type=TransactionType.SOCIAL_SECURITY_INCOME,
                    amount=gross_benefit,
                    destination_account_id=source.destination_account_id,
                )
            )
    return benefits, transactions


def _income_transactions(request: ProjectionRequest, year: int) -> list[AnnualTransactionInput]:
    transactions: list[AnnualTransactionInput] = []
    for income in request.plan.income:
        if income.start_date.year <= year and (
            income.end_date is None or income.end_date.year >= year
        ):
            if income.destination_account_id is None:
                raise ValueError(f"Income {income.id} requires a destination cash account")
            transactions.append(
                AnnualTransactionInput(
                    id=f"income:{income.id}:{year}",
                    year=year,
                    transaction_type=TransactionType.INCOME,
                    amount=income.annual_amount,
                    destination_account_id=income.destination_account_id,
                )
            )
    return transactions
