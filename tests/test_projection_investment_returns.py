from decimal import Decimal

import pytest
from pydantic import ValidationError

from retirement_core.domain.models import (
    AccountInput,
    AnnualAccountResult,
    GivingPolicyInput,
    ProjectionRequest,
    ProjectionResult,
    SocialSecurityInput,
)
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection


def _request(
    start_date: str,
    end_date: str,
    *,
    balance: str = "1000",
    annual_return: str = "0",
    overrides: dict[str, str] | None = None,
) -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Investment returns",
                "filing_status": "married_filing_jointly",
                "start_date": start_date,
                "end_date": end_date,
                "people": [],
                "accounts": [
                    {
                        "id": "account",
                        "owner_id": "owner",
                        "account_type": "taxable",
                        "starting_balance": balance,
                        "annual_return": annual_return,
                        "annual_return_overrides": overrides or {},
                    }
                ],
            }
        }
    )


def _account(result: ProjectionResult, year: int) -> AnnualAccountResult:
    return next(item for item in result.annual_accounts if item.year == year)


def _assert_reconciles(result: ProjectionResult) -> None:
    for account in result.annual_accounts:
        reconcile_account(account)
    for household in result.annual_household:
        reconcile_household_cash(
            household.gross_income,
            household.cash_withdrawals,
            household.spending,
            household.contributions,
            household.cash_surplus,
            federal_tax=household.federal_tax_payment,
            missouri_tax=household.missouri_tax_payment,
            federal_tax_refunds=household.federal_tax_refund,
            missouri_tax_refunds=household.missouri_tax_refund,
            medicare_costs=household.medicare_costs,
        )


def test_existing_positive_default_return_behavior_is_unchanged() -> None:
    result = run_projection(_request("2030-01-01", "2030-12-31", annual_return="0.10"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("100.00")
    assert account.ending_balance == Decimal("1100.00")
    assert account.annual_return_applied == Decimal("0.10")
    assert account.annual_return_source == "default"
    _assert_reconciles(result)


def test_full_year_negative_return() -> None:
    result = run_projection(_request("2030-01-01", "2030-12-31", annual_return="-0.20"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("-200.00")
    assert account.ending_balance == Decimal("800.00")
    _assert_reconciles(result)


def test_partial_year_negative_return() -> None:
    result = run_projection(_request("2030-07-01", "2030-12-31", annual_return="-0.20"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("-100.82")
    assert account.ending_balance == Decimal("899.18")
    _assert_reconciles(result)


def test_leap_year_negative_return() -> None:
    result = run_projection(_request("2028-07-01", "2028-12-31", annual_return="-0.20"))
    account = _account(result, 2028)

    assert account.investment_return == Decimal("-100.55")
    _assert_reconciles(result)


def test_full_year_hundred_percent_loss_reaches_zero_without_going_negative() -> None:
    result = run_projection(_request("2030-01-01", "2030-12-31", annual_return="-1"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("-1000.00")
    assert account.ending_balance == Decimal("0.00")
    _assert_reconciles(result)


def test_return_below_negative_hundred_percent_is_rejected() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to -1"):
        AccountInput.model_validate(
            {
                "id": "account",
                "owner_id": "owner",
                "account_type": "taxable",
                "starting_balance": "1000",
                "annual_return": "-1.01",
            }
        )


def test_return_above_positive_hundred_percent_is_accepted() -> None:
    result = run_projection(_request("2030-01-01", "2030-12-31", annual_return="1.50"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("1500.00")
    assert account.ending_balance == Decimal("2500.00")


def test_zero_balance_with_negative_return_remains_zero() -> None:
    result = run_projection(
        _request("2030-01-01", "2030-12-31", balance="0", annual_return="-0.20")
    )
    account = _account(result, 2030)

    assert account.investment_return == Decimal("0.00")
    assert account.ending_balance == Decimal("0.00")


def test_year_specific_overrides_drive_a_three_year_sequence() -> None:
    result = run_projection(
        _request(
            "2030-01-01",
            "2032-12-31",
            annual_return="0",
            overrides={"2030": "0.10", "2031": "-0.20", "2032": "0.05"},
        )
    )

    first = _account(result, 2030)
    second = _account(result, 2031)
    third = _account(result, 2032)
    assert (first.ending_balance, second.ending_balance, third.ending_balance) == (
        Decimal("1100.00"),
        Decimal("880.00"),
        Decimal("924.00"),
    )
    assert [
        first.annual_return_source,
        second.annual_return_source,
        third.annual_return_source,
    ] == [
        "annual_override",
        "annual_override",
        "annual_override",
    ]
    _assert_reconciles(result)


def test_override_takes_priority_over_default() -> None:
    result = run_projection(
        _request(
            "2030-01-01",
            "2030-12-31",
            annual_return="0.10",
            overrides={"2030": "-0.20"},
        )
    )
    account = _account(result, 2030)

    assert account.annual_return_applied == Decimal("-0.20")
    assert account.annual_return_source == "annual_override"
    assert account.ending_balance == Decimal("800.00")


def test_out_of_range_override_year_fails_validation() -> None:
    with pytest.raises(ValueError, match="annual_return_overrides outside projection range"):
        _request("2030-01-01", "2030-12-31", overrides={"2031": "0.10"})


def test_unrelated_percent_fields_still_reject_negative_values() -> None:
    with pytest.raises(ValidationError):
        SocialSecurityInput.model_validate(
            {
                "id": "ss",
                "owner_id": "owner",
                "claim_date": "2030-01-01",
                "monthly_benefit": "1",
                "annual_cola": "-0.01",
                "destination_account_id": "cash",
            }
        )
    with pytest.raises(ValidationError):
        GivingPolicyInput.model_validate({"target_rate_after_tax_income": "-0.01"})
