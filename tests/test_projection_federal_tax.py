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
    source: str,
    destination: str | None = None,
) -> dict[str, str | int]:
    transaction: dict[str, str | int] = {
        "id": transaction_id,
        "year": 2026,
        "transaction_type": transaction_type,
        "amount": amount,
        "source_account_id": source,
    }
    if destination is not None:
        transaction["destination_account_id"] = destination
    return transaction


def _request(
    *,
    accounts: list[dict[str, str]],
    pension: str | None = None,
    pension_taxable: bool = True,
    income_type: str = "pension",
    transactions: list[dict[str, str | int]] | None = None,
    payment_account_id: str | None = "cash",
) -> ProjectionRequest:
    income: list[dict[str, object]] = []
    if pension is not None:
        income.append(
            {
                "id": "pension",
                "income_type": income_type,
                "annual_amount": pension,
                "start_date": "2026-01-01",
                "end_date": None,
                "destination_account_id": "cash",
                "taxable_federal": pension_taxable,
            }
        )
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "2026 Tax Test",
                "filing_status": "married_filing_jointly",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "people": [],
                "accounts": accounts,
                "income": income,
                "transactions": transactions or [],
                "federal_tax_payment_account_id": payment_account_id,
            }
        }
    )


def _balance(result: ProjectionResult, account_id: str) -> Decimal:
    return next(
        row.ending_balance for row in result.annual_accounts if row.account_id == account_id
    )


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


def test_pension_income_is_taxed_and_paid_from_cash(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(accounts=[_account("cash", "cash", "0")], pension="100000")

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]
    tax = household.federal_tax_result

    assert tax is not None
    assert tax.gross_income == Decimal("100000")
    assert tax.standard_deduction == Decimal("32200")
    assert tax.taxable_income == Decimal("67800")
    assert tax.total_federal_tax == Decimal("7640.00")
    assert household.gross_income == Decimal("100000")
    assert household.cash_surplus == Decimal("92360.00")
    assert _balance(result, "cash") == Decimal("92360.00")
    assert result.provenance["federal_tax_dataset_id"] == "US-FED-2026-v1"
    _assert_reconciles(result)


def test_roth_conversion_and_pension_are_both_ordinary_income(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        accounts=[
            _account("cash", "cash", "0"),
            _account("traditional", "traditional_ira", "100000"),
            _account("roth", "roth_ira", "0"),
        ],
        pension="50000",
        transactions=[_transaction("convert", "roth_conversion", "50000", "traditional", "roth")],
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]
    tax = household.federal_tax_result

    assert tax is not None
    assert tax.gross_income == Decimal("100000")
    assert household.gross_income == Decimal("50000")
    assert household.cash_withdrawals == 0
    assert household.taxes == Decimal("7640.00")
    assert _balance(result, "cash") == Decimal("42360.00")
    assert _balance(result, "traditional") == Decimal("50000")
    assert _balance(result, "roth") == Decimal("50000")
    _assert_reconciles(result)


def test_income_below_standard_deduction_has_no_tax(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(accounts=[_account("cash", "cash", "0")], pension="30000")

    result = run_projection(request, federal_tax_rules)
    tax = result.annual_household[0].federal_tax_result

    assert tax is not None
    assert tax.taxable_income == 0
    assert tax.total_federal_tax == 0
    assert tax.marginal_bracket is None
    assert _balance(result, "cash") == Decimal("30000")
    assert not any(
        entry.transaction_type is TransactionType.FEDERAL_TAX_PAYMENT
        for entry in result.transactions
    )
    _assert_reconciles(result)


def test_conversion_tax_can_create_household_cash_deficit(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        accounts=[
            _account("cash", "cash", "10000"),
            _account("traditional", "traditional_ira", "100000"),
            _account("roth", "roth_ira", "0"),
        ],
        transactions=[_transaction("convert", "roth_conversion", "100000", "traditional", "roth")],
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]

    assert household.gross_income == 0
    assert household.taxes == Decimal("7640.00")
    assert household.cash_surplus == Decimal("-7640.00")
    assert household.giving_target == 0
    assert _balance(result, "cash") == Decimal("2360.00")
    _assert_reconciles(result)


def test_zero_tax_year_has_explicit_zero_result(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(accounts=[_account("cash", "cash", "0")])

    result = run_projection(request, federal_tax_rules)
    tax = result.annual_household[0].federal_tax_result

    assert tax is not None
    assert tax.gross_income == 0
    assert tax.taxable_income == 0
    assert tax.total_federal_tax == 0
    assert tax.marginal_bracket is None
    assert result.annual_household[0].cash_surplus == 0
    _assert_reconciles(result)


def test_internal_transfer_does_not_affect_tax_or_household_cash(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        accounts=[
            _account("cash", "cash", "0"),
            _account("taxable_one", "taxable", "30000"),
            _account("taxable_two", "taxable", "0"),
        ],
        pension="50000",
        transactions=[_transaction("move", "transfer", "10000", "taxable_one", "taxable_two")],
    )

    result = run_projection(request, federal_tax_rules)
    household = result.annual_household[0]
    tax = household.federal_tax_result

    assert tax is not None
    assert tax.gross_income == Decimal("50000")
    assert tax.total_federal_tax == Decimal("1780.00")
    assert household.gross_income == Decimal("50000")
    assert household.cash_withdrawals == 0
    assert household.cash_surplus == Decimal("48220.00")
    assert _balance(result, "taxable_one") == Decimal("20000")
    assert _balance(result, "taxable_two") == Decimal("10000")
    _assert_reconciles(result)


def test_2026_pretax_withdrawal_fails_explicitly(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        accounts=[
            _account("cash", "cash", "0"),
            _account("traditional", "traditional_ira", "50000"),
        ],
        transactions=[_transaction("withdraw", "withdrawal", "10000", "traditional", "cash")],
    )

    with pytest.raises(ValueError, match="Taxable-distribution treatment is not implemented"):
        run_projection(request, federal_tax_rules)


def test_unsupported_taxable_income_type_fails(
    federal_tax_rules: FederalTaxRules,
) -> None:
    request = _request(
        accounts=[_account("cash", "cash", "0")],
        pension="50000",
        income_type="unspecified",
    )

    with pytest.raises(ValueError, match="Federal AGI treatment is unsupported"):
        run_projection(request, federal_tax_rules)


def test_tax_payment_account_must_be_cash(federal_tax_rules: FederalTaxRules) -> None:
    request = _request(
        accounts=[
            _account("cash", "cash", "0"),
            _account("taxable", "taxable", "100000"),
        ],
        pension="50000",
        payment_account_id="taxable",
    )

    with pytest.raises(ValueError, match="Federal tax payment source must be cash"):
        run_projection(request, federal_tax_rules)
