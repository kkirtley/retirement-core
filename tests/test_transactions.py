from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from retirement_core.domain.models import (
    ProjectionRequest,
    ProjectionResult,
)
from retirement_core.engine.ledger import reconcile_household_cash
from retirement_core.engine.projection import run_projection


def _account(account_id: str, account_type: str, balance: str) -> dict[str, str]:
    return {
        "id": account_id,
        "owner_id": "person",
        "account_type": account_type,
        "starting_balance": balance,
    }


def _transaction(
    transaction_id: str,
    transaction_type: str,
    amount: str,
    *,
    source: str | None = None,
    destination: str | None = None,
) -> dict[str, str]:
    transaction = {
        "id": transaction_id,
        "year": "2030",
        "transaction_type": transaction_type,
        "amount": amount,
    }
    if source is not None:
        transaction["source_account_id"] = source
    if destination is not None:
        transaction["destination_account_id"] = destination
    return transaction


def _request(
    *,
    accounts: list[dict[str, str]],
    transactions: list[dict[str, str]] | None = None,
    income: list[dict[str, str | None]] | None = None,
    allow_negative_cash_balance: bool = False,
) -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Test Household",
                "filing_status": "married_filing_jointly",
                "start_date": "2030-01-01",
                "end_date": "2030-12-31",
                "people": [],
                "accounts": accounts,
                "income": income or [],
                "transactions": transactions or [],
                "allow_negative_cash_balance": allow_negative_cash_balance,
            }
        }
    )


def _account_balances(result: ProjectionResult) -> dict[str, Decimal]:
    return {row.account_id: row.ending_balance for row in result.annual_accounts}


@pytest.mark.parametrize(
    ("case", "projection_request", "expected_balances", "expected_cash_flow"),
    [
        (
            "income deposited to cash",
            _request(
                accounts=[_account("cash", "cash", "0")],
                income=[
                    {
                        "id": "pension",
                        "annual_amount": "1000",
                        "start_date": "2030-01-01",
                        "end_date": None,
                        "destination_account_id": "cash",
                    }
                ],
            ),
            {"cash": Decimal("1000")},
            (Decimal("1000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1000")),
        ),
        (
            "spending withdrawn from cash",
            _request(
                accounts=[_account("cash", "cash", "1000")],
                transactions=[_transaction("spend", "spending", "400", source="cash")],
            ),
            {"cash": Decimal("600")},
            (Decimal("0"), Decimal("0"), Decimal("400"), Decimal("0"), Decimal("-400")),
        ),
        (
            "traditional to Roth conversion",
            _request(
                accounts=[
                    _account("traditional", "traditional_ira", "1000"),
                    _account("roth", "roth_ira", "0"),
                ],
                transactions=[
                    _transaction(
                        "convert",
                        "roth_conversion",
                        "300",
                        source="traditional",
                        destination="roth",
                    )
                ],
            ),
            {"traditional": Decimal("700"), "roth": Decimal("300")},
            (Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        ),
        (
            "account withdrawal funds spending",
            _request(
                accounts=[
                    _account("traditional", "traditional_ira", "1000"),
                    _account("cash", "cash", "0"),
                ],
                transactions=[
                    _transaction(
                        "withdraw",
                        "withdrawal",
                        "500",
                        source="traditional",
                        destination="cash",
                    ),
                    _transaction("spend", "spending", "500", source="cash"),
                ],
            ),
            {"traditional": Decimal("500"), "cash": Decimal("0")},
            (Decimal("0"), Decimal("500"), Decimal("500"), Decimal("0"), Decimal("0")),
        ),
        (
            "annual surplus",
            _request(
                accounts=[_account("cash", "cash", "0")],
                income=[
                    {
                        "id": "income",
                        "annual_amount": "1000",
                        "start_date": "2030-01-01",
                        "end_date": None,
                        "destination_account_id": "cash",
                    }
                ],
                transactions=[_transaction("spend", "spending", "600", source="cash")],
            ),
            {"cash": Decimal("400")},
            (Decimal("1000"), Decimal("0"), Decimal("600"), Decimal("0"), Decimal("400")),
        ),
        (
            "annual deficit",
            _request(
                accounts=[_account("cash", "cash", "500")],
                income=[
                    {
                        "id": "income",
                        "annual_amount": "100",
                        "start_date": "2030-01-01",
                        "end_date": None,
                        "destination_account_id": "cash",
                    }
                ],
                transactions=[_transaction("spend", "spending", "300", source="cash")],
            ),
            {"cash": Decimal("300")},
            (Decimal("100"), Decimal("0"), Decimal("300"), Decimal("0"), Decimal("-200")),
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_annual_transaction_scenarios(
    case: str,
    projection_request: ProjectionRequest,
    expected_balances: dict[str, Decimal],
    expected_cash_flow: tuple[Decimal, Decimal, Decimal, Decimal, Decimal],
) -> None:
    del case
    result = run_projection(projection_request)
    household = result.annual_household[0]

    assert _account_balances(result) == expected_balances
    assert (
        household.gross_income,
        household.cash_withdrawals,
        household.spending,
        household.contributions,
        household.cash_surplus,
    ) == expected_cash_flow


@pytest.mark.parametrize("incorrect_surplus", [Decimal("59.98"), Decimal("60.02")])
def test_failed_household_reconciliation(incorrect_surplus: Decimal) -> None:
    with pytest.raises(ValueError, match="Household cash flow failed reconciliation"):
        reconcile_household_cash(
            spendable_income=Decimal("100"),
            cash_withdrawals=Decimal("0"),
            spending=Decimal("40"),
            contributions=Decimal("0"),
            cash_surplus=incorrect_surplus,
        )


def test_cash_account_cannot_silently_go_negative() -> None:
    request = _request(
        accounts=[_account("cash", "cash", "100")],
        transactions=[_transaction("spend", "spending", "100.02", source="cash")],
    )

    with pytest.raises(ValueError, match="would make account cash negative"):
        run_projection(request)


def test_roth_conversion_does_not_affect_household_cash() -> None:
    request = _request(
        accounts=[
            _account("traditional", "traditional_ira", "1000"),
            _account("roth", "roth_ira", "0"),
        ],
        transactions=[
            _transaction(
                "convert",
                "roth_conversion",
                "250",
                source="traditional",
                destination="roth",
            )
        ],
    )

    result = run_projection(request)
    household = result.annual_household[0]
    traditional = next(row for row in result.annual_accounts if row.account_id == "traditional")
    assert household.cash_withdrawals == 0
    assert household.cash_surplus == 0
    assert traditional.withdrawals == Decimal("250")
    assert traditional.transfers_out == 0


def test_internal_transfer_nets_to_zero_for_household() -> None:
    request = _request(
        accounts=[_account("one", "taxable", "500"), _account("two", "taxable", "100")],
        transactions=[_transaction("move", "transfer", "200", source="one", destination="two")],
    )

    result = run_projection(request)
    assert sum(_account_balances(result).values()) == Decimal("600")
    assert result.annual_household[0].cash_surplus == 0


def test_growth_is_applied_before_transactions() -> None:
    account = _account("traditional", "traditional_ira", "100")
    account["annual_return"] = "0.10"
    request = _request(
        accounts=[account, _account("cash", "cash", "0")],
        transactions=[
            _transaction(
                "withdraw",
                "withdrawal",
                "50",
                source="traditional",
                destination="cash",
            )
        ],
    )

    result = run_projection(request)
    assert _account_balances(result) == {"traditional": Decimal("60.00"), "cash": Decimal("50")}
    assert (
        result.provenance["transaction_timing"]
        == "beginning_balance_growth_then_transactions_then_tax"
    )


def test_input_transactions_are_immutable_and_results_are_separate() -> None:
    request = _request(
        accounts=[_account("cash", "cash", "100")],
        transactions=[_transaction("spend", "spending", "25", source="cash")],
    )
    original = deepcopy(request.plan.transactions[0].model_dump())

    result = run_projection(request)

    assert request.plan.transactions[0].model_dump() == original
    assert id(result.transactions[0]) != id(request.plan.transactions[0])
    with pytest.raises(ValidationError):
        request.plan.transactions[0].amount = Decimal("30")


def test_cash_charitable_giving_reduces_cash_and_counts_as_spending() -> None:
    request = _request(
        accounts=[_account("cash", "cash", "200")],
        transactions=[
            {
                **_transaction("gift", "charitable_giving", "50", source="cash"),
                "charitable_method": "cash",
            }
        ],
    )

    result = run_projection(request)
    assert _account_balances(result)["cash"] == Decimal("150")
    assert result.annual_household[0].spending == Decimal("50")
