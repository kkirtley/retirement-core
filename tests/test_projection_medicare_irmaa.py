from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus, TransactionType
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules, MedicareIrmaaRules


@pytest.fixture(scope="module")
def federal_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.fixture(scope="module")
def medicare_rules_2026() -> MedicareIrmaaRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset(
        "medicare_irmaa", "US-FED", 2026
    )
    return MedicareIrmaaRules.from_dataset(dataset)


@pytest.fixture(scope="module")
def medicare_rules_2028() -> MedicareIrmaaRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset(
        "medicare_irmaa", "US-FED", 2026
    )
    return MedicareIrmaaRules.from_dataset(
        dataset.model_copy(
            update={
                "dataset_id": "US-FED-MEDICARE-IRMAA-2028-test",
                "premium_year": 2028,
                "effective_from": date(2028, 1, 1),
                "effective_to": date(2028, 12, 31),
            }
        )
    )


def _person(owner_id: str) -> dict[str, str]:
    return {"id": owner_id, "name": owner_id, "date_of_birth": "1960-01-01"}


def _account(account_id: str, owner_id: str, account_type: str, balance: str) -> dict[str, str]:
    return {
        "id": account_id,
        "owner_id": owner_id,
        "account_type": account_type,
        "starting_balance": balance,
    }


def _medicare_person(
    owner_id: str,
    *,
    part_b: str | None = "2028-01-01",
    part_d: str | None = "2028-01-01",
    part_d_premium: str = "50.00",
) -> dict[str, str]:
    person = {"owner_id": owner_id, "part_d_plan_monthly_premium": part_d_premium}
    if part_b is not None:
        person["part_b_enrollment_date"] = part_b
    if part_d is not None:
        person["part_d_enrollment_date"] = part_d
    return person


def _request(
    *,
    start_year: int = 2028,
    end_year: int = 2028,
    filing_status: str = "married_filing_jointly",
    cash_balance: str = "100000",
    cash_account_type: str = "cash",
    income_2026: str | None = None,
    historical_magi: str | None = "300000",
    base_mode: str = "modeled_separately",
    people: list[dict[str, str]] | None = None,
    medicare_people: list[dict[str, str]] | None = None,
    payment_account_id: str = "premium-cash",
    accounts: list[dict[str, str]] | None = None,
) -> ProjectionRequest:
    plan_people = people or [_person("spouse_a"), _person("spouse_b")]
    plan_accounts = accounts or [
        _account("premium-cash", "spouse_a", cash_account_type, cash_balance),
        _account("other-cash", "spouse_b", "cash", "0"),
    ]
    income = []
    if income_2026 is not None:
        income.append(
            {
                "id": "pension",
                "income_type": "pension",
                "owner_id": "spouse_a",
                "annual_amount": income_2026,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "destination_account_id": "premium-cash",
            }
        )
    historical_records = []
    if historical_magi is not None:
        historical_records.append(
            {
                "tax_year": 2026,
                "filing_status": filing_status,
                "federal_adjusted_gross_income": historical_magi,
            }
        )
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Medicare Projection Test",
                "filing_status": filing_status,
                "start_date": f"{start_year}-01-01",
                "end_date": f"{end_year}-12-31",
                "people": plan_people,
                "accounts": plan_accounts,
                "income": income,
                "federal_tax_payment_account_id": "premium-cash",
                "medicare": {
                    "premium_payment_account_id": payment_account_id,
                    "base_premium_mode": base_mode,
                    "people": medicare_people
                    or [
                        _medicare_person("spouse_a"),
                        _medicare_person("spouse_b"),
                    ],
                    "historical_tax_records": historical_records,
                },
            }
        }
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
            federal_tax=sum(
                entry.federal_tax_payment
                for entry in result.transactions
                if entry.year == household.year
            ),
            missouri_tax=sum(
                entry.missouri_tax_payment
                for entry in result.transactions
                if entry.year == household.year
            ),
            medicare_costs=household.medicare_costs,
        )


def _medicare_entries(result: ProjectionResult) -> list[object]:
    return [
        entry
        for entry in result.transactions
        if entry.transaction_type is TransactionType.MEDICARE_PAYMENT
    ]


def test_2028_premiums_use_2026_projected_agi(
    federal_rules: FederalTaxRules, medicare_rules_2028: MedicareIrmaaRules
) -> None:
    request = _request(start_year=2026, end_year=2028, income_2026="300000")

    result = run_projection(
        request, federal_rules, medicare_irmaa_rules_by_year={2028: medicare_rules_2028}
    )
    household_2028 = result.annual_household[-1]
    irmaa = household_2028.irmaa_result

    assert irmaa is not None
    assert irmaa.magi.source == "completed_projection"
    assert irmaa.magi_tax_year == 2026
    assert irmaa.magi.federal_adjusted_gross_income == Decimal("300000")
    assert irmaa.tier_index == 2
    assert household_2028.medicare_costs == Decimal("11839.20")
    assert sum(entry.medicare_payment for entry in _medicare_entries(result)) == Decimal("11839.20")
    _assert_reconciles(result)


def test_historical_record_fallback(medicare_rules_2028: MedicareIrmaaRules) -> None:
    request = _request(historical_magi="250000")

    result = run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    irmaa = result.annual_household[0].irmaa_result

    assert irmaa is not None
    assert irmaa.magi.source == "historical_tax_record"
    assert irmaa.tier_index == 1
    _assert_reconciles(result)


def test_missing_lookback_fails(medicare_rules_2028: MedicareIrmaaRules) -> None:
    request = _request(historical_magi=None)

    with pytest.raises(ValueError, match="requires MAGI for tax year 2026"):
        run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})


def test_one_and_two_enrolled_spouses(medicare_rules_2028: MedicareIrmaaRules) -> None:
    one_spouse = _request(
        medicare_people=[_medicare_person("spouse_a")],
    )
    two_spouses = _request()

    one = run_projection(one_spouse, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    two = run_projection(two_spouses, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})

    assert one.annual_household[0].medicare_costs == Decimal("5919.60")
    assert two.annual_household[0].medicare_costs == Decimal("11839.20")
    _assert_reconciles(one)
    _assert_reconciles(two)


def test_partial_first_year_and_different_enrollment_dates(
    medicare_rules_2028: MedicareIrmaaRules,
) -> None:
    request = _request(
        medicare_people=[
            _medicare_person("spouse_a", part_b="2028-07-31", part_d="2028-10-01"),
            _medicare_person("spouse_b", part_b=None, part_d="2028-03-15"),
        ]
    )

    result = run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    irmaa = result.annual_household[0].irmaa_result

    assert irmaa is not None
    assert [(item.owner_id, item.part_b_months, item.part_d_months) for item in irmaa.people] == [
        ("spouse_a", 6, 3),
        ("spouse_b", 0, 10),
    ]
    assert result.annual_household[0].medicare_costs == Decimal("3572.30")
    _assert_reconciles(result)


@pytest.mark.parametrize(
    ("magi", "tier", "part_b_irmaa", "part_d_irmaa"),
    [
        ("218000.00", 0, "0.00", "0.00"),
        ("218000.01", 1, "81.20", "14.50"),
        ("274000.01", 2, "202.90", "37.50"),
        ("342000.01", 3, "324.60", "60.40"),
        ("410000.01", 4, "446.30", "83.30"),
        ("750000.00", 5, "487.00", "91.00"),
    ],
)
def test_projection_covers_each_irmaa_tier_boundary(
    magi: str,
    tier: int,
    part_b_irmaa: str,
    part_d_irmaa: str,
    medicare_rules_2028: MedicareIrmaaRules,
) -> None:
    request = _request(historical_magi=magi, medicare_people=[_medicare_person("spouse_a")])

    result = run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    irmaa = result.annual_household[0].irmaa_result

    assert irmaa is not None
    person = irmaa.people[0]
    assert irmaa.tier_index == tier
    assert person.part_b_irmaa_monthly == Decimal(part_b_irmaa)
    assert person.part_d_irmaa_monthly == Decimal(part_d_irmaa)
    _assert_reconciles(result)


def test_included_in_spending_mode_generates_only_irmaa_surcharges(
    medicare_rules_2028: MedicareIrmaaRules,
) -> None:
    request = _request(base_mode="included_in_spending")

    result = run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    entries = _medicare_entries(result)

    assert result.annual_household[0].medicare_costs == Decimal("5769.60")
    assert {entry.transaction_id for entry in entries} == {
        "medicare:spouse_a:part-b-irmaa:2028",
        "medicare:spouse_a:part-d-irmaa:2028",
        "medicare:spouse_b:part-b-irmaa:2028",
        "medicare:spouse_b:part-d-irmaa:2028",
    }
    _assert_reconciles(result)


def test_modeled_separately_mode_includes_base_and_plan_premium_without_double_counting(
    medicare_rules_2028: MedicareIrmaaRules,
) -> None:
    request = _request()

    result = run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    entries = _medicare_entries(result)

    assert result.annual_household[0].medicare_costs == Decimal("11839.20")
    assert sum(entry.medicare_payment for entry in entries) == Decimal("11839.20")
    assert len(entries) == 8
    _assert_reconciles(result)


def test_only_configured_payment_account_is_debited(
    medicare_rules_2028: MedicareIrmaaRules,
) -> None:
    request = _request(cash_balance="20000")

    result = run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
    balances = {item.account_id: item.ending_balance for item in result.annual_accounts}

    assert balances["premium-cash"] == Decimal("8160.80")
    assert balances["other-cash"] == Decimal("0")
    assert all(entry.source_account_id == "premium-cash" for entry in _medicare_entries(result))
    _assert_reconciles(result)


def test_missing_payment_account_fails(medicare_rules_2028: MedicareIrmaaRules) -> None:
    request = _request(payment_account_id="missing")

    with pytest.raises(ValueError, match="Unknown source account: missing"):
        run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})


def test_non_cash_payment_account_fails(medicare_rules_2028: MedicareIrmaaRules) -> None:
    request = _request(cash_account_type="taxable")

    with pytest.raises(ValueError, match="Medicare payment source must be cash"):
        run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})


def test_insufficient_cash_fails(medicare_rules_2028: MedicareIrmaaRules) -> None:
    request = _request(cash_balance="100")

    with pytest.raises(ValueError, match="would make account premium-cash negative"):
        run_projection(request, medicare_irmaa_rules_by_year={2028: medicare_rules_2028})
