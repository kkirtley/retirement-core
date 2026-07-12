from decimal import Decimal

import pytest

from retirement_core.domain.models import AnnualAccountResult
from retirement_core.engine.ledger import reconcile_account


def test_account_reconciles() -> None:
    row = AnnualAccountResult(
        year=2033,
        account_id="roth",
        beginning_balance=Decimal("100.00"),
        investment_return=Decimal("6.00"),
        ending_balance=Decimal("106.00"),
    )
    reconcile_account(row)


def test_account_reconciliation_fails() -> None:
    row = AnnualAccountResult(
        year=2033,
        account_id="roth",
        beginning_balance=Decimal("100.00"),
        investment_return=Decimal("6.00"),
        ending_balance=Decimal("105.00"),
    )
    with pytest.raises(ValueError):
        reconcile_account(row)
