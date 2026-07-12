from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.domain.models import AnnualAccountResult, ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules
from retirement_core.rules.rmd_qcd import RmdQcdRules


@pytest.fixture(scope="module")
def federal_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.fixture(scope="module")
def rmd_rules() -> RmdQcdRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_applicable_dataset(
        "rmd_qcd", "US-FED", 2026
    )
    return RmdQcdRules.from_dataset(dataset)


def _request(
    start_date: str,
    end_date: str,
    *,
    annual_return: str = "0.10",
    people: list[dict[str, str]] | None = None,
    accounts: list[dict[str, str]] | None = None,
) -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Partial growth",
                "filing_status": "married_filing_jointly",
                "start_date": start_date,
                "end_date": end_date,
                "people": people or [],
                "accounts": accounts
                or [
                    {
                        "id": "cash",
                        "owner_id": "owner",
                        "account_type": "cash",
                        "starting_balance": "1000",
                        "annual_return": annual_return,
                    }
                ],
                "federal_tax_payment_account_id": "cash",
            }
        }
    )


def _account(result: ProjectionResult, year: int) -> AnnualAccountResult:
    return next(
        item for item in result.annual_accounts if item.year == year and item.account_id == "cash"
    )


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


def test_full_calendar_year_growth_is_unchanged() -> None:
    result = run_projection(_request("2030-01-01", "2030-12-31"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("100.00")
    assert account.ending_balance == Decimal("1100.00")
    assert account.growth_fraction == Decimal("1")
    _assert_reconciles(result)


def test_july_first_through_year_end_uses_inclusive_day_proration(
    federal_rules: FederalTaxRules,
) -> None:
    result = run_projection(_request("2026-07-01", "2026-12-31"), federal_rules)
    account = _account(result, 2026)

    assert account.growth_period_start is not None
    assert account.growth_period_start.isoformat() == "2026-07-01"
    assert account.growth_period_end is not None
    assert account.growth_period_end.isoformat() == "2026-12-31"
    assert account.growth_fraction == Decimal(184) / Decimal(365)
    assert account.investment_return == Decimal("50.41")
    _assert_reconciles(result)


def test_non_month_boundary_start_uses_actual_days() -> None:
    result = run_projection(_request("2030-07-02", "2030-12-31"))
    account = _account(result, 2030)

    assert account.growth_fraction == Decimal(183) / Decimal(365)
    assert account.investment_return == Decimal("50.14")
    _assert_reconciles(result)


def test_partial_final_year_is_prorated() -> None:
    result = run_projection(_request("2030-01-01", "2031-06-30"))
    final_account = _account(result, 2031)

    assert final_account.beginning_balance == Decimal("1100.00")
    assert final_account.growth_fraction == Decimal(181) / Decimal(365)
    assert final_account.investment_return == Decimal("54.55")
    _assert_reconciles(result)


def test_one_day_projection_uses_one_inclusive_day() -> None:
    result = run_projection(_request("2030-12-31", "2030-12-31"))
    account = _account(result, 2030)

    assert account.growth_fraction == Decimal(1) / Decimal(365)
    assert account.investment_return == Decimal("0.27")
    _assert_reconciles(result)


def test_leap_year_uses_366_day_denominator() -> None:
    result = run_projection(_request("2028-07-01", "2028-12-31"))
    account = _account(result, 2028)

    assert account.growth_fraction == Decimal(184) / Decimal(366)
    assert account.investment_return == Decimal("50.27")
    _assert_reconciles(result)


def test_zero_return_has_zero_growth() -> None:
    result = run_projection(_request("2030-07-01", "2030-12-31", annual_return="0"))
    account = _account(result, 2030)

    assert account.investment_return == Decimal("0.00")
    _assert_reconciles(result)


def test_partial_first_year_with_due_rmd_fails_explicitly(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        "2026-07-01",
        "2026-12-31",
        people=[{"id": "owner", "name": "Owner", "date_of_birth": "1950-01-01"}],
        accounts=[
            {
                "id": "cash",
                "owner_id": "owner",
                "account_type": "cash",
                "starting_balance": "0",
            },
            {
                "id": "ira",
                "owner_id": "owner",
                "account_type": "traditional_ira",
                "starting_balance": "100000",
            },
        ],
    )

    with pytest.raises(ValueError, match=r"starting balances are as of plan\.start_date"):
        run_projection(request, federal_rules, {2026: rmd_rules})


def test_partial_first_year_with_no_rmd_due_continues(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        "2026-07-01",
        "2026-12-31",
        people=[{"id": "owner", "name": "Owner", "date_of_birth": "1960-01-01"}],
        accounts=[
            {
                "id": "cash",
                "owner_id": "owner",
                "account_type": "cash",
                "starting_balance": "0",
            },
            {
                "id": "ira",
                "owner_id": "owner",
                "account_type": "traditional_ira",
                "starting_balance": "100000",
                "annual_return": "0.10",
            },
        ],
    )
    result = run_projection(request, federal_rules, {2026: rmd_rules})

    assert result.annual_household[0].rmd_qcd_result is not None
    assert result.annual_household[0].rmd_qcd_result.gross_rmd == Decimal("0.00")
    assert next(
        item for item in result.annual_accounts if item.account_id == "ira"
    ).investment_return == Decimal("5041.10")
    _assert_reconciles(result)
