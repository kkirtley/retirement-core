from decimal import Decimal

from retirement_core.domain.models import AnnualAccountResult

RECONCILIATION_TOLERANCE = Decimal("0.01")


def reconcile_account(result: AnnualAccountResult) -> None:
    expected = (
        result.beginning_balance
        + result.investment_return
        + result.contributions
        + result.transfers_in
        - result.withdrawals
        - result.transfers_out
    )
    if abs(expected - result.ending_balance) > RECONCILIATION_TOLERANCE:
        raise ValueError(
            f"Account {result.account_id} failed reconciliation for {result.year}: "
            f"expected {expected}, actual {result.ending_balance}"
        )


def reconcile_household_cash(
    spendable_income: Decimal,
    cash_withdrawals: Decimal,
    spending: Decimal,
    contributions: Decimal,
    cash_surplus: Decimal,
    federal_tax: Decimal = Decimal("0"),
    missouri_tax: Decimal = Decimal("0"),
    federal_tax_refunds: Decimal = Decimal("0"),
    missouri_tax_refunds: Decimal = Decimal("0"),
    medicare_costs: Decimal = Decimal("0"),
) -> None:
    expected = (
        spendable_income
        + cash_withdrawals
        + federal_tax_refunds
        + missouri_tax_refunds
        - spending
        - contributions
        - federal_tax
        - missouri_tax
        - medicare_costs
    )
    if abs(expected - cash_surplus) > RECONCILIATION_TOLERANCE:
        raise ValueError(
            f"Household cash flow failed reconciliation: expected {expected}, actual {cash_surplus}"
        )


def calculate_growth(
    balance: Decimal,
    annual_return: Decimal,
    growth_fraction: Decimal = Decimal("1"),
) -> Decimal:
    return (balance * annual_return * growth_fraction).quantize(Decimal("0.01"))
