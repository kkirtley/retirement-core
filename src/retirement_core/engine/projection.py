from decimal import Decimal

from retirement_core import __version__
from retirement_core.domain.enums import TransactionType
from retirement_core.domain.models import (
    AnnualAccountResult,
    AnnualHouseholdResult,
    AnnualTransactionInput,
    ProjectionRequest,
    ProjectionResult,
    TransactionLedgerEntry,
)
from retirement_core.engine.ledger import (
    calculate_growth,
    reconcile_account,
    reconcile_household_cash,
)
from retirement_core.engine.transactions import AccountActivity, apply_transaction


def run_projection(request: ProjectionRequest) -> ProjectionResult:
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

        annual_transactions = _income_transactions(request, year)
        annual_transactions.extend(
            transaction for transaction in plan.transactions if transaction.year == year
        )
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
        cash_surplus = spendable_income + cash_withdrawals - spending - contributions
        reconcile_household_cash(
            spendable_income,
            cash_withdrawals,
            spending,
            contributions,
            cash_surplus,
        )

        taxes = Decimal("0")  # Real tax engine will use versioned rule datasets.
        after_tax = spendable_income - taxes
        giving_target = (after_tax * plan.giving_policy.target_rate_after_tax_income).quantize(
            Decimal("0.01")
        )
        annual_household.append(
            AnnualHouseholdResult(
                year=year,
                gross_income=spendable_income,
                taxes=taxes,
                after_tax_income=after_tax,
                giving_target=giving_target,
                spending=spending,
                contributions=contributions,
                cash_withdrawals=cash_withdrawals,
                cash_surplus=cash_surplus,
            )
        )

    return ProjectionResult(
        engine_version=__version__,
        plan_schema_version=plan.schema_version,
        scenario_id=request.options.scenario_id,
        annual_accounts=annual_accounts,
        annual_household=annual_household,
        transactions=ledger_entries,
        warnings=[
            "Tax, RMD, QCD, Social Security, Medicare, IRMAA, and survivor engines "
            "are not yet implemented in this scaffold."
        ],
        provenance={
            "rules_mode": "external_versioned_datasets",
            "transaction_timing": "beginning_balance_growth_then_transactions",
        },
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
