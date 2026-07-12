from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.domain.models import ProjectionRequest
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules
from retirement_core.rules.rmd_qcd import RmdQcdRules

YEAR = 2027


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


def _request(**overrides: object) -> ProjectionRequest:
    plan: dict[str, object] = {
        "household_name": "Unsupported federal tax year",
        "filing_status": "married_filing_jointly",
        "start_date": f"{YEAR}-01-01",
        "end_date": f"{YEAR}-12-31",
        "people": [],
        "accounts": [
            {"id": "cash", "owner_id": "owner", "account_type": "cash", "starting_balance": "100"}
        ],
    }
    plan.update(overrides)
    return ProjectionRequest.model_validate({"plan": plan})


@pytest.mark.parametrize(
    ("income", "source_id"),
    [
        (
            {
                "id": "wages",
                "income_type": "w2_wages",
                "annual_taxable_amount": "1",
                "annual_spendable_cash_amount": "1",
            },
            "income:wages",
        ),
        (
            {"id": "pension", "income_type": "pension", "annual_amount": "1"},
            "income:pension",
        ),
        (
            {"id": "interest", "income_type": "taxable_interest", "annual_amount": "1"},
            "income:interest",
        ),
        (
            {"id": "muni", "income_type": "tax_exempt_interest", "annual_amount": "1"},
            "income:muni",
        ),
    ],
)
def test_unsupported_year_relevant_income_fails_closed(
    income: dict[str, str], source_id: str
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"tax year {YEAR}; triggering source IDs: {source_id}",
    ):
        run_projection(
            _request(
                income=[
                    {
                        **income,
                        "start_date": f"{YEAR}-01-01",
                        "destination_account_id": "cash",
                    }
                ]
            )
        )


@pytest.mark.parametrize(
    ("transaction", "accounts"),
    [
        (
            {
                "id": "conversion",
                "year": YEAR,
                "transaction_type": "roth_conversion",
                "amount": "1",
                "source_account_id": "traditional",
                "destination_account_id": "roth",
            },
            [
                {
                    "id": "cash",
                    "owner_id": "owner",
                    "account_type": "cash",
                    "starting_balance": "100",
                },
                {
                    "id": "traditional",
                    "owner_id": "owner",
                    "account_type": "traditional_ira",
                    "starting_balance": "1",
                },
                {
                    "id": "roth",
                    "owner_id": "owner",
                    "account_type": "roth_ira",
                    "starting_balance": "0",
                },
            ],
        ),
        (
            {
                "id": "pretax-withdrawal",
                "year": YEAR,
                "transaction_type": "withdrawal",
                "amount": "1",
                "source_account_id": "traditional",
                "destination_account_id": "cash",
            },
            [
                {
                    "id": "cash",
                    "owner_id": "owner",
                    "account_type": "cash",
                    "starting_balance": "100",
                },
                {
                    "id": "traditional",
                    "owner_id": "owner",
                    "account_type": "traditional_ira",
                    "starting_balance": "1",
                },
            ],
        ),
    ],
)
def test_unsupported_year_taxable_transactions_fail_closed(
    transaction: dict[str, object], accounts: list[dict[str, str]]
) -> None:
    with pytest.raises(ValueError, match=rf"tax year {YEAR}.*transaction:{transaction['id']}"):
        run_projection(_request(accounts=accounts, transactions=[transaction]))


def test_unsupported_year_taxable_rmd_fails_closed(rmd_rules: RmdQcdRules) -> None:
    with pytest.raises(ValueError, match=rf"tax year {YEAR}.*taxable-rmd:owner:traditional:{YEAR}"):
        run_projection(
            _request(
                people=[{"id": "owner", "name": "Owner", "date_of_birth": "1940-01-01"}],
                accounts=[
                    {
                        "id": "cash",
                        "owner_id": "owner",
                        "account_type": "cash",
                        "starting_balance": "0",
                    },
                    {
                        "id": "traditional",
                        "owner_id": "owner",
                        "account_type": "traditional_ira",
                        "starting_balance": "100000",
                    },
                ],
                taxable_rmd_destination_account_by_owner={"owner": "cash"},
                taxable_rmd_source_policy={"allocation_method": "proportional_to_account_rmd"},
            ),
            rmd_qcd_rules_by_year={YEAR: rmd_rules},
        )


def test_error_lists_all_triggering_source_ids() -> None:
    with pytest.raises(ValueError) as error:
        run_projection(
            _request(
                income=[
                    {
                        "id": "wages",
                        "income_type": "w2_wages",
                        "annual_taxable_amount": "1",
                        "annual_spendable_cash_amount": "1",
                        "start_date": f"{YEAR}-01-01",
                        "destination_account_id": "cash",
                    },
                    {
                        "id": "muni",
                        "income_type": "tax_exempt_interest",
                        "annual_amount": "1",
                        "start_date": f"{YEAR}-01-01",
                        "destination_account_id": "cash",
                    },
                ]
            )
        )
    assert "income:wages" in str(error.value)
    assert "income:muni" in str(error.value)


def test_va_disability_only_year_succeeds_without_federal_results() -> None:
    result = run_projection(
        _request(
            income=[
                {
                    "id": "va",
                    "income_type": "va_disability",
                    "annual_taxable_amount": "0",
                    "annual_spendable_cash_amount": "100",
                    "start_date": f"{YEAR}-01-01",
                    "destination_account_id": "cash",
                }
            ]
        )
    )
    household = result.annual_household[0]
    assert household.federal_agi_result is None
    assert household.federal_tax_result is None
    assert household.gross_income == Decimal("100")
    assert household.cash_surplus == Decimal("100")


def test_nontax_cash_activity_does_not_require_federal_processing() -> None:
    result = run_projection(
        _request(
            accounts=[
                {
                    "id": "cash",
                    "owner_id": "owner",
                    "account_type": "cash",
                    "starting_balance": "100",
                },
                {
                    "id": "taxable",
                    "owner_id": "owner",
                    "account_type": "taxable",
                    "starting_balance": "0",
                },
            ],
            transactions=[
                {
                    "id": "contribution",
                    "year": YEAR,
                    "transaction_type": "contribution",
                    "amount": "10",
                    "source_account_id": "cash",
                    "destination_account_id": "taxable",
                },
                {
                    "id": "transfer",
                    "year": YEAR,
                    "transaction_type": "transfer",
                    "amount": "5",
                    "source_account_id": "taxable",
                    "destination_account_id": "cash",
                },
                {
                    "id": "spending",
                    "year": YEAR,
                    "transaction_type": "spending",
                    "amount": "5",
                    "source_account_id": "cash",
                },
            ],
        )
    )
    household = result.annual_household[0]
    assert household.federal_agi_result is None
    assert household.federal_tax_result is None


def test_2026_federal_processing_remains_supported(federal_rules: FederalTaxRules) -> None:
    request = _request(
        start_date="2026-01-01",
        end_date="2026-12-31",
        income=[
            {
                "id": "wages",
                "income_type": "w2_wages",
                "annual_taxable_amount": "100",
                "annual_spendable_cash_amount": "100",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            }
        ],
    )
    result = run_projection(request, federal_rules)
    assert result.annual_household[0].federal_tax_result is not None
