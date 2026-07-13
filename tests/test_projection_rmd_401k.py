from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import (
    FilingStatus,
    RmdFirstPaymentTiming,
    RmdObligationGroupType,
    TransactionType,
)
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.ledger import reconcile_account, reconcile_household_cash
from retirement_core.engine.projection import run_projection
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.missouri_tax import MissouriTaxRules
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


@pytest.fixture(scope="module")
def missouri_rules() -> MissouriTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("missouri_tax", "US-MO", YEAR)
    return MissouriTaxRules.from_dataset(dataset)


def _person(owner_id: str = "owner", birth_date: str = "1950-01-01") -> dict[str, str]:
    return {"id": owner_id, "name": owner_id, "date_of_birth": birth_date}


def _cash(account_id: str = "cash", owner_id: str = "owner") -> dict[str, str]:
    return {
        "id": account_id,
        "owner_id": owner_id,
        "account_type": "cash",
        "starting_balance": "0",
    }


def _ira(
    account_id: str = "ira", owner_id: str = "owner", balance: str = "237000"
) -> dict[str, str]:
    return {
        "id": account_id,
        "owner_id": owner_id,
        "account_type": "traditional_ira",
        "starting_balance": balance,
    }


def _workplace_plan(
    destination: str | None = "cash",
    *,
    first_payment_timing: str = "distribution_year",
) -> dict[str, object]:
    return {
        "employer_status": "former_employer",
        "rmd_timing_rule": "standard_statutory_age",
        "is_five_percent_owner": False,
        "taxable_rmd_destination_account_id": destination,
        "first_rmd_payment_timing": first_payment_timing,
    }


def _plan_401k(
    account_id: str = "plan-401k",
    owner_id: str = "owner",
    balance: str = "237000",
    *,
    destination: str | None = "cash",
    first_payment_timing: str = "distribution_year",
    annual_return: str | None = None,
) -> dict[str, object]:
    account: dict[str, object] = {
        "id": account_id,
        "owner_id": owner_id,
        "account_type": "traditional_401k",
        "starting_balance": balance,
        "workplace_plan_rmd": _workplace_plan(
            destination, first_payment_timing=first_payment_timing
        ),
    }
    if annual_return is not None:
        account["annual_return"] = annual_return
    return account


def _request(
    accounts: list[dict[str, object]],
    *,
    people: list[dict[str, str]] | None = None,
    qcd_policy: dict[str, object] | None = None,
    ira_destination: dict[str, str] | None = None,
    missouri: bool = False,
    income: list[dict[str, object]] | None = None,
) -> ProjectionRequest:
    plan: dict[str, object] = {
        "household_name": "Traditional 401(k) projection",
        "filing_status": "married_filing_jointly",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "people": people or [_person()],
        "accounts": accounts,
        "income": income or [],
        "giving_policy": {"qcd_policy": qcd_policy or {"enabled": False}},
        "taxable_rmd_destination_account_by_owner": ira_destination or {"owner": "cash"},
        "taxable_rmd_source_policy": {"allocation_method": "proportional_to_account_rmd"},
        "federal_tax_payment_account_id": "cash",
    }
    if missouri:
        plan.update(
            {
                "state_residency": {"state_code": "MO", "status": "full_year_resident"},
                "missouri_tax_payment_account_id": "cash",
            }
        )
    return ProjectionRequest.model_validate({"plan": plan})


def _run(
    request: ProjectionRequest,
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules | None = None,
) -> ProjectionResult:
    return run_projection(
        request,
        federal_rules,
        {YEAR: rmd_rules},
        {YEAR: missouri_rules} if missouri_rules is not None else None,
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


def _rmd_entries(result: ProjectionResult) -> list[object]:
    return [
        entry
        for entry in result.transactions
        if entry.transaction_type is TransactionType.RMD_DISTRIBUTION
    ]


def test_401k_distribution_is_account_specific_and_taxable(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    result = _run(_request([_plan_401k(), _cash()]), federal_rules, rmd_rules)
    household = result.annual_household[0]
    rmd = household.rmd_qcd_result

    assert rmd is not None
    assert rmd.gross_ira_rmd == 0
    assert rmd.gross_traditional_401k_rmd == Decimal("10000.00")
    assert rmd.taxable_traditional_401k_rmd == Decimal("10000.00")
    assert rmd.gross_rmd == Decimal("10000.00")
    assert rmd.taxable_rmd == Decimal("10000.00")
    assert rmd.aggregate_gross_rmd == Decimal("10000.00")
    entry = _rmd_entries(result)[0]
    assert entry.source_account_id == "plan-401k"
    assert entry.destination_account_id == "cash"
    assert entry.rmd_obligation_group_id == "traditional-401k:plan-401k"
    assert entry.rmd_obligation_group_type is RmdObligationGroupType.TRADITIONAL_401K_PLAN
    assert household.cash_withdrawals == Decimal("10000.00")
    assert household.federal_agi_result is not None
    assert household.federal_agi_result.taxable_rmd_distributions == Decimal("10000.00")
    assert household.federal_agi_result.irmaa_magi == Decimal("10000.00")
    agi_component = next(
        component
        for component in household.federal_agi_result.components
        if component.source_account_id == "plan-401k"
    )
    assert agi_component.owner_id == "owner"
    assert agi_component.provenance == "generated taxable Traditional 401(k) RMD distribution"
    assert household.federal_tax_result is not None
    assert household.federal_tax_result.gross_income == Decimal("10000.00")
    _assert_reconciles(result)


def test_two_401k_plans_create_separate_distributions(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    result = _run(
        _request([_plan_401k("plan-a"), _plan_401k("plan-b", balance="118500"), _cash()]),
        federal_rules,
        rmd_rules,
    )

    entries = _rmd_entries(result)
    assert {entry.source_account_id for entry in entries} == {"plan-a", "plan-b"}
    assert {entry.rmd_obligation_group_id for entry in entries} == {
        "traditional-401k:plan-a",
        "traditional-401k:plan-b",
    }
    rmd = result.annual_household[0].rmd_qcd_result
    assert rmd is not None
    assert rmd.gross_traditional_401k_rmd == Decimal("15000.00")
    assert (
        len(
            [
                group
                for group in rmd.obligation_groups
                if group.group_type is RmdObligationGroupType.TRADITIONAL_401K_PLAN
            ]
        )
        == 2
    )
    _assert_reconciles(result)


def test_ira_qcd_does_not_reduce_401k_obligation(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    result = _run(
        _request(
            [_ira(), _plan_401k(), _cash()],
            qcd_policy={
                "enabled": True,
                "annual_qcd_floor": "10000",
                "target_mode": "fixed_floor",
            },
        ),
        federal_rules,
        rmd_rules,
    )
    rmd = result.annual_household[0].rmd_qcd_result

    assert rmd is not None
    assert rmd.gross_ira_rmd == Decimal("10000.00")
    assert rmd.qcd == Decimal("10000.00")
    assert rmd.taxable_ira_rmd == 0
    assert rmd.gross_traditional_401k_rmd == Decimal("10000.00")
    assert rmd.taxable_traditional_401k_rmd == Decimal("10000.00")
    assert rmd.gross_rmd == Decimal("20000.00")
    assert rmd.taxable_rmd == Decimal("10000.00")
    assert len(_rmd_entries(result)) == 1
    assert _rmd_entries(result)[0].source_account_id == "plan-401k"
    _assert_reconciles(result)


def test_ira_and_401k_obligations_cannot_satisfy_each_other(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    result = _run(_request([_ira(), _plan_401k(), _cash()]), federal_rules, rmd_rules)
    entries = {entry.source_account_id: entry for entry in _rmd_entries(result)}
    rmd = result.annual_household[0].rmd_qcd_result

    assert set(entries) == {"ira", "plan-401k"}
    assert entries["ira"].rmd_obligation_group_type is RmdObligationGroupType.IRA_OWNER_AGGREGATE
    assert (
        entries["plan-401k"].rmd_obligation_group_type
        is RmdObligationGroupType.TRADITIONAL_401K_PLAN
    )
    assert rmd is not None
    assert rmd.taxable_ira_rmd == Decimal("10000.00")
    assert rmd.taxable_traditional_401k_rmd == Decimal("10000.00")
    _assert_reconciles(result)


def test_401k_destination_and_live_balance_failures(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    with pytest.raises(ValueError, match="plan-401k requires an explicit taxable RMD destination"):
        _run(_request([_plan_401k(destination=None), _cash()]), federal_rules, rmd_rules)

    with pytest.raises(ValueError, match="Insufficient live Traditional 401\\(k\\) balance"):
        _run(_request([_plan_401k(annual_return="-1"), _cash()]), federal_rules, rmd_rules)


def test_first_rmd_distribution_year_and_deferred_election(
    federal_rules: FederalTaxRules, rmd_rules: RmdQcdRules
) -> None:
    result = _run(
        _request([_plan_401k(), _cash()], people=[_person(birth_date="1953-01-01")]),
        federal_rules,
        rmd_rules,
    )
    group = next(
        item
        for item in result.annual_household[0].rmd_qcd_result.obligation_groups  # type: ignore[union-attr]
        if item.group_type is RmdObligationGroupType.TRADITIONAL_401K_PLAN
    )
    assert group.payment_deadline.isoformat() == "2027-04-01"
    assert _rmd_entries(result)[0].amount == group.required_amount

    with pytest.raises(ValueError, match=r"DEFER_TO_FOLLOWING_YEAR.*not implemented"):
        _run(
            _request(
                [
                    _plan_401k(
                        first_payment_timing=RmdFirstPaymentTiming.DEFER_TO_FOLLOWING_YEAR.value
                    ),
                    _cash(),
                ]
            ),
            federal_rules,
            rmd_rules,
        )


def test_missouri_fails_closed_for_401k_rmd(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "Missouri tax year 2026 cannot classify Traditional 401\\(k\\) RMD "
            "for owner owner, account plan-401k: unsupported Missouri classification"
        ),
    ):
        _run(
            _request([_plan_401k(), _cash()], missouri=True),
            federal_rules,
            rmd_rules,
            missouri_rules,
        )


def test_missouri_allows_zero_401k_rmd(
    federal_rules: FederalTaxRules,
    rmd_rules: RmdQcdRules,
    missouri_rules: MissouriTaxRules,
) -> None:
    result = _run(
        _request(
            [_plan_401k(balance="0"), _cash()],
            missouri=True,
            people=[_person(), _person("spouse", "1960-01-01")],
            income=[
                {
                    "id": "private-pension",
                    "income_type": "pension",
                    "pension_type": "private",
                    "owner_id": "owner",
                    "annual_amount": "10000",
                    "start_date": "2026-01-01",
                    "destination_account_id": "cash",
                }
            ],
        ),
        federal_rules,
        rmd_rules,
        missouri_rules,
    )

    assert result.annual_household[0].missouri_tax_result is not None
    _assert_reconciles(result)
