from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FederalAgiComponentType, FilingStatus
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.medicare_irmaa import irmaa_tax_record_from_annual_agi
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules
from retirement_core.rules.rmd_qcd import RmdQcdRules

YEAR = 2026


@pytest.fixture(scope="module")
def federal_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", YEAR)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.fixture(scope="module")
def rmd_rules() -> RmdQcdRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_applicable_dataset(
        "rmd_qcd", "US-FED", YEAR
    )
    return RmdQcdRules.from_dataset(dataset)


def _person(owner_id: str, birth_date: str = "1960-01-01") -> dict[str, str]:
    return {"id": owner_id, "name": owner_id, "date_of_birth": birth_date}


def _account(account_id: str, owner_id: str, account_type: str, balance: str) -> dict[str, str]:
    return {
        "id": account_id,
        "owner_id": owner_id,
        "account_type": account_type,
        "starting_balance": balance,
    }


def _cash(balance: str = "0") -> dict[str, str]:
    return _account("cash", "spouse_a", "cash", balance)


def _request(**plan_overrides: object) -> ProjectionRequest:
    plan: dict[str, object] = {
        "household_name": "Federal AGI Test",
        "filing_status": "married_filing_jointly",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "people": [_person("spouse_a"), _person("spouse_b")],
        "accounts": [_cash()],
        "federal_tax_payment_account_id": "cash",
    }
    plan.update(plan_overrides)
    return ProjectionRequest.model_validate({"plan": plan})


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
        federal_tax=household.taxes,
    )


def test_pension_plus_taxable_social_security_agi(federal_rules: FederalTaxRules) -> None:
    request = _request(
        income=[
            {
                "id": "pension",
                "income_type": "pension",
                "owner_id": "spouse_a",
                "annual_amount": "30000",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            }
        ],
        social_security=[
            {
                "id": "ss-a",
                "owner_id": "spouse_a",
                "benefit_subtype": "retirement",
                "claim_date": "2026-01-01",
                "monthly_benefit": "2000",
                "destination_account_id": "cash",
            }
        ],
    )

    result = run_projection(request, federal_rules)
    agi = result.annual_household[0].federal_agi_result

    assert agi is not None
    assert agi.taxable_pension == Decimal("30000")
    assert agi.federally_taxable_social_security == Decimal("5000.00")
    assert agi.federal_adjusted_gross_income == Decimal("35000.00")
    assert agi.irmaa_magi == Decimal("35000.00")
    assert result.annual_household[0].federal_tax_result is not None
    assert result.annual_household[0].federal_tax_result.gross_income == Decimal("35000.00")
    _assert_reconciles(result)


def test_partially_taxable_roth_conversion_agi(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[
            _cash(),
            _account("traditional", "spouse_a", "traditional_ira", "50000"),
            _account("roth", "spouse_a", "roth_ira", "0"),
        ],
        transactions=[
            {
                "id": "partial-conversion",
                "year": YEAR,
                "transaction_type": "roth_conversion",
                "amount": "50000",
                "taxable_amount": "12000",
                "source_account_id": "traditional",
                "destination_account_id": "roth",
            }
        ],
    )

    result = run_projection(request, federal_rules, {YEAR: rmd_rules})
    household = result.annual_household[0]
    agi = household.federal_agi_result

    assert agi is not None
    assert agi.federally_taxable_roth_conversions == Decimal("12000")
    assert agi.federal_adjusted_gross_income == Decimal("12000")
    assert household.gross_income == Decimal("0")
    assert next(
        row.ending_balance for row in result.annual_accounts if row.account_id == "roth"
    ) == Decimal("50000")
    assert (
        next(
            component
            for component in agi.components
            if component.component_type is FederalAgiComponentType.FEDERALLY_TAXABLE_ROTH_CONVERSION
        ).source_account_id
        == "traditional"
    )
    _assert_reconciles(result)


def test_taxable_and_tax_exempt_interest_agi(federal_rules: FederalTaxRules) -> None:
    request = _request(
        income=[
            {
                "id": "bank-interest",
                "income_type": "taxable_interest",
                "owner_id": "spouse_a",
                "annual_amount": "1000",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            },
            {
                "id": "muni-interest",
                "income_type": "tax_exempt_interest",
                "owner_id": "spouse_b",
                "annual_amount": "3000",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            },
        ]
    )

    result = run_projection(request, federal_rules)
    agi = result.annual_household[0].federal_agi_result

    assert agi is not None
    assert agi.taxable_interest == Decimal("1000")
    assert agi.tax_exempt_interest == Decimal("3000")
    assert agi.federal_adjusted_gross_income == Decimal("1000")
    assert agi.irmaa_magi == Decimal("4000")
    irmaa_record = irmaa_tax_record_from_annual_agi(agi)
    assert irmaa_record.federal_adjusted_gross_income == Decimal("1000")
    assert irmaa_record.tax_exempt_interest == Decimal("3000")
    assert irmaa_record.irmaa_magi == Decimal("4000")
    assert result.annual_household[0].federal_tax_result is not None
    assert result.annual_household[0].federal_tax_result.gross_income == Decimal("1000")
    _assert_reconciles(result)


def test_rmd_plus_qcd_agi(federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules) -> None:
    request = _request(
        people=[_person("spouse_a", "1950-01-01")],
        accounts=[
            _cash(),
            _account("traditional", "spouse_a", "traditional_ira", "237000"),
        ],
        giving_policy={
            "qcd_policy": {
                "enabled": True,
                "annual_qcd_floor": "1000",
                "target_mode": "fixed_floor",
            }
        },
        taxable_rmd_destination_account_by_owner={"spouse_a": "cash"},
        taxable_rmd_source_policy={"allocation_method": "proportional_to_account_rmd"},
    )

    result = run_projection(request, federal_rules, {YEAR: rmd_rules})
    household = result.annual_household[0]
    agi = household.federal_agi_result
    rmd = household.rmd_qcd_result

    assert agi is not None
    assert rmd is not None
    assert agi.taxable_rmd_distributions == rmd.taxable_rmd
    assert agi.federal_adjusted_gross_income == rmd.taxable_rmd
    qcd_component = next(
        component
        for component in agi.components
        if component.component_type is FederalAgiComponentType.QCD
    )
    assert qcd_component.included_in_federal_agi is False
    assert qcd_component.included_in_irmaa_magi is False
    _assert_reconciles(result)


def test_pretax_rollover_is_excluded_from_agi_and_irmaa(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[
            _cash(),
            _account("traditional-a", "spouse_a", "traditional_ira", "10000"),
            _account("traditional-b", "spouse_a", "traditional_ira", "0"),
        ],
        transactions=[
            {
                "id": "pretax-rollover",
                "year": YEAR,
                "transaction_type": "transfer",
                "amount": "4000",
                "source_account_id": "traditional-a",
                "destination_account_id": "traditional-b",
            }
        ],
    )

    result = run_projection(request, federal_rules, {YEAR: rmd_rules})
    household = result.annual_household[0]
    agi = household.federal_agi_result

    assert agi is not None
    assert agi.federal_adjusted_gross_income == Decimal("0")
    assert agi.irmaa_magi == Decimal("0")
    rollover_component = next(
        component
        for component in agi.components
        if component.component_type is FederalAgiComponentType.PRETAX_ROLLOVER
    )
    assert rollover_component.included_in_federal_agi is False
    assert rollover_component.included_in_irmaa_magi is False
    assert rollover_component.owner_id == "spouse_a"
    assert rollover_component.source_account_id == "traditional-a"
    assert rollover_component.source_transaction_ids == ("pretax-rollover",)
    assert household.gross_income == Decimal("0")
    _assert_reconciles(result)


def test_unsupported_agi_relevant_projected_income_fails(
    federal_rules: FederalTaxRules,
) -> None:
    request = _request(
        income=[
            {
                "id": "unknown-income",
                "income_type": "unspecified",
                "owner_id": "spouse_a",
                "annual_amount": "1000",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            }
        ]
    )

    with pytest.raises(ValueError, match="Federal AGI treatment is unsupported"):
        run_projection(request, federal_rules)
