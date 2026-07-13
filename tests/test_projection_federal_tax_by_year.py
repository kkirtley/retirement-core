from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxBracket, FederalTaxRules

YEAR = 2027


@pytest.fixture(scope="module")
def federal_2026_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.fixture(scope="module")
def synthetic_2027_rules(federal_2026_rules: FederalTaxRules) -> FederalTaxRules:
    return federal_2026_rules.model_copy(
        update={
            "dataset_id": "TEST-US-FED-2027-v1",
            "tax_year": YEAR,
            "standard_deduction": Decimal("10000"),
            "ordinary_income_brackets": (
                FederalTaxBracket(
                    lower_bound=Decimal("0"), upper_bound=Decimal("20000"), rate=Decimal("0.10")
                ),
                FederalTaxBracket(
                    lower_bound=Decimal("20000"), upper_bound=None, rate=Decimal("0.20")
                ),
            ),
        }
    )


def _request(
    *, income: list[dict[str, object]], withholding: str = "0", missouri: bool = False
) -> ProjectionRequest:
    if withholding:
        for source in income:
            if source.get("income_type") == "w2_wages":
                source["annual_federal_income_tax_withholding"] = withholding
    plan: dict[str, object] = {
        "household_name": "Synthetic federal tax year",
        "filing_status": "married_filing_jointly",
        "start_date": f"{YEAR}-01-01",
        "end_date": f"{YEAR}-12-31",
        "people": [],
        "accounts": [
            {"id": "cash", "owner_id": "owner", "account_type": "cash", "starting_balance": "0"}
        ],
        "income": income,
        "federal_tax_payment_account_id": "cash",
    }
    if missouri:
        plan["state_residency"] = {"state_code": "MO", "status": "full_year_resident"}
        plan["missouri_tax_payment_account_id"] = "cash"
    return ProjectionRequest.model_validate({"plan": plan})


def _income(source_id: str, income_type: str, amount: str) -> dict[str, object]:
    source: dict[str, object] = {
        "id": source_id,
        "income_type": income_type,
        "annual_amount": amount,
        "start_date": f"{YEAR}-01-01",
        "destination_account_id": "cash",
    }
    if income_type == "w2_wages":
        source.pop("annual_amount")
        source["annual_taxable_amount"] = amount
        source["annual_spendable_cash_amount"] = amount
    return source


def _assert_reconciles(result: ProjectionResult) -> None:
    for account in result.annual_accounts:
        reconcile_account(account)
    household = result.annual_household[0]
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


def test_synthetic_2027_wages_use_matching_dataset(
    synthetic_2027_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(income=[_income("wages", "w2_wages", "50000")]),
        federal_tax_rules_by_year={YEAR: synthetic_2027_rules},
    )
    household = result.annual_household[0]

    assert household.federal_tax_result is not None
    assert household.federal_tax_result.standard_deduction == Decimal("10000")
    assert household.federal_tax_result.taxable_income == Decimal("40000")
    assert household.federal_tax_result.total_federal_tax == Decimal("6000.00")
    assert result.provenance[f"federal_tax_dataset_id:{YEAR}"] == "TEST-US-FED-2027-v1"
    _assert_reconciles(result)


def test_synthetic_2027_pension_and_interest_are_taxed(
    synthetic_2027_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(
            income=[
                _income("pension", "pension", "30000"),
                _income("interest", "taxable_interest", "5000"),
            ]
        ),
        federal_tax_rules_by_year={YEAR: synthetic_2027_rules},
    )
    tax = result.annual_household[0].federal_tax_result

    assert tax is not None
    assert tax.gross_income == Decimal("35000")
    assert tax.standard_deduction == Decimal("10000")
    assert tax.total_federal_tax == Decimal("3000.00")


def test_mismatched_rule_tax_year_fails(
    federal_2026_rules: FederalTaxRules,
) -> None:
    with pytest.raises(ValueError, match="tax year 2026 does not match projection year 2027"):
        run_projection(
            _request(income=[_income("wages", "w2_wages", "1")]),
            federal_tax_rules_by_year={YEAR: federal_2026_rules},
        )


def test_missing_2027_rules_fail_closed_with_source_id() -> None:
    with pytest.raises(ValueError, match=r"tax year 2027.*income:wages"):
        run_projection(_request(income=[_income("wages", "w2_wages", "1")]))


def test_synthetic_2027_withholding_refund_uses_net_settlement(
    synthetic_2027_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(
            income=[_income("wages", "w2_wages", "50000")],
            withholding="8000",
        ),
        federal_tax_rules_by_year={YEAR: synthetic_2027_rules},
    )
    household = result.annual_household[0]

    assert household.total_federal_liability == Decimal("6000.00")
    assert household.federal_withholding == Decimal("8000")
    assert household.federal_tax_refund == Decimal("2000.00")
    _assert_reconciles(result)


def test_missouri_cannot_proceed_without_matching_federal_rules() -> None:
    with pytest.raises(ValueError, match=r"tax year 2027.*income:wages"):
        run_projection(_request(income=[_income("wages", "w2_wages", "1")], missouri=True))
