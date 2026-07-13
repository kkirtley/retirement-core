from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus, TransactionType
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules


@pytest.fixture(scope="module")
def federal_2026_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.fixture(scope="module")
def synthetic_2027_rules(federal_2026_rules: FederalTaxRules) -> FederalTaxRules:
    return federal_2026_rules.model_copy(
        update={"dataset_id": "TEST-US-FED-2027-SS-v1", "tax_year": 2027}
    )


@pytest.fixture(scope="module")
def synthetic_2028_rules(federal_2026_rules: FederalTaxRules) -> FederalTaxRules:
    return federal_2026_rules.model_copy(
        update={"dataset_id": "TEST-US-FED-2028-SS-v1", "tax_year": 2028}
    )


def _request(
    start_date: str,
    end_date: str,
    social_security: list[dict[str, object]],
) -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Multi-year Social Security",
                "filing_status": "married_filing_jointly",
                "start_date": start_date,
                "end_date": end_date,
                "people": [
                    {"id": "kevin", "name": "Kevin", "date_of_birth": "1960-01-01"},
                    {"id": "joan", "name": "Joan", "date_of_birth": "1961-01-01"},
                ],
                "accounts": [
                    {
                        "id": "cash",
                        "owner_id": "kevin",
                        "account_type": "cash",
                        "starting_balance": "0",
                    }
                ],
                "social_security": social_security,
                "federal_tax_payment_account_id": "cash",
            }
        }
    )


def _benefit(
    source_id: str,
    owner_id: str,
    claim_date: str,
    monthly_benefit: str,
    cola: str = "0",
) -> dict[str, object]:
    return {
        "id": source_id,
        "owner_id": owner_id,
        "claim_date": claim_date,
        "monthly_benefit": monthly_benefit,
        "annual_cola": cola,
        "destination_account_id": "cash",
    }


def _household(result: ProjectionResult, year: int) -> object:
    return next(item for item in result.annual_household if item.year == year)


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


def test_claim_before_2026_receives_full_2026_benefit_with_cola(
    federal_2026_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(
            "2026-01-01", "2026-12-31", [_benefit("kevin-ss", "kevin", "2025-02-01", "100", "0.10")]
        ),
        federal_2026_rules,
    )
    benefit = _household(result, 2026).social_security_benefits[0]

    assert (benefit.monthly_benefit, benefit.months_received, benefit.gross_benefit) == (
        Decimal("110.00"),
        12,
        Decimal("1320.00"),
    )
    assert benefit.applied_cola_years == 1
    _assert_reconciles(result)


@pytest.mark.parametrize(
    ("claim_date", "expected_months", "expected_period_start"),
    [
        ("2026-02-01", 11, "2026-02-01"),
        ("2026-07-01", 6, "2026-07-01"),
    ],
)
def test_claim_year_includes_claim_month_through_december(
    federal_2026_rules: FederalTaxRules,
    claim_date: str,
    expected_months: int,
    expected_period_start: str,
) -> None:
    result = run_projection(
        _request("2026-01-01", "2026-12-31", [_benefit("ss", "joan", claim_date, "100")]),
        federal_2026_rules,
    )
    benefit = _household(result, 2026).social_security_benefits[0]

    assert benefit.months_received == expected_months
    assert benefit.gross_benefit == Decimal("100") * expected_months
    assert benefit.benefit_period_start is not None
    assert benefit.benefit_period_start.isoformat() == expected_period_start
    assert benefit.benefit_period_end is not None
    assert benefit.benefit_period_end.isoformat() == "2026-12-01"


def test_claim_after_2026_uses_matching_synthetic_rule_year(
    synthetic_2027_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request("2027-01-01", "2027-12-31", [_benefit("joan-ss", "joan", "2027-03-01", "100")]),
        federal_tax_rules_by_year={2027: synthetic_2027_rules},
    )
    benefit = _household(result, 2027).social_security_benefits[0]

    assert benefit.months_received == 10
    assert benefit.gross_benefit == Decimal("1000")
    assert result.provenance["federal_tax_dataset_id:2027"] == "TEST-US-FED-2027-SS-v1"
    _assert_reconciles(result)


def test_later_year_cola_compounds_and_monthly_amount_rounds_first(
    synthetic_2027_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(
            "2027-01-01",
            "2027-12-31",
            [_benefit("kevin-ss", "kevin", "2026-02-01", "100.01", "0.005")],
        ),
        federal_tax_rules_by_year={2027: synthetic_2027_rules},
    )
    benefit = _household(result, 2027).social_security_benefits[0]

    assert benefit.monthly_benefit == Decimal("100.51")
    assert benefit.months_received == 12
    assert benefit.gross_benefit == Decimal("1206.12")
    assert benefit.applied_cola_years == 1


def test_partial_plan_years_include_only_months_active_on_the_first_day(
    federal_2026_rules: FederalTaxRules, synthetic_2027_rules: FederalTaxRules
) -> None:
    result = run_projection(
        _request(
            "2026-07-01",
            "2027-05-15",
            [_benefit("ss", "kevin", "2026-02-01", "100")],
        ),
        federal_tax_rules_by_year={2026: federal_2026_rules, 2027: synthetic_2027_rules},
    )

    assert _household(result, 2026).social_security_benefits[0].months_received == 6
    assert _household(result, 2027).social_security_benefits[0].months_received == 5
    _assert_reconciles(result)


def test_leap_year_does_not_change_month_based_benefit_count(
    synthetic_2028_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request("2028-01-01", "2028-12-31", [_benefit("ss", "joan", "2027-02-01", "100")]),
        federal_tax_rules_by_year={2028: synthetic_2028_rules},
    )
    benefit = _household(result, 2028).social_security_benefits[0]

    assert benefit.months_received == 12
    assert benefit.gross_benefit == Decimal("1200")


def test_two_spouses_keep_separate_records_across_claim_years(
    federal_2026_rules: FederalTaxRules, synthetic_2027_rules: FederalTaxRules
) -> None:
    result = run_projection(
        _request(
            "2026-01-01",
            "2027-12-31",
            [
                _benefit("kevin-ss", "kevin", "2026-07-01", "100"),
                _benefit("joan-ss", "joan", "2027-02-01", "200"),
            ],
        ),
        federal_tax_rules_by_year={2026: federal_2026_rules, 2027: synthetic_2027_rules},
    )

    assert [item.owner_id for item in _household(result, 2026).social_security_benefits] == [
        "kevin"
    ]
    assert [item.owner_id for item in _household(result, 2027).social_security_benefits] == [
        "kevin",
        "joan",
    ]


def test_missing_future_federal_rules_fail_closed_for_social_security() -> None:
    with pytest.raises(ValueError, match=r"tax year 2027.*social-security:ss"):
        run_projection(
            _request("2027-01-01", "2027-12-31", [_benefit("ss", "kevin", "2027-01-01", "100")])
        )


def test_social_security_is_spendable_but_not_direct_ledger_ordinary_income(
    synthetic_2027_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request("2027-01-01", "2027-12-31", [_benefit("ss", "kevin", "2027-01-01", "100")]),
        federal_tax_rules_by_year={2027: synthetic_2027_rules},
    )
    entry = next(
        item
        for item in result.transactions
        if item.transaction_type is TransactionType.SOCIAL_SECURITY_INCOME
    )

    assert entry.spendable_income == Decimal("1200")
    assert entry.taxable_ordinary_income == Decimal("0")
    assert _household(result, 2027).federal_tax_result is not None
    _assert_reconciles(result)
