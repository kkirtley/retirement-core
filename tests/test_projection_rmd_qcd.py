import json
from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.application.services import ProjectionService
from retirement_core.domain.enums import FilingStatus, TransactionType
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
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


def _request(
    *,
    year: int = YEAR,
    people: list[dict[str, str]] | None = None,
    accounts: list[dict[str, str]] | None = None,
    qcd_policy: dict[str, object] | None = None,
    destinations: dict[str, str] | None = None,
    source_policy: dict[str, object] | None = None,
) -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "RMD/QCD Integration",
                "filing_status": "married_filing_jointly",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-12-31",
                "people": people or [_person("owner")],
                "accounts": accounts
                or [_account("ira", "owner", "traditional_ira", "237000"), _cash()],
                "giving_policy": {"qcd_policy": qcd_policy or {"enabled": False}},
                "taxable_rmd_destination_account_by_owner": (
                    {"owner": "cash"} if destinations is None else destinations
                ),
                "taxable_rmd_source_policy": source_policy
                or {"allocation_method": "proportional_to_account_rmd"},
                "federal_tax_payment_account_id": "cash",
            }
        }
    )


def _person(owner_id: str, birth_date: str = "1950-01-01") -> dict[str, str]:
    return {"id": owner_id, "name": owner_id, "date_of_birth": birth_date}


def _account(
    account_id: str,
    owner_id: str,
    account_type: str,
    balance: str,
    annual_return: str = "0",
) -> dict[str, str]:
    return {
        "id": account_id,
        "owner_id": owner_id,
        "account_type": account_type,
        "starting_balance": balance,
        "annual_return": annual_return,
    }


def _cash(account_id: str = "cash", owner_id: str = "owner") -> dict[str, str]:
    return _account(account_id, owner_id, "cash", "0")


def _run(
    request: ProjectionRequest,
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
) -> ProjectionResult:
    return run_projection(request, federal_rules, {request.plan.start_date.year: rmd_rules})


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
            federal_tax=household.taxes,
        )


def _balance(result: ProjectionResult, account_id: str) -> Decimal:
    return next(
        item.ending_balance for item in result.annual_accounts if item.account_id == account_id
    )


def test_no_rmd_before_required_age(federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules) -> None:
    request = _request(people=[_person("owner", "1960-01-01")])

    result = _run(request, federal_rules, rmd_rules)
    annual = result.annual_household[0].rmd_qcd_result

    assert annual is not None
    assert annual.gross_rmd == 0
    assert annual.qcd == 0
    assert annual.taxable_rmd == 0
    assert not any(
        item.transaction_type is TransactionType.RMD_DISTRIBUTION for item in result.transactions
    )
    _assert_reconciles(result)


def test_qcd_before_rmd_age_never_enters_cash(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        people=[_person("owner", "1954-01-01")],
        accounts=[_account("ira", "owner", "traditional_ira", "20000"), _cash()],
        qcd_policy={
            "enabled": True,
            "annual_qcd_floor": "6000",
            "target_mode": "fixed_floor",
        },
        destinations={},
    )

    result = _run(request, federal_rules, rmd_rules)
    household = result.annual_household[0]
    annual = household.rmd_qcd_result

    assert annual is not None
    assert annual.gross_rmd == 0
    assert annual.qcd == Decimal("6000.00")
    assert household.cash_withdrawals == 0
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == 0
    assert _balance(result, "cash") == 0
    assert _balance(result, "ira") == Decimal("14000.00")
    qcd_entry = next(
        item
        for item in result.transactions
        if item.transaction_type is TransactionType.CHARITABLE_GIVING
    )
    assert qcd_entry.charitable_method == "qcd"
    assert qcd_entry.cash_withdrawal == 0
    assert qcd_entry.taxable_ordinary_income == 0
    _assert_reconciles(result)


def test_proportional_multi_account_rmd_enters_cash_and_federal_income(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[
            _account("large", "owner", "traditional_ira", "189600", "0.10"),
            _account("small", "owner", "traditional_ira", "94800"),
            _cash(),
        ]
    )

    result = _run(request, federal_rules, rmd_rules)
    household = result.annual_household[0]
    annual = household.rmd_qcd_result

    assert annual is not None
    rows = {item.source_account_id: item for item in annual.owners[0].accounts}
    assert rows["large"].gross_rmd == Decimal("8000.00")
    assert rows["large"].taxable_rmd == Decimal("8000.00")
    assert rows["small"].gross_rmd == Decimal("4000.00")
    assert rows["small"].taxable_rmd == Decimal("4000.00")
    assert household.cash_withdrawals == Decimal("12000.00")
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("12000.00")
    rmd_entries = [
        item
        for item in result.transactions
        if item.transaction_type is TransactionType.RMD_DISTRIBUTION
    ]
    assert sum((item.taxable_ordinary_income for item in rmd_entries), Decimal("0")) == Decimal(
        "12000.00"
    )
    assert _balance(result, "large") == Decimal("200560.00")
    assert _balance(result, "cash") == Decimal("12000.00")
    _assert_reconciles(result)


def test_configured_account_priority_controls_taxable_rmd_source(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[
            _account("first", "owner", "traditional_ira", "189600"),
            _account("preferred", "owner", "traditional_ira", "94800"),
            _cash(),
        ],
        source_policy={
            "allocation_method": "account_priority",
            "account_priority": ["preferred", "first"],
        },
    )

    result = _run(request, federal_rules, rmd_rules)
    annual = result.annual_household[0].rmd_qcd_result

    assert annual is not None
    rows = {item.source_account_id: item for item in annual.owners[0].accounts}
    assert rows["preferred"].taxable_rmd == Decimal("12000.00")
    assert rows["first"].taxable_rmd == 0
    _assert_reconciles(result)


def test_account_priority_mode_has_no_hidden_account_id_fallback(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[
            _account("a", "owner", "traditional_ira", "237000"),
            _account("b", "owner", "traditional_ira", "118500"),
            _cash(),
        ],
        source_policy={"allocation_method": "account_priority"},
    )

    with pytest.raises(ValueError, match="account_priority is required"):
        _run(request, federal_rules, rmd_rules)


def test_explicit_account_amounts_control_each_owners_sources(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[
            _account("one", "owner", "traditional_ira", "189600"),
            _account("two", "owner", "traditional_ira", "94800"),
            _cash(),
        ],
        source_policy={
            "allocation_method": "explicit_account_amounts",
            "explicit_account_amounts": {YEAR: {"owner": {"one": "4000", "two": "8000"}}},
        },
    )

    result = _run(request, federal_rules, rmd_rules)
    annual = result.annual_household[0].rmd_qcd_result

    assert annual is not None
    rows = {item.source_account_id: item for item in annual.owners[0].accounts}
    assert rows["one"].taxable_rmd == Decimal("4000")
    assert rows["two"].taxable_rmd == Decimal("8000")
    _assert_reconciles(result)


@pytest.mark.parametrize(
    ("floor", "expected_qcd", "expected_taxable"),
    [
        ("7000", Decimal("7000.00"), Decimal("5000.00")),
        ("12000", Decimal("12000.00"), Decimal("0.00")),
    ],
)
def test_partial_and_full_qcd_are_owner_specific_and_excluded_from_tax(
    floor: str,
    expected_qcd: Decimal,
    expected_taxable: Decimal,
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
) -> None:
    request = _request(
        accounts=[_account("ira", "owner", "traditional_ira", "284400"), _cash()],
        qcd_policy={"enabled": True, "annual_qcd_floor": floor, "target_mode": "fixed_floor"},
    )

    result = _run(request, federal_rules, rmd_rules)
    household = result.annual_household[0]
    annual = household.rmd_qcd_result

    assert annual is not None
    assert annual.gross_rmd == Decimal("12000.00")
    assert annual.qcd == expected_qcd
    assert annual.taxable_rmd == expected_taxable
    assert household.cash_withdrawals == expected_taxable
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == expected_taxable
    _assert_reconciles(result)


def test_two_owner_qcd_cannot_offset_other_owner_rmd(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        people=[_person("a"), _person("b")],
        accounts=[
            _account("a-ira", "a", "traditional_ira", "237000"),
            _account("b-ira", "b", "traditional_ira", "237000"),
            _cash("cash-a", "a"),
            _cash("cash-b", "b"),
        ],
        qcd_policy={
            "enabled": True,
            "annual_qcd_floor": "10000",
            "target_mode": "fixed_floor",
            "allocation_method": "owner_priority",
            "owner_priority": ["a", "b"],
        },
        destinations={"a": "cash-a", "b": "cash-b"},
    )

    result = _run(request, federal_rules, rmd_rules)
    annual = result.annual_household[0].rmd_qcd_result

    assert annual is not None
    owners = {item.owner_id: item for item in annual.owners}
    assert owners["a"].qcd == Decimal("10000.00")
    assert owners["a"].taxable_rmd == 0
    assert owners["b"].qcd == 0
    assert owners["b"].taxable_rmd == Decimal("10000.00")
    assert _balance(result, "cash-a") == 0
    assert _balance(result, "cash-b") > 0
    _assert_reconciles(result)


def test_qcd_capacity_shortfall_is_reported(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(
        accounts=[_account("ira", "owner", "traditional_ira", "9000"), _cash()],
        qcd_policy={"enabled": True, "annual_qcd_floor": "12000", "target_mode": "fixed_floor"},
        destinations={},
    )

    result = _run(request, federal_rules, rmd_rules)
    annual = result.annual_household[0].rmd_qcd_result

    assert annual is not None
    assert annual.qcd == Decimal("9000.00")
    assert annual.qcd_capacity_shortfall == Decimal("3000.00")
    assert annual.warnings
    _assert_reconciles(result)


def test_missing_taxable_rmd_destination_fails(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request(destinations={})

    with pytest.raises(ValueError, match="explicit taxable RMD destination"):
        _run(request, federal_rules, rmd_rules)


def test_missing_taxable_rmd_source_policy_fails(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    request = _request()
    request.plan.taxable_rmd_source_policy = None

    with pytest.raises(ValueError, match="explicit taxable RMD source policy"):
        _run(request, federal_rules, rmd_rules)


def test_future_year_uses_earlier_still_effective_dataset() -> None:
    request = _request(year=2027)
    service = ProjectionService(JsonRuleDatasetProvider(Path("data/rules")))

    result = service.run(request)

    annual = result.annual_household[0].rmd_qcd_result
    assert annual is not None
    assert annual.rule_dataset_id == "US-FED-RMD-QCD-2026-v1"
    assert result.provenance["rmd_qcd_dataset_id:2027"] == annual.rule_dataset_id
    _assert_reconciles(result)


def test_projection_fails_when_no_effective_rule_dataset_exists(tmp_path: Path) -> None:
    source = json.loads(Path("data/rules/rmd_qcd/US-FED/2026.json").read_text(encoding="utf-8"))
    source["effective_to"] = "2026-12-31"
    destination = tmp_path / "rmd_qcd" / "US-FED"
    destination.mkdir(parents=True)
    (destination / "2026.json").write_text(json.dumps(source), encoding="utf-8")
    request = _request(year=2027)
    service = ProjectionService(JsonRuleDatasetProvider(tmp_path))

    with pytest.raises(ValueError, match="No applicable RMD/QCD rule dataset"):
        service.run(request)


def test_rule_provider_selects_latest_applicable_version(tmp_path: Path) -> None:
    earlier = json.loads(Path("data/rules/rmd_qcd/US-FED/2026.json").read_text(encoding="utf-8"))
    later = dict(earlier)
    later.update(
        {
            "dataset_id": "US-FED-RMD-QCD-later",
            "tax_year": 2027,
            "version": "2",
            "effective_from": "2027-01-01",
        }
    )
    destination = tmp_path / "rmd_qcd" / "US-FED"
    destination.mkdir(parents=True)
    (destination / "earlier.json").write_text(json.dumps(earlier), encoding="utf-8")
    (destination / "later.json").write_text(json.dumps(later), encoding="utf-8")

    selected = JsonRuleDatasetProvider(tmp_path).get_applicable_dataset("rmd_qcd", "US-FED", 2028)

    assert selected.dataset_id == "US-FED-RMD-QCD-later"
