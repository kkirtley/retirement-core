from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus, IncomeStopRule, TransactionType
from retirement_core.domain.models import IncomeInput, ProjectionRequest
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.missouri_tax import MissouriTaxRules
from retirement_core.rules.models import FederalTaxRules


@pytest.fixture(scope="module")
def federal_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.fixture(scope="module")
def missouri_rules() -> MissouriTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_applicable_dataset(
        "missouri_tax", "US-MO", 2026
    )
    return MissouriTaxRules.from_dataset(dataset)


def _request(**overrides: object) -> ProjectionRequest:
    plan: dict[str, object] = {
        "household_name": "Recurring income",
        "filing_status": "married_filing_jointly",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "people": [
            {"id": "kevin", "name": "Kevin", "date_of_birth": "1960-01-01"},
            {"id": "joan", "name": "Joan", "date_of_birth": "1961-01-01"},
        ],
        "accounts": [
            {"id": "cash", "owner_id": "kevin", "account_type": "cash", "starting_balance": "0"}
        ],
        "federal_tax_payment_account_id": "cash",
    }
    plan.update(overrides)
    return ProjectionRequest.model_validate({"plan": plan})


def test_w2_taxable_wages_can_exceed_cash_and_settle_only_balance_due(
    federal_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "195000",
                    "annual_spendable_cash_amount": "130000",
                    "annual_federal_income_tax_withholding": "20000",
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ]
        ),
        federal_rules,
    )
    household = result.annual_household[0]
    assert household.gross_income == Decimal("130000")
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("195000")
    assert household.federal_withholding == Decimal("20000")
    assert household.federal_tax_payment == household.total_federal_liability - Decimal("20000")
    assert household.federal_agi_result is not None
    assert household.federal_agi_result.federal_adjusted_gross_income == Decimal("195000")


def test_va_disability_is_cash_but_not_agi_or_irmaa(federal_rules: FederalTaxRules) -> None:
    result = run_projection(
        _request(
            income=[
                {
                    "id": "kevin-va",
                    "income_type": "va_disability",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "0",
                    "annual_spendable_cash_amount": "24000",
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ]
        ),
        federal_rules,
    )
    household = result.annual_household[0]
    assert household.gross_income == Decimal("24000")
    assert household.federal_agi_result is not None
    assert household.federal_agi_result.federal_adjusted_gross_income == Decimal("0")
    assert household.federal_agi_result.irmaa_magi == Decimal("0")


def test_va_disability_override_cannot_add_taxable_income_or_withholding() -> None:
    with pytest.raises(ValueError, match="VA_DISABILITY override"):
        IncomeInput.model_validate(
            {
                "id": "kevin-va",
                "income_type": "va_disability",
                "owner_id": "kevin",
                "annual_taxable_amount": "0",
                "annual_spendable_cash_amount": "24000",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
                "annual_overrides": {
                    "2026": {"taxable_amount": "1", "spendable_cash_amount": "24000"}
                },
            }
        )


def test_partial_first_year_requires_override() -> None:
    with pytest.raises(ValueError, match="requires an annual override"):
        _request(
            start_date="2026-07-01",
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "195000",
                    "annual_spendable_cash_amount": "65000",
                    "start_date": "2020-01-01",
                    "destination_account_id": "cash",
                }
            ],
        )


def test_partial_first_year_override_keeps_full_tax_year_wages_and_partial_cash(
    federal_rules: FederalTaxRules,
) -> None:
    request = _request(
        start_date="2026-07-01",
        income=[
            {
                "id": "kevin-w2",
                "income_type": "w2_wages",
                "owner_id": "kevin",
                "annual_taxable_amount": "195000",
                "annual_spendable_cash_amount": "130000",
                "start_date": "2020-01-01",
                "destination_account_id": "cash",
                "annual_overrides": {
                    "2026": {
                        "taxable_amount": "195000",
                        "spendable_cash_amount": "65000",
                    }
                },
            }
        ],
    )
    result = run_projection(request, federal_rules)
    household = result.annual_household[0]
    assert household.gross_income == Decimal("65000")
    assert household.federal_agi_result is not None
    assert household.federal_agi_result.federal_adjusted_gross_income == Decimal("195000")


@pytest.mark.parametrize(
    ("input_data", "expected"),
    [
        ({"end_date": "2026-12-31"}, IncomeStopRule.EXPLICIT_END_DATE),
        ({}, IncomeStopRule.CONTINUES_FOR_LIFE),
    ],
)
def test_legacy_stop_rule_migration(input_data: dict[str, str], expected: IncomeStopRule) -> None:
    income = IncomeInput.model_validate(
        {
            "id": "interest",
            "income_type": "taxable_interest",
            "owner_id": "kevin",
            "annual_amount": "100",
            "start_date": "2026-01-01",
            "destination_account_id": "cash",
            **input_data,
        }
    )
    assert income.stop_rule is expected
    assert income.annual_taxable_amount == Decimal("100")
    assert income.annual_spendable_cash_amount == Decimal("100")


def test_stop_rule_validation() -> None:
    with pytest.raises(ValueError, match="EXPLICIT_END_DATE"):
        IncomeInput.model_validate(
            {
                "id": "wage",
                "income_type": "w2_wages",
                "owner_id": "kevin",
                "annual_taxable_amount": "1",
                "annual_spendable_cash_amount": "1",
                "start_date": "2026-01-01",
                "stop_rule": "explicit_end_date",
                "destination_account_id": "cash",
            }
        )


def test_owner_retirement_stop_rule_requires_owner_retirement_date() -> None:
    with pytest.raises(ValueError, match="OWNER_RETIREMENT_DATE"):
        _request(
            income=[
                {
                    "id": "wage",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "1",
                    "annual_spendable_cash_amount": "1",
                    "start_date": "2026-01-01",
                    "stop_rule": "owner_retirement_date",
                    "destination_account_id": "cash",
                }
            ]
        )


def test_full_year_monthly_proration_uses_first_day_of_month(
    federal_rules: FederalTaxRules,
) -> None:
    result = run_projection(
        _request(
            income=[
                {
                    "id": "wage",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "1200",
                    "annual_spendable_cash_amount": "1200",
                    "start_date": "2026-02-01",
                    "destination_account_id": "cash",
                }
            ]
        ),
        federal_rules,
    )
    resolved = result.annual_household[0].resolved_income[0]
    assert resolved.taxable_amount == Decimal("1100.00")
    assert resolved.spendable_cash_amount == Decimal("1100.00")


def test_mid_month_boundary_requires_override(federal_rules: FederalTaxRules) -> None:
    with pytest.raises(ValueError, match="mid-month"):
        run_projection(
            _request(
                income=[
                    {
                        "id": "wage",
                        "income_type": "w2_wages",
                        "owner_id": "kevin",
                        "annual_taxable_amount": "1200",
                        "annual_spendable_cash_amount": "1200",
                        "start_date": "2026-02-15",
                        "destination_account_id": "cash",
                    }
                ]
            ),
            federal_rules,
        )
    with pytest.raises(ValueError, match="CONTINUES_FOR_LIFE"):
        IncomeInput.model_validate(
            {
                "id": "wage",
                "income_type": "w2_wages",
                "owner_id": "kevin",
                "annual_taxable_amount": "1",
                "annual_spendable_cash_amount": "1",
                "start_date": "2026-01-01",
                "stop_rule": "continues_for_life",
                "end_date": "2026-12-31",
                "destination_account_id": "cash",
            }
        )


def test_wages_are_missouri_income_without_retirement_subtraction(
    federal_rules: FederalTaxRules, missouri_rules: MissouriTaxRules
) -> None:
    result = run_projection(
        _request(
            state_residency={"state_code": "MO", "status": "full_year_resident"},
            missouri_tax_payment_account_id="cash",
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "100000",
                    "annual_spendable_cash_amount": "100000",
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ],
        ),
        federal_rules,
        missouri_tax_rules_by_year={2026: missouri_rules},
    )
    state = result.annual_household[0].missouri_tax_result
    assert state is not None
    assert state.gross_income_basis == Decimal("100000")
    assert state.private_retirement_subtraction == Decimal("0")
    assert state.public_pension_subtraction == Decimal("0")


@pytest.mark.parametrize(
    ("federal_withholding", "expected_type"),
    [("0", TransactionType.FEDERAL_TAX_PAYMENT), ("999999", TransactionType.FEDERAL_TAX_REFUND)],
)
def test_federal_settlement_can_be_payment_or_refund(
    federal_rules: FederalTaxRules,
    federal_withholding: str,
    expected_type: TransactionType,
) -> None:
    result = run_projection(
        _request(
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "100000",
                    "annual_spendable_cash_amount": "1000000",
                    "annual_federal_income_tax_withholding": federal_withholding,
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ]
        ),
        federal_rules,
    )
    settlement = next(
        entry
        for entry in result.transactions
        if entry.transaction_type
        in {
            TransactionType.FEDERAL_TAX_PAYMENT,
            TransactionType.FEDERAL_TAX_REFUND,
        }
    )
    assert settlement.transaction_type is expected_type
    assert settlement.spendable_income == Decimal("0")
    assert result.annual_household[0].federal_agi_result is not None
    assert result.annual_household[0].federal_agi_result.federal_adjusted_gross_income == Decimal(
        "100000"
    )


def test_exact_withholding_creates_no_additional_cash_settlement(
    federal_rules: FederalTaxRules,
) -> None:
    base = run_projection(
        _request(
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "100000",
                    "annual_spendable_cash_amount": "100000",
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ]
        ),
        federal_rules,
    )
    liability = base.annual_household[0].total_federal_liability
    exact = run_projection(
        _request(
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "100000",
                    "annual_spendable_cash_amount": "100000",
                    "annual_federal_income_tax_withholding": str(liability),
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ]
        ),
        federal_rules,
    )
    household = exact.annual_household[0]
    assert household.federal_tax_payment == Decimal("0")
    assert household.federal_tax_refund == Decimal("0")


def test_missouri_withholding_settles_as_a_refund(
    federal_rules: FederalTaxRules, missouri_rules: MissouriTaxRules
) -> None:
    result = run_projection(
        _request(
            state_residency={"state_code": "MO", "status": "full_year_resident"},
            missouri_tax_payment_account_id="cash",
            income=[
                {
                    "id": "kevin-w2",
                    "income_type": "w2_wages",
                    "owner_id": "kevin",
                    "annual_taxable_amount": "100000",
                    "annual_spendable_cash_amount": "1000000",
                    "annual_state_income_tax_withholding": "999999",
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ],
        ),
        federal_rules,
        missouri_tax_rules_by_year={2026: missouri_rules},
    )
    household = result.annual_household[0]
    assert household.total_missouri_liability > 0
    assert household.missouri_tax_payment == Decimal("0")
    assert household.missouri_tax_refund == Decimal("999999") - household.total_missouri_liability
    assert any(
        entry.transaction_type is TransactionType.MISSOURI_TAX_REFUND
        for entry in result.transactions
    )


def test_self_employment_is_explicitly_unsupported(federal_rules: FederalTaxRules) -> None:
    with pytest.raises(
        ValueError, match="self-employment tax projection integration is not implemented"
    ):
        run_projection(
            _request(
                income=[
                    {
                        "id": "business",
                        "income_type": "self_employment_net_income",
                        "owner_id": "kevin",
                        "annual_taxable_amount": "100",
                        "annual_spendable_cash_amount": "100",
                        "start_date": "2026-01-01",
                        "destination_account_id": "cash",
                        "self_employment_tax_base": {
                            "business_id": "business",
                            "net_business_profit": "100",
                        },
                    }
                ]
            ),
            federal_rules,
        )
