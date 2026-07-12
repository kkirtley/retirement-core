from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus, TransactionType
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.missouri_tax import MissouriTaxRules
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


@pytest.fixture(scope="module")
def missouri_rules() -> MissouriTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_applicable_dataset(
        "missouri_tax", "US-MO", 2026
    )
    return MissouriTaxRules.from_dataset(dataset)


def request(
    *,
    pension: str | None = None,
    pension_type: str = "public",
    social_security: bool = False,
    rmd: bool = False,
    qcd_floor: str | None = None,
    payment_account: str = "cash",
    conversion: str | None = None,
    taxable_conversion: str | None = None,
    conversion_method: str = "direct",
    pretax_rollover: bool = False,
) -> ProjectionRequest:
    accounts: list[dict[str, object]] = [
        {
            "id": "cash",
            "owner_id": "joan",
            "account_type": "cash",
            "starting_balance": "10000" if conversion is not None else "0",
        }
    ]
    if payment_account != "cash":
        accounts.append(
            {
                "id": payment_account,
                "owner_id": "kevin",
                "account_type": "cash",
                "starting_balance": "0",
            }
        )
    income: list[dict[str, object]] = []
    if pension is not None:
        income.append(
            {
                "id": "joan-imrf",
                "income_type": "pension",
                "pension_type": pension_type,
                "owner_id": "joan",
                "annual_amount": pension,
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            }
        )
    social: list[dict[str, object]] = []
    if social_security:
        social.append(
            {
                "id": "joan-ss",
                "owner_id": "joan",
                "benefit_subtype": "retirement",
                "claim_date": "2026-01-01",
                "monthly_benefit": "2000",
                "destination_account_id": "cash",
            }
        )
    giving_policy: dict[str, object] = {"qcd_policy": {"enabled": False}}
    destinations: dict[str, str] = {}
    source_policy: dict[str, object] | None = None
    transactions: list[dict[str, object]] = []
    if rmd:
        accounts.append(
            {
                "id": "joan-ira",
                "owner_id": "joan",
                "account_type": "traditional_ira",
                "starting_balance": "284400",
            }
        )
        destinations = {"joan": "cash"}
        source_policy = {"allocation_method": "proportional_to_account_rmd"}
        if qcd_floor is not None:
            giving_policy = {
                "qcd_policy": {
                    "enabled": True,
                    "annual_qcd_floor": qcd_floor,
                    "target_mode": "fixed_floor",
                }
            }
    if conversion is not None:
        accounts.extend(
            [
                {
                    "id": "conversion-traditional",
                    "owner_id": "joan",
                    "account_type": "traditional_ira",
                    "starting_balance": conversion,
                },
                {
                    "id": "conversion-roth",
                    "owner_id": "joan",
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
                "taxable_amount": taxable_conversion,
                "roth_conversion_method": conversion_method,
                "source_account_id": "conversion-traditional",
                "destination_account_id": "conversion-roth",
            }
        )
    if pretax_rollover:
        accounts.extend(
            [
                {
                    "id": "pretax-one",
                    "owner_id": "joan",
                    "account_type": "traditional_ira",
                    "starting_balance": "10000",
                },
                {
                    "id": "pretax-two",
                    "owner_id": "joan",
                    "account_type": "traditional_ira",
                    "starting_balance": "0",
                },
            ]
        )
        transactions.append(
            {
                "id": "pretax-rollover",
                "year": 2026,
                "transaction_type": "transfer",
                "amount": "10000",
                "source_account_id": "pretax-one",
                "destination_account_id": "pretax-two",
            }
        )
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Missouri household",
                "filing_status": "married_filing_jointly",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "people": [
                    {"id": "kevin", "name": "Kevin", "date_of_birth": "1950-01-01"},
                    {
                        "id": "joan",
                        "name": "Joan",
                        "date_of_birth": (
                            "1960-01-01"
                            if conversion is not None or pretax_rollover
                            else "1950-01-01"
                        ),
                    },
                ],
                "accounts": accounts,
                "income": income,
                "social_security": social,
                "transactions": transactions,
                "giving_policy": giving_policy,
                "taxable_rmd_destination_account_by_owner": destinations,
                "taxable_rmd_source_policy": source_policy,
                "federal_tax_payment_account_id": "cash",
                "state_residency": {"state_code": "MO", "status": "full_year_resident"},
                "missouri_tax_payment_account_id": payment_account,
            }
        }
    )


def run(
    projection_request: ProjectionRequest,
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> ProjectionResult:
    return run_projection(
        projection_request,
        federal_rules,
        {2026: rmd_rules},
        {2026: missouri_rules},
    )


def assert_reconciles(result: ProjectionResult) -> None:
    for account in result.annual_accounts:
        reconcile_account(account)
    household = result.annual_household[0]
    federal = sum((item.federal_tax_payment for item in result.transactions), Decimal("0"))
    missouri = sum((item.missouri_tax_payment for item in result.transactions), Decimal("0"))
    reconcile_household_cash(
        household.gross_income,
        household.cash_withdrawals,
        household.spending,
        household.contributions,
        household.cash_surplus,
        federal_tax=federal,
        missouri_tax=missouri,
    )


def test_joan_imrf_public_pension_and_tax_payment_reduce_cash(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(request(pension="100000"), federal_rules, rmd_rules, missouri_rules)
    household = result.annual_household[0]
    state = household.missouri_tax_result

    assert state is not None
    assert state.public_pension_subtraction == Decimal("48967")
    assert state.total_tax > 0
    payment = next(
        item
        for item in result.transactions
        if item.transaction_type is TransactionType.MISSOURI_TAX_PAYMENT
    )
    assert payment.missouri_tax_payment == state.total_tax
    assert household.taxes == household.federal_tax_result.total_federal_tax + state.total_tax
    assert_reconciles(result)


def test_public_pension_plus_social_security_interaction(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(
        request(pension="50000", social_security=True),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )
    state = result.annual_household[0].missouri_tax_result

    assert state is not None
    assert state.social_security_subtraction > 0
    assert state.public_pension_subtraction + state.social_security_subtraction == Decimal("48967")
    assert_reconciles(result)


def test_taxable_rmd_is_private_retirement_and_qcd_is_excluded(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(
        request(rmd=True, qcd_floor="7000"),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )
    household = result.annual_household[0]
    state = household.missouri_tax_result

    assert state is not None
    assert household.rmd_qcd_result is not None
    assert household.rmd_qcd_result.qcd == Decimal("7000.00")
    assert household.rmd_qcd_result.taxable_rmd == Decimal("5000.00")
    assert state.gross_income_basis == Decimal("5000.00")
    assert state.private_retirement_subtraction == Decimal("5000.00")
    assert_reconciles(result)


def test_private_pension_projection(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(
        request(pension="10000", pension_type="private"),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )
    state = result.annual_household[0].missouri_tax_result

    assert state is not None
    assert state.private_retirement_subtraction == Decimal("6000")
    assert_reconciles(result)


def test_missouri_payment_fails_when_configured_cash_is_insufficient(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    projection_request = request(pension="100000", payment_account="state-cash")

    with pytest.raises(ValueError, match="would make account state-cash negative"):
        run(projection_request, federal_rules, rmd_rules, missouri_rules)


@pytest.mark.parametrize("method", ["direct", "trustee_to_trustee", "sixty_day_rollover"])
def test_roth_conversion_methods_share_missouri_classification_and_remain_nonspendable(
    method: str,
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(
        request(conversion="50000", conversion_method=method),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )
    household = result.annual_household[0]
    state = household.missouri_tax_result

    assert state is not None
    assert state.gross_income_basis == Decimal("50000")
    assert state.private_retirement_subtraction == 0
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("50000")
    assert household.gross_income == 0
    assert household.cash_withdrawals == 0
    conversion_entry = next(
        item
        for item in result.transactions
        if item.transaction_type is TransactionType.ROTH_CONVERSION
    )
    assert conversion_entry.roth_conversion_method == method
    assert conversion_entry.taxable_amount == Decimal("50000")
    balances = {item.account_id: item.ending_balance for item in result.annual_accounts}
    assert balances["conversion-traditional"] == 0
    assert balances["conversion-roth"] == Decimal("50000")
    assert_reconciles(result)


def test_partially_taxable_conversion_uses_only_taxable_amount(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(
        request(conversion="50000", taxable_conversion="12000"),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )
    household = result.annual_household[0]
    state = household.missouri_tax_result

    assert state is not None
    assert state.gross_income_basis == Decimal("12000")
    assert state.private_retirement_subtraction == 0
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("12000")
    assert_reconciles(result)


def test_pretax_rollover_is_excluded_from_federal_and_missouri_income(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = run(
        request(pretax_rollover=True),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )
    household = result.annual_household[0]
    state = household.missouri_tax_result

    assert state is not None
    assert state.gross_income_basis == 0
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == 0
    assert household.gross_income == 0
    assert_reconciles(result)
