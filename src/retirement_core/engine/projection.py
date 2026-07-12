from decimal import Decimal

from retirement_core import __version__
from retirement_core.domain.models import (
    AnnualAccountResult,
    AnnualHouseholdResult,
    ProjectionRequest,
    ProjectionResult,
)
from retirement_core.engine.ledger import calculate_growth, reconcile_account


def run_projection(request: ProjectionRequest) -> ProjectionResult:
    plan = request.plan
    accounts = {account.id: account for account in plan.accounts}
    balances = {account.id: account.starting_balance for account in plan.accounts}
    annual_accounts: list[AnnualAccountResult] = []
    annual_household: list[AnnualHouseholdResult] = []

    for year in range(plan.start_date.year, plan.end_date.year + 1):
        for account_id, account in accounts.items():
            beginning = balances[account_id]
            growth = calculate_growth(beginning, account.annual_return)
            ending = beginning + growth
            row = AnnualAccountResult(
                year=year,
                account_id=account_id,
                beginning_balance=beginning,
                investment_return=growth,
                ending_balance=ending,
            )
            reconcile_account(row)
            annual_accounts.append(row)
            balances[account_id] = ending

        gross_income = sum(
            (
                item.annual_amount
                for item in plan.income
                if item.start_date.year <= year
                and (item.end_date is None or item.end_date.year >= year)
            ),
            Decimal("0"),
        )
        taxes = Decimal("0")  # Real tax engine will use versioned rule datasets.
        after_tax = gross_income - taxes
        giving_target = (
            after_tax * plan.giving_policy.target_rate_after_tax_income
        ).quantize(Decimal("0.01"))
        annual_household.append(
            AnnualHouseholdResult(
                year=year,
                gross_income=gross_income,
                taxes=taxes,
                after_tax_income=after_tax,
                giving_target=giving_target,
                spending=Decimal("0"),
                cash_surplus=after_tax - giving_target,
            )
        )

    return ProjectionResult(
        engine_version=__version__,
        plan_schema_version=plan.schema_version,
        scenario_id=request.options.scenario_id,
        annual_accounts=annual_accounts,
        annual_household=annual_household,
        warnings=[
            "Tax, RMD, QCD, Social Security, Medicare, IRMAA, and survivor engines "
            "are not yet implemented in this scaffold."
        ],
        provenance={"rules_mode": "external_versioned_datasets"},
    )
