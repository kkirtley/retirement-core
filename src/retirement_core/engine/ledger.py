from decimal import Decimal

from retirement_core.domain.models import AnnualAccountResult


def reconcile_account(result: AnnualAccountResult) -> None:
    expected = (
        result.beginning_balance
        + result.investment_return
        + result.contributions
        + result.inbound_transfers
        - result.withdrawals
        - result.roth_conversions
        - result.qcd
    )
    if expected != result.ending_balance:
        raise ValueError(
            f"Account {result.account_id} failed reconciliation for {result.year}: "
            f"expected {expected}, actual {result.ending_balance}"
        )


def calculate_growth(balance: Decimal, annual_return: Decimal) -> Decimal:
    return (balance * annual_return).quantize(Decimal("0.01"))
