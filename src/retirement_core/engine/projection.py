from calendar import isleap
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from retirement_core import __version__
from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    FilingStatus,
    IncomeStopRule,
    IncomeType,
    MedicareBasePremiumMode,
    PensionType,
    SocialSecurityBenefitSubtype,
    TaxableRmdAllocationMethod,
    TransactionType,
)
from retirement_core.domain.medicare import (
    AnnualIrmaaResult,
    IrmaaTaxRecordInput,
    MedicarePersonInput,
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
    IncomeInput,
    MissouriTaxResult,
    PlanInput,
    ProjectionRequest,
    ProjectionResult,
    ResolvedAnnualIncome,
    SocialSecurityTaxationResult,
    TransactionLedgerEntry,
)
from retirement_core.domain.tax import AnnualFederalAgiResult
from retirement_core.engine.federal_agi import (
    build_annual_federal_agi,
    supported_federal_ordinary_income,
    supported_provisional_income_before_social_security,
)
from retirement_core.engine.federal_tax import calculate_federal_income_tax
from retirement_core.engine.ledger import (
    calculate_growth,
    reconcile_account,
    reconcile_household_cash,
)
from retirement_core.engine.medicare_irmaa import (
    calculate_annual_irmaa,
    irmaa_tax_record_from_annual_agi,
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
from retirement_core.rules.models import FederalTaxRules, MedicareIrmaaRules
from retirement_core.rules.rmd_qcd import RmdQcdRules

_PRETAX_ACCOUNT_TYPES = {AccountType.TRADITIONAL_IRA, AccountType.TRADITIONAL_401K}
_CENT = Decimal("0.01")


def run_projection(
    request: ProjectionRequest,
    federal_tax_rules: FederalTaxRules | None = None,
    rmd_qcd_rules_by_year: dict[int, RmdQcdRules] | None = None,
    missouri_tax_rules_by_year: dict[int, MissouriTaxRules] | None = None,
    medicare_irmaa_rules_by_year: dict[int, MedicareIrmaaRules] | None = None,
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
    medicare_irmaa_rules_by_year = medicare_irmaa_rules_by_year or {}
    completed_irmaa_tax_records: dict[int, IrmaaTaxRecordInput] = {}

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
        growth_period_start, growth_period_end, growth_fraction = _growth_period(plan, year)

        # Temporary deterministic timing convention: all annual growth is applied to
        # beginning-of-year balances before any transaction for that year is applied.
        for account_id, account in accounts.items():
            growth = calculate_growth(
                beginning_balances[account_id], account.annual_return, growth_fraction
            )
            growth_by_account[account_id] = growth
            balances[account_id] += growth

        plan_transactions = [
            transaction for transaction in plan.transactions if transaction.year == year
        ]
        if year == 2026:
            _validate_2026_transaction_tax_treatment(plan_transactions, accounts)

        resolved_income = _resolve_annual_income(request, year)
        _raise_if_unsupported_federal_processing(
            year,
            _federal_processing_source_ids(
                request,
                year,
                plan_transactions,
                resolved_income,
                [],
                [],
            ),
        )

        rmd_qcd_result: AnnualRmdQcdResult | None = None
        year_entries: list[TransactionLedgerEntry] = []
        if _requires_rmd_qcd(plan.people, plan.accounts):
            try:
                rmd_qcd_rules = rmd_qcd_rules_by_year[year]
            except KeyError as error:
                raise ValueError(
                    f"No applicable RMD/QCD rule dataset exists for projection year {year}"
                ) from error
            _fail_for_partial_first_year_rmd(request, year, rmd_qcd_rules)
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

        social_security_benefits, social_security_transactions = _social_security_transactions(
            request, year
        )
        annual_transactions = _income_transactions(resolved_income)
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

        _raise_if_unsupported_federal_processing(
            year,
            _federal_processing_source_ids(
                request,
                year,
                plan_transactions,
                resolved_income,
                social_security_benefits,
                year_entries,
            ),
        )

        federal_agi_result, federal_tax_result, social_security_taxation = (
            _calculate_annual_federal_tax(
                request,
                year,
                plan_transactions,
                year_entries,
                resolved_income,
                social_security_benefits,
                federal_tax_rules,
            )
        )
        missouri_tax_result = _calculate_annual_missouri_tax(
            request,
            year,
            plan_transactions,
            social_security_benefits,
            social_security_taxation,
            rmd_qcd_result,
            federal_tax_result,
            resolved_income,
            missouri_tax_rules_by_year.get(year),
        )
        federal_withholding = sum(
            (income.federal_income_tax_withholding for income in resolved_income), Decimal("0")
        )
        missouri_withholding = sum(
            (income.state_income_tax_withholding for income in resolved_income), Decimal("0")
        )
        federal_liability = (
            federal_tax_result.total_federal_tax if federal_tax_result is not None else Decimal("0")
        )
        missouri_liability = (
            missouri_tax_result.total_tax if missouri_tax_result is not None else Decimal("0")
        )
        federal_settlement = federal_liability - federal_withholding
        missouri_settlement = missouri_liability - missouri_withholding
        if federal_settlement != 0:
            payment_account_id = plan.federal_tax_payment_account_id
            if payment_account_id is None:
                raise ValueError(
                    "A federal_tax_payment_account_id is required for 2026 tax settlement"
                )
            tax_payment = AnnualTransactionInput(
                id=f"federal-tax:{year}",
                year=year,
                transaction_type=(
                    TransactionType.FEDERAL_TAX_PAYMENT
                    if federal_settlement > 0
                    else TransactionType.FEDERAL_TAX_REFUND
                ),
                amount=abs(federal_settlement),
                source_account_id=payment_account_id if federal_settlement > 0 else None,
                destination_account_id=payment_account_id if federal_settlement < 0 else None,
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
        if missouri_settlement != 0:
            payment_account_id = plan.missouri_tax_payment_account_id
            if payment_account_id is None:
                raise ValueError("A missouri_tax_payment_account_id is required for tax settlement")
            payment = AnnualTransactionInput(
                id=f"missouri-tax:{year}",
                year=year,
                transaction_type=(
                    TransactionType.MISSOURI_TAX_PAYMENT
                    if missouri_settlement > 0
                    else TransactionType.MISSOURI_TAX_REFUND
                ),
                amount=abs(missouri_settlement),
                source_account_id=payment_account_id if missouri_settlement > 0 else None,
                destination_account_id=payment_account_id if missouri_settlement < 0 else None,
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

        irmaa_result = _calculate_annual_medicare(
            request,
            year,
            completed_irmaa_tax_records,
            medicare_irmaa_rules_by_year.get(year),
        )
        if irmaa_result is not None:
            medicare_entries = _apply_medicare_payments(
                request,
                year,
                accounts,
                balances,
                activity,
                irmaa_result,
            )
            year_entries.extend(medicare_entries)
            ledger_entries.extend(medicare_entries)

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
                growth_period_start=growth_period_start,
                growth_period_end=growth_period_end,
                growth_fraction=growth_fraction,
                ending_balance=balances[account_id],
            )
            reconcile_account(row)
            annual_accounts.append(row)

        spendable_income = sum((entry.spendable_income for entry in year_entries), Decimal("0"))
        cash_withdrawals = sum((entry.cash_withdrawal for entry in year_entries), Decimal("0"))
        spending = sum((entry.spending for entry in year_entries), Decimal("0"))
        contributions = sum((entry.contribution for entry in year_entries), Decimal("0"))
        federal_tax = sum((entry.federal_tax_payment for entry in year_entries), Decimal("0"))
        federal_tax_refund = sum((entry.federal_tax_refund for entry in year_entries), Decimal("0"))
        missouri_tax = sum((entry.missouri_tax_payment for entry in year_entries), Decimal("0"))
        missouri_tax_refund = sum(
            (entry.missouri_tax_refund for entry in year_entries), Decimal("0")
        )
        medicare_costs = sum((entry.medicare_payment for entry in year_entries), Decimal("0"))
        cash_surplus = (
            spendable_income
            + cash_withdrawals
            + federal_tax_refund
            + missouri_tax_refund
            - spending
            - contributions
            - federal_tax
            - missouri_tax
            - medicare_costs
        )
        reconcile_household_cash(
            spendable_income,
            cash_withdrawals,
            spending,
            contributions,
            cash_surplus,
            federal_tax=federal_tax,
            missouri_tax=missouri_tax,
            federal_tax_refunds=federal_tax_refund,
            missouri_tax_refunds=missouri_tax_refund,
            medicare_costs=medicare_costs,
        )

        total_taxes = federal_tax + missouri_tax
        after_tax = spendable_income - total_taxes + federal_tax_refund + missouri_tax_refund
        giving_target = (
            max(after_tax, Decimal("0")) * plan.giving_policy.target_rate_after_tax_income
        ).quantize(Decimal("0.01"))
        annual_household.append(
            AnnualHouseholdResult(
                year=year,
                gross_income=spendable_income,
                taxes=total_taxes,
                total_federal_liability=federal_liability,
                total_missouri_liability=missouri_liability,
                federal_withholding=federal_withholding,
                missouri_withholding=missouri_withholding,
                federal_tax_payment=federal_tax,
                missouri_tax_payment=missouri_tax,
                federal_tax_refund=federal_tax_refund,
                missouri_tax_refund=missouri_tax_refund,
                after_tax_income=after_tax,
                giving_target=giving_target,
                spending=spending,
                contributions=contributions,
                cash_withdrawals=cash_withdrawals,
                medicare_costs=medicare_costs,
                cash_surplus=cash_surplus,
                federal_agi_result=federal_agi_result,
                federal_tax_result=federal_tax_result,
                social_security_benefits=tuple(social_security_benefits),
                social_security_taxation=social_security_taxation,
                rmd_qcd_result=rmd_qcd_result,
                missouri_tax_result=missouri_tax_result,
                irmaa_result=irmaa_result,
                resolved_income=tuple(resolved_income),
            )
        )
        if federal_agi_result is not None:
            completed_irmaa_tax_records[year] = irmaa_tax_record_from_annual_agi(federal_agi_result)

    provenance = {
        "rules_mode": "external_versioned_datasets",
        "transaction_timing": "beginning_balance_growth_then_transactions_then_tax",
        "starting_balance_timing": "account starting balances are measured as of plan.start_date",
        "growth_timing": "growth is applied before generated and declared annual transactions",
        "growth_proration": (
            "full calendar years use the configured annual return; partial years use simple "
            "inclusive actual-calendar-day proration"
        ),
    }
    if federal_tax_rules is not None and first_year <= 2026 <= last_year:
        provenance["federal_tax_dataset_id"] = federal_tax_rules.dataset_id
    for year, rules in sorted(rmd_qcd_rules_by_year.items()):
        if first_year <= year <= last_year:
            provenance[f"rmd_qcd_dataset_id:{year}"] = rules.dataset_id
    for year, missouri_rules in sorted(missouri_tax_rules_by_year.items()):
        if first_year <= year <= last_year:
            provenance[f"missouri_tax_dataset_id:{year}"] = missouri_rules.dataset_id
    for year, medicare_rules in sorted(medicare_irmaa_rules_by_year.items()):
        if first_year <= year <= last_year:
            provenance[f"medicare_irmaa_dataset_id:{year}"] = medicare_rules.dataset_id

    return ProjectionResult(
        engine_version=__version__,
        plan_schema_version=plan.schema_version,
        scenario_id=request.options.scenario_id,
        annual_accounts=annual_accounts,
        annual_household=annual_household,
        transactions=ledger_entries,
        warnings=[
            "Federal tax and AGI processing is limited to 2026 MFJ modeled sources and fails "
            "closed for unsupported years with federal-processing-relevant activity. "
            "Missouri tax uses a projected 2026 return rate based on the official withholding "
            "formula. Medicare/IRMAA cash-flow integration excludes appeals, hold-harmless, "
            "late-enrollment penalties, Extra Help, survivor behavior, and automatic enrollment "
            "inference. Other-state tax, inherited-IRA, and survivor engines are not implemented."
        ],
        provenance=provenance,
    )


def _requires_rmd_qcd(people: Sequence[object], accounts: list[AccountInput]) -> bool:
    return bool(people) and any(
        account.account_type in _PRETAX_ACCOUNT_TYPES for account in accounts
    )


def _growth_period(plan: PlanInput, year: int) -> tuple[date, date, Decimal]:
    start_date = plan.start_date
    end_date = plan.end_date
    period_start = max(start_date, date(year, 1, 1))
    period_end = min(end_date, date(year, 12, 31))
    active_days = (period_end - period_start).days + 1
    days_in_year = 366 if isleap(year) else 365
    return period_start, period_end, Decimal(active_days) / Decimal(days_in_year)


def _fail_for_partial_first_year_rmd(
    request: ProjectionRequest,
    year: int,
    rules: RmdQcdRules,
) -> None:
    plan = request.plan
    if year != plan.start_date.year or plan.start_date == date(year, 1, 1):
        return
    rmd = calculate_rmd(
        year,
        tuple(
            RmdOwnerInput(owner_id=person.id, date_of_birth=person.date_of_birth)
            for person in plan.people
        ),
        tuple(
            RmdAccountInput(
                account_id=account.id,
                owner_id=account.owner_id,
                account_type=account.account_type,
                prior_year_end_balance=Decimal("0"),
            )
            for account in plan.accounts
        ),
        rules,
    )
    if any(
        account.eligible and account.divisor is not None
        for owner in rmd.owners
        for account in owner.accounts
    ):
        raise ValueError(
            f"Cannot calculate RMD for partial first projection year {year}: "
            "starting balances are as of plan.start_date, not prior-year-end balances"
        )


def _calculate_annual_medicare(
    request: ProjectionRequest,
    year: int,
    completed_irmaa_tax_records: dict[int, IrmaaTaxRecordInput],
    rules: MedicareIrmaaRules | None,
) -> AnnualIrmaaResult | None:
    medicare = request.plan.medicare
    if medicare is None:
        return None
    if not any(_is_enrolled_for_medicare(person, year) for person in medicare.people):
        return None
    if rules is None:
        raise ValueError(f"No applicable Medicare/IRMAA rule dataset exists for {year}")
    if rules.premium_year != year:
        raise ValueError(f"Medicare/IRMAA rules for premium year {year} are required")
    historical = {record.tax_year: record for record in medicare.historical_tax_records}
    return calculate_annual_irmaa(
        rules,
        medicare.people,
        completed_irmaa_tax_records,
        historical,
    )


def _is_enrolled_for_medicare(person: MedicarePersonInput, year: int) -> bool:
    return (
        person.part_b_enrollment_date is not None and person.part_b_enrollment_date.year <= year
    ) or (person.part_d_enrollment_date is not None and person.part_d_enrollment_date.year <= year)


def _apply_medicare_payments(
    request: ProjectionRequest,
    year: int,
    accounts: dict[str, AccountInput],
    balances: dict[str, Decimal],
    activity: dict[str, AccountActivity],
    result: AnnualIrmaaResult,
) -> list[TransactionLedgerEntry]:
    medicare = request.plan.medicare
    if medicare is None:
        return []
    entries: list[TransactionLedgerEntry] = []
    include_base = medicare.base_premium_mode is MedicareBasePremiumMode.MODELED_SEPARATELY
    for person in result.people:
        parts = [
            ("part-b-irmaa", person.annual_part_b_irmaa),
            ("part-d-irmaa", person.annual_part_d_irmaa),
        ]
        if include_base:
            parts.extend(
                [
                    ("part-b-base", person.annual_part_b_base),
                    ("part-d-plan", person.annual_part_d_plan),
                ]
            )
        for label, amount in parts:
            if amount == 0:
                continue
            transaction = AnnualTransactionInput(
                id=f"medicare:{person.owner_id}:{label}:{year}",
                year=year,
                transaction_type=TransactionType.MEDICARE_PAYMENT,
                amount=amount,
                source_account_id=medicare.premium_payment_account_id,
            )
            entry = apply_transaction(
                transaction,
                accounts,
                balances,
                activity,
                allow_negative_cash_balance=request.plan.allow_negative_cash_balance,
            )
            entries.append(entry)
    return entries


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
    resolved_income: list[ResolvedAnnualIncome],
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
            "wages": Decimal("0"),
            "rmd": Decimal("0"),
            "conversion": Decimal("0"),
            "gross_ss": Decimal("0"),
            "retirement_ss": Decimal("0"),
            "disability_ss": Decimal("0"),
        }
        for person in request.plan.people
    }
    resolved_by_id = {income.income_id: income for income in resolved_income}
    for income in request.plan.income:
        resolved = resolved_by_id.get(income.id)
        if (
            resolved is None
            or income.income_type is not IncomeType.PENSION
            or income.taxable_federal is not True
            or income.taxable_state is not True
        ):
            continue
        if income.owner_id not in people or income.pension_type is None:
            raise ValueError(
                f"Missouri pension {income.id} requires an owner and public/private pension_type"
            )
        key = "public" if income.pension_type is PensionType.PUBLIC else "private"
        components[income.owner_id][key] += resolved.taxable_amount
    for resolved in resolved_income:
        if resolved.income_type is not IncomeType.W2_WAGES:
            continue
        if resolved.owner_id not in people:
            raise ValueError(f"Missouri wages {resolved.income_id} require an owner")
        components[resolved.owner_id]["wages"] += resolved.taxable_amount
    if rmd_qcd_result is not None:
        for owner in rmd_qcd_result.owners:
            components[owner.owner_id]["rmd"] += owner.taxable_rmd
    account_owners = {account.id: account.owner_id for account in request.plan.accounts}
    for transaction in plan_transactions:
        if transaction.transaction_type is not TransactionType.ROTH_CONVERSION:
            continue
        owner_id = account_owners.get(transaction.source_account_id or "")
        if owner_id not in components:
            raise ValueError(f"Roth conversion {transaction.id} requires an owned source account")
        components[owner_id]["conversion"] += _taxable_conversion_amount(transaction)
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
            taxable_wages=components[person.id]["wages"],
            public_pension=components[person.id]["public"],
            private_pension=components[person.id]["private"],
            taxable_rmd=components[person.id]["rmd"],
            taxable_roth_conversion=components[person.id]["conversion"],
            gross_social_security=components[person.id]["gross_ss"],
            taxable_social_security_retirement=components[person.id]["retirement_ss"],
            taxable_social_security_disability=components[person.id]["disability_ss"],
        )
        for person in request.plan.people
    )
    return calculate_missouri_income_tax(
        owners,
        federal_tax_result.total_federal_tax,
        rules,
    )


def _calculate_annual_federal_tax(
    request: ProjectionRequest,
    year: int,
    plan_transactions: list[AnnualTransactionInput],
    year_entries: list[TransactionLedgerEntry],
    resolved_income: list[ResolvedAnnualIncome],
    social_security_benefits: list[AnnualSocialSecurityBenefit],
    federal_tax_rules: FederalTaxRules | None,
) -> tuple[
    AnnualFederalAgiResult | None,
    FederalIncomeTaxResult | None,
    SocialSecurityTaxationResult | None,
]:
    if year != 2026:
        return None, None, None
    if federal_tax_rules is None:
        raise ValueError("2026 federal tax rules are required for a 2026 projection")
    if request.plan.filing_status is not FilingStatus.MARRIED_FILING_JOINTLY:
        raise ValueError("Only married-filing-jointly 2026 federal tax is implemented")

    preliminary_agi = build_annual_federal_agi(
        request,
        year,
        year_entries,
        plan_transactions,
        social_security_benefits,
        None,
        resolved_income,
    )
    gross_social_security = sum(
        (benefit.gross_benefit for benefit in social_security_benefits),
        Decimal("0"),
    )
    social_security_taxation = calculate_taxable_social_security(
        gross_social_security,
        supported_provisional_income_before_social_security(preliminary_agi),
        federal_tax_rules.social_security_taxation,
    )
    federal_agi = build_annual_federal_agi(
        request,
        year,
        year_entries,
        plan_transactions,
        social_security_benefits,
        social_security_taxation,
        resolved_income,
    )
    federal_tax = calculate_federal_income_tax(
        supported_federal_ordinary_income(federal_agi),
        federal_tax_rules,
    )
    return federal_agi, federal_tax, social_security_taxation


def _raise_if_unsupported_federal_processing(year: int, source_ids: list[str]) -> None:
    if year == 2026 or not source_ids:
        return
    raise ValueError(
        f"Federal tax/AGI processing is unsupported for tax year {year}; "
        f"triggering source IDs: {', '.join(source_ids)}"
    )


def _federal_processing_source_ids(
    request: ProjectionRequest,
    year: int,
    plan_transactions: list[AnnualTransactionInput],
    resolved_income: list[ResolvedAnnualIncome],
    social_security_benefits: list[AnnualSocialSecurityBenefit],
    year_entries: list[TransactionLedgerEntry],
) -> list[str]:
    """Return deterministic IDs for nonzero activity requiring federal processing."""
    source_ids: list[str] = []
    for income in resolved_income:
        requires_processing = (
            (
                income.income_type in {IncomeType.W2_WAGES, IncomeType.TAXABLE_INTEREST}
                and income.taxable_amount > 0
            )
            or (income.income_type is IncomeType.PENSION and income.taxable_amount > 0)
            or (
                income.income_type is IncomeType.TAX_EXEMPT_INTEREST
                and income.spendable_cash_amount > 0
            )
        )
        if requires_processing:
            source_ids.append(f"income:{income.income_id}")
    for source in request.plan.social_security:
        if source.claim_date.year <= year and source.monthly_benefit > 0:
            source_ids.append(f"social-security:{source.id}")
    accounts = {account.id: account for account in request.plan.accounts}
    for transaction in plan_transactions:
        if (
            transaction.transaction_type is TransactionType.ROTH_CONVERSION
            and (
                transaction.taxable_amount
                if transaction.taxable_amount is not None
                else transaction.amount
            )
            > 0
        ):
            source_ids.append(f"transaction:{transaction.id}")
        elif transaction.transaction_type is TransactionType.WITHDRAWAL:
            withdrawal_source = accounts.get(transaction.source_account_id or "")
            if (
                withdrawal_source is not None
                and withdrawal_source.account_type in _PRETAX_ACCOUNT_TYPES
            ):
                source_ids.append(f"transaction:{transaction.id}")
    for benefit in social_security_benefits:
        if benefit.gross_benefit > 0:
            source_ids.append(f"social-security:{benefit.source_id}")
    for entry in year_entries:
        if (
            entry.transaction_type is TransactionType.RMD_DISTRIBUTION
            and entry.taxable_ordinary_income > 0
        ):
            source_ids.append(f"transaction:{entry.transaction_id}")
    return list(dict.fromkeys(source_ids))


def _taxable_conversion_amount(transaction: AnnualTransactionInput) -> Decimal:
    return (
        transaction.taxable_amount if transaction.taxable_amount is not None else transaction.amount
    )


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
        if transaction.transaction_type is TransactionType.MEDICARE_PAYMENT:
            raise ValueError("Medicare payment transactions are generated by the projection")
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


def _income_transactions(
    resolved_income: list[ResolvedAnnualIncome],
) -> list[AnnualTransactionInput]:
    transactions: list[AnnualTransactionInput] = []
    for income in resolved_income:
        if income.spendable_cash_amount == 0:
            continue
        transactions.append(
            AnnualTransactionInput(
                id=f"income:{income.income_id}:{income.year}",
                year=income.year,
                transaction_type=TransactionType.INCOME,
                amount=income.spendable_cash_amount,
                destination_account_id=income.destination_account_id,
                income_taxable_amount=income.taxable_amount,
                federal_income_tax_withholding=income.federal_income_tax_withholding,
                state_income_tax_withholding=income.state_income_tax_withholding,
                payroll_deductions_embedded_in_cash=income.payroll_deductions_embedded_in_cash,
                assumption_source=income.assumption_source,
            )
        )
    return transactions


def _resolve_annual_income(request: ProjectionRequest, year: int) -> list[ResolvedAnnualIncome]:
    """Resolve income without mutating plan inputs; overrides trump annual amounts."""
    plan = request.plan
    full_calendar_year = date(year, 1, 1) >= plan.start_date and date(year, 12, 31) <= plan.end_date
    resolved: list[ResolvedAnnualIncome] = []
    owners = {person.id: person for person in plan.people}
    for income in plan.income:
        end_date = (
            income.end_date
            if income.stop_rule is IncomeStopRule.EXPLICIT_END_DATE
            else owners[income.owner_id].retirement_date
            if (
                income.stop_rule is IncomeStopRule.OWNER_RETIREMENT_DATE
                and income.owner_id in owners
            )
            else None
        )
        if year < income.start_date.year or (end_date is not None and year > end_date.year):
            continue
        if income.income_type is IncomeType.SELF_EMPLOYMENT_NET_INCOME:
            raise ValueError(
                "SELF_EMPLOYMENT_NET_INCOME is unsupported until SE tax is implemented"
            )
        if income.destination_account_id is None:
            raise ValueError(f"Income {income.id} requires a destination cash account")
        override = income.annual_overrides.get(year)
        if override is not None:
            taxable = override.taxable_amount
            cash = override.spendable_cash_amount
            federal_withholding = override.federal_income_tax_withholding
            state_withholding = override.state_income_tax_withholding
            payroll_note = override.payroll_deductions_embedded_in_cash
            source = override.assumption_source or income.assumption_source
        else:
            taxable = income.annual_taxable_amount or Decimal("0")
            cash = income.annual_spendable_cash_amount or Decimal("0")
            federal_withholding = income.annual_federal_income_tax_withholding
            state_withholding = income.annual_state_income_tax_withholding
            payroll_note = income.payroll_deductions_embedded_in_cash
            source = income.assumption_source
            if full_calendar_year and _requires_monthly_proration(income, end_date, year):
                if _has_partial_boundary_month(income.start_date, end_date, year):
                    raise ValueError(
                        f"Income {income.id} requires an annual override for "
                        "a mid-month start or stop"
                    )
                months = _active_months(income.start_date, end_date, year)
                factor = Decimal(months) / Decimal("12")
                taxable = (taxable * factor).quantize(_CENT, rounding=ROUND_HALF_UP)
                cash = (cash * factor).quantize(_CENT, rounding=ROUND_HALF_UP)
                federal_withholding = (federal_withholding * factor).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
                state_withholding = (state_withholding * factor).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
        resolved.append(
            ResolvedAnnualIncome(
                income_id=income.id,
                year=year,
                owner_id=income.owner_id,
                income_type=income.income_type,
                taxable_amount=taxable,
                spendable_cash_amount=cash,
                federal_income_tax_withholding=federal_withholding,
                state_income_tax_withholding=state_withholding,
                payroll_deductions_embedded_in_cash=payroll_note,
                assumption_source=source,
                destination_account_id=income.destination_account_id,
            )
        )
    return resolved


def _requires_monthly_proration(
    income: IncomeInput,
    end_date: date | None,
    year: int,
) -> bool:
    return income.start_date.year == year or (end_date is not None and end_date.year == year)


def _active_months(start_date: date, end_date: date | None, year: int) -> int:
    return sum(
        1
        for month in range(1, 13)
        if date(year, month, 1) >= start_date
        and (end_date is None or date(year, month, 1) <= end_date)
    )


def _has_partial_boundary_month(start_date: date, end_date: date | None, year: int) -> bool:
    if start_date.year == year and start_date.day != 1:
        return True
    if end_date is None or end_date.year != year:
        return False
    next_day = end_date + timedelta(days=1)
    return next_day.month == end_date.month
