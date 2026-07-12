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
def federal_tax_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


def _request(
    *,
    social_security: list[dict[str, object]],
    pension: str | None = None,
    conversion: str | None = None,
    cash_balance: str = "0",
) -> ProjectionRequest:
    accounts = [
        {
            "id": "cash",
            "owner_id": "spouse_a",
            "account_type": "cash",
            "starting_balance": cash_balance,
        }
    ]
    transactions: list[dict[str, object]] = []
    if conversion is not None:
        accounts.extend(
            [
                {
                    "id": "traditional",
                    "owner_id": "spouse_a",
                    "account_type": "traditional_ira",
                    "starting_balance": conversion,
                },
                {
                    "id": "roth",
                    "owner_id": "spouse_a",
                    "account_type": "roth_ira",
                    "starting_balance": "0",
                },
            ]
        )
        transactions.append(
            {
                "id": "conversion",
                "year": 2026,
                "transaction_type": "roth_conversion",
                "amount": conversion,
                "source_account_id": "traditional",
                "destination_account_id": "roth",
            }
        )
    income: list[dict[str, object]] = []
    if pension is not None:
        income.append(
            {
                "id": "pension",
                "income_type": "pension",
                "annual_amount": pension,
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            }
        )
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Social Security Test",
                "filing_status": "married_filing_jointly",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "people": [
                    {
                        "id": "spouse_a",
                        "name": "Spouse A",
                        "date_of_birth": "1960-01-01",
                    },
                    {
                        "id": "spouse_b",
                        "name": "Spouse B",
                        "date_of_birth": "1962-01-01",
                    },
                ],
                "accounts": accounts,
                "social_security": social_security,
                "income": income,
                "transactions": transactions,
                "federal_tax_payment_account_id": "cash",
            }
        }
    )


def _social_security(
    source_id: str,
    owner_id: str,
    monthly_benefit: str,
    claim_date: str = "2026-01-01",
    annual_cola: str = "0",
    benefit_subtype: str | None = None,
) -> dict[str, object]:
    source: dict[str, object] = {
        "id": source_id,
        "owner_id": owner_id,
        "claim_date": claim_date,
        "monthly_benefit": monthly_benefit,
        "annual_cola": annual_cola,
        "destination_account_id": "cash",
    }
    if benefit_subtype is not None:
        source["benefit_subtype"] = benefit_subtype
    return source


def _cash_balance(result: ProjectionResult) -> Decimal:
    return next(row.ending_balance for row in result.annual_accounts if row.account_id == "cash")


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


def test_social_security_is_spendable_when_not_taxable(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(social_security=[_social_security("benefit_a", "spouse_a", "1666.67")])

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]

    assert household.gross_income == Decimal("20000.04")
    assert household.social_security_taxation is not None
    assert household.social_security_taxation.taxable_social_security == 0
    assert household.taxes == 0
    assert _cash_balance(result) == Decimal("20000.04")
    _assert_reconciles(result)


def test_zero_federal_tax_after_taxable_social_security(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        social_security=[_social_security("benefit_a", "spouse_a", "1666.67")],
        pension="25000",
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]
    taxation = household.social_security_taxation

    assert taxation is not None
    assert taxation.taxable_social_security == Decimal("1500.01")
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.taxable_income == 0
    assert household.taxes == 0
    assert household.gross_income == Decimal("45000.04")
    _assert_reconciles(result)


def test_roth_conversion_increases_taxable_social_security_without_being_spendable(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        social_security=[_social_security("benefit_a", "spouse_a", "1666.67")],
        conversion="30000",
        cash_balance="1000",
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]
    taxation = household.social_security_taxation

    assert taxation is not None
    assert taxation.taxable_social_security == Decimal("4000.01")
    assert household.gross_income == Decimal("20000.04")
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("34000.01")
    assert household.taxes == Decimal("180.00")
    _assert_reconciles(result)


def test_pension_increases_taxable_social_security_and_is_spendable(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        social_security=[_social_security("benefit_a", "spouse_a", "1666.67")],
        pension="30000",
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]

    assert household.social_security_taxation is not None
    assert household.social_security_taxation.taxable_social_security == Decimal("4000.01")
    assert household.gross_income == Decimal("50000.04")
    assert household.taxes == Decimal("180.00")
    assert _cash_balance(result) == Decimal("49820.04")
    _assert_reconciles(result)


def test_two_spouses_keep_separate_benefit_records_and_joint_taxation(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        social_security=[
            _social_security("benefit_a", "spouse_a", "2000", benefit_subtype="retirement"),
            _social_security(
                "benefit_b",
                "spouse_b",
                "1000",
                claim_date="2026-07-01",
                benefit_subtype="disability",
            ),
        ],
        pension="30000",
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]
    taxation = household.social_security_taxation

    assert [benefit.owner_id for benefit in household.social_security_benefits] == [
        "spouse_a",
        "spouse_b",
    ]
    assert [benefit.gross_benefit for benefit in household.social_security_benefits] == [
        Decimal("24000"),
        Decimal("6000"),
    ]
    assert taxation is not None
    assert taxation.gross_social_security == Decimal("30000")
    assert taxation.provisional_income == Decimal("45000")
    assert taxation.taxable_social_security == Decimal("6850.00")
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("36850.00")
    assert household.taxes == Decimal("465.00")
    assert household.gross_income == Decimal("60000")
    assert _cash_balance(result) == Decimal("59535.00")
    assert (
        len(
            [
                entry
                for entry in result.transactions
                if entry.transaction_type is TransactionType.SOCIAL_SECURITY_INCOME
            ]
        )
        == 2
    )
    _assert_reconciles(result)


def test_cola_is_applied_each_january_and_monthly_amount_is_rounded(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        social_security=[
            _social_security(
                "benefit_a",
                "spouse_a",
                "1000",
                claim_date="2024-06-01",
                annual_cola="0.03",
            )
        ]
    )

    result = run_projection(request, federal_tax_rules)
    benefit = result.annual_household[0].social_security_benefits[0]

    assert benefit.monthly_benefit == Decimal("1060.90")
    assert benefit.months_received == 12
    assert benefit.gross_benefit == Decimal("12730.80")
    _assert_reconciles(result)
