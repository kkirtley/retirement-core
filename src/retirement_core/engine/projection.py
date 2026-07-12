from decimal import Decimal

from retirement_core import __version__
from retirement_core.domain.enums import AccountType, FilingStatus, IncomeType, TransactionType
from retirement_core.domain.models import (
    AccountInput,
    AnnualAccountResult,
    AnnualHouseholdResult,
    AnnualTransactionInput,
    FederalIncomeTaxResult,
    ProjectionRequest,
    ProjectionResult,
    TransactionLedgerEntry,
)
from retirement_core.engine.federal_tax import calculate_federal_income_tax
from retirement_core.engine.ledger import (
    calculate_growth,
    reconcile_account,
    reconcile_household_cash,
)
from retirement_core.engine.transactions import AccountActivity, apply_transaction
from retirement_core.rules.models import FederalTaxRules

_PRETAX_ACCOUNT_TYPES = {AccountType.TRADITIONAL_IRA, AccountType.TRADITIONAL_401K}


def run_projection(
    request: ProjectionRequest,
    federal_tax_rules: FederalTaxRules | None = None,
) -> ProjectionResult:
    plan = request.plan
    accounts = {account.id: account for account in plan.accounts}
    if len(accounts) != len(plan.accounts):
        raise ValueError("Account IDs must be unique")

    balances = {account.id: account.starting_balance for account in plan.accounts}
    annual_accounts: list[AnnualAccountResult] = []
    annual_household: list[AnnualHouseholdResult] = []
    ledger_entries: list[TransactionLedgerEntry] = []

    first_year = plan.start_date.year
    last_year = plan.end_date.year
    invalid_years = [
        transaction.id
        for transaction in plan.transactions
        if not first_year <= transaction.year <= last_year
    ]
    if invalid_years:
        raise ValueError(f"Transactions outside the projection period: {', '.join(invalid_years)}")

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

        plan_transactions = [
            transaction for transaction in plan.transactions if transaction.year == year
        ]
        if year == 2026:
            _validate_2026_transaction_tax_treatment(plan_transactions, accounts)

        annual_transactions = _income_transactions(request, year)
        annual_transactions.extend(plan_transactions)
        year_entries: list[TransactionLedgerEntry] = []
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

        federal_tax_result = _calculate_annual_federal_tax(
            request,
            year,
            plan_transactions,
            federal_tax_rules,
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
        cash_surplus = spendable_income + cash_withdrawals - spending - contributions - federal_tax
        reconcile_household_cash(
            spendable_income,
            cash_withdrawals,
            spending,
            contributions,
            cash_surplus,
            federal_tax=federal_tax,
        )

        after_tax = spendable_income - federal_tax
        giving_target = (
            max(after_tax, Decimal("0")) * plan.giving_policy.target_rate_after_tax_income
        ).quantize(Decimal("0.01"))
        annual_household.append(
            AnnualHouseholdResult(
                year=year,
                gross_income=spendable_income,
                taxes=federal_tax,
                after_tax_income=after_tax,
                giving_target=giving_target,
                spending=spending,
                contributions=contributions,
                cash_withdrawals=cash_withdrawals,
                cash_surplus=cash_surplus,
                federal_tax_result=federal_tax_result,
            )
        )

    provenance = {
        "rules_mode": "external_versioned_datasets",
        "transaction_timing": "beginning_balance_growth_then_transactions_then_tax",
    }
    if federal_tax_rules is not None and first_year <= 2026 <= last_year:
        provenance["federal_tax_dataset_id"] = federal_tax_rules.dataset_id

    return ProjectionResult(
        engine_version=__version__,
        plan_schema_version=plan.schema_version,
        scenario_id=request.options.scenario_id,
        annual_accounts=annual_accounts,
        annual_household=annual_household,
        transactions=ledger_entries,
        warnings=[
            "Federal tax is limited to 2026 MFJ ordinary pension income and Roth conversions. "
            "RMD, QCD, Social Security, Medicare, IRMAA, state-tax, and survivor engines are "
            "not implemented."
        ],
        provenance=provenance,
    )


def _calculate_annual_federal_tax(
    request: ProjectionRequest,
    year: int,
    plan_transactions: list[AnnualTransactionInput],
    federal_tax_rules: FederalTaxRules | None,
) -> FederalIncomeTaxResult | None:
    if year != 2026:
        return None
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
    return calculate_federal_income_tax(ordinary_income, federal_tax_rules)


def _validate_2026_transaction_tax_treatment(
    transactions: list[AnnualTransactionInput],
    accounts: dict[str, AccountInput],
) -> None:
    for transaction in transactions:
        if transaction.transaction_type is TransactionType.FEDERAL_TAX_PAYMENT:
            raise ValueError("Federal tax payment transactions are generated by the projection")
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
