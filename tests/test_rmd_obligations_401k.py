from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from retirement_core.domain.enums import (
    AccountType,
    RmdObligationGroupType,
    WorkplacePlanStatus,
    WorkplaceRmdTimingRule,
)
from retirement_core.domain.models import WorkplacePlanRmdInput
from retirement_core.engine.rmd_qcd import (
    RmdAccountInput,
    RmdOwnerInput,
    calculate_rmd_obligations,
)
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.rmd_qcd import RmdQcdRules

YEAR = 2026


def _rules() -> RmdQcdRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("rmd_qcd", "US-FED", YEAR)
    return RmdQcdRules.from_dataset(dataset)


def _owner(owner_id: str = "owner", birth_year: int = 1950) -> RmdOwnerInput:
    return RmdOwnerInput(owner_id=owner_id, date_of_birth=date(birth_year, 1, 1))


def _ira(account_id: str, balance: str, owner_id: str = "owner") -> RmdAccountInput:
    return RmdAccountInput(
        account_id=account_id,
        owner_id=owner_id,
        account_type=AccountType.TRADITIONAL_IRA,
        prior_year_end_balance=Decimal(balance),
    )


def _workplace(
    *,
    status: WorkplacePlanStatus = WorkplacePlanStatus.FORMER_EMPLOYER,
    timing: WorkplaceRmdTimingRule = WorkplaceRmdTimingRule.STANDARD_STATUTORY_AGE,
    five_percent_owner: bool | None = False,
    employment_end_date: date | None = None,
) -> WorkplacePlanRmdInput:
    return WorkplacePlanRmdInput(
        employer_status=status,
        rmd_timing_rule=timing,
        is_five_percent_owner=five_percent_owner,
        employment_end_date=employment_end_date,
        taxable_rmd_destination_account_id="cash",
    )


def _401k(
    account_id: str,
    balance: str,
    workplace: WorkplacePlanRmdInput | None,
    owner_id: str = "owner",
) -> RmdAccountInput:
    return RmdAccountInput(
        account_id=account_id,
        owner_id=owner_id,
        account_type=AccountType.TRADITIONAL_401K,
        prior_year_end_balance=Decimal(balance),
        workplace_plan_rmd=workplace,
    )


def test_ira_only_obligation_preserves_owner_aggregation() -> None:
    result = calculate_rmd_obligations(
        YEAR, (_owner(),), (_ira("ira-one", "237000"), _ira("ira-two", "118500")), _rules()
    )

    assert len(result.obligations) == 1
    obligation = result.obligations[0]
    assert obligation.group_type is RmdObligationGroupType.IRA_OWNER_AGGREGATE
    assert obligation.account_ids == ("ira-one", "ira-two")
    assert obligation.required_amount == Decimal("15000.00")


def test_former_employer_401k_has_its_own_obligation() -> None:
    result = calculate_rmd_obligations(
        YEAR,
        (_owner(),),
        (_401k("plan-a", "237000", _workplace()),),
        _rules(),
    )
    obligation = result.obligations[0]

    assert obligation.group_type is RmdObligationGroupType.TRADITIONAL_401K_PLAN
    assert obligation.account_ids == ("plan-a",)
    assert obligation.prior_year_end_balances == {"plan-a": Decimal("237000")}
    assert obligation.balance_date == date(2025, 12, 31)
    assert obligation.divisor == Decimal("23.7")
    assert obligation.required_amount == Decimal("10000.00")
    assert obligation.distribution_year == YEAR
    assert obligation.payment_deadline == date(YEAR, 12, 31)


def test_two_401k_plans_never_aggregate() -> None:
    result = calculate_rmd_obligations(
        YEAR,
        (_owner(),),
        (
            _401k("plan-a", "237000", _workplace()),
            _401k("plan-b", "118500", _workplace()),
        ),
        _rules(),
    )

    assert [item.group_id for item in result.obligations] == [
        "traditional-401k:plan-a",
        "traditional-401k:plan-b",
    ]
    assert [item.required_amount for item in result.obligations] == [
        Decimal("10000.00"),
        Decimal("5000.00"),
    ]


def test_ira_and_401k_produce_separate_obligation_groups() -> None:
    result = calculate_rmd_obligations(
        YEAR,
        (_owner(),),
        (_ira("ira", "237000"), _401k("plan", "237000", _workplace())),
        _rules(),
    )

    assert [item.group_type for item in result.obligations] == [
        RmdObligationGroupType.IRA_OWNER_AGGREGATE,
        RmdObligationGroupType.TRADITIONAL_401K_PLAN,
    ]


def test_current_employer_later_of_retirement_defers_while_still_working() -> None:
    workplace = _workplace(
        status=WorkplacePlanStatus.CURRENT_EMPLOYER,
        timing=WorkplaceRmdTimingRule.LATER_OF_RETIREMENT,
    )
    result = calculate_rmd_obligations(
        YEAR, (_owner(),), (_401k("plan", "237000", workplace),), _rules()
    )
    obligation = result.obligations[0]

    assert obligation.required_amount == Decimal("0.00")
    assert obligation.distribution_year is None
    assert obligation.payment_deadline is None


def test_current_employer_below_statutory_age_has_no_obligation() -> None:
    workplace = _workplace(
        status=WorkplacePlanStatus.CURRENT_EMPLOYER,
        timing=WorkplaceRmdTimingRule.LATER_OF_RETIREMENT,
    )
    result = calculate_rmd_obligations(
        YEAR,
        (_owner(birth_year=1960),),
        (_401k("plan", "237000", workplace),),
        _rules(),
    )
    assert result.obligations[0].required_amount == Decimal("0.00")


def test_later_of_retirement_uses_retirement_year_and_first_deadline() -> None:
    workplace = _workplace(
        status=WorkplacePlanStatus.CURRENT_EMPLOYER,
        timing=WorkplaceRmdTimingRule.LATER_OF_RETIREMENT,
        employment_end_date=date(2026, 6, 30),
    )
    result = calculate_rmd_obligations(
        YEAR, (_owner(),), (_401k("plan", "237000", workplace),), _rules()
    )
    obligation = result.obligations[0]

    assert obligation.distribution_year == 2026
    assert obligation.payment_deadline == date(2027, 4, 1)
    assert obligation.required_amount == Decimal("10000.00")


def test_five_percent_owner_uses_standard_statutory_age() -> None:
    workplace = _workplace(five_percent_owner=True)
    result = calculate_rmd_obligations(
        YEAR, (_owner(),), (_401k("plan", "237000", workplace),), _rules()
    )

    assert result.obligations[0].required_amount == Decimal("10000.00")
    assert result.obligations[0].timing_rule == "standard_statutory_age"


def test_invalid_later_of_retirement_configuration_fails_validation() -> None:
    with pytest.raises(ValidationError, match="LATER_OF_RETIREMENT requires CURRENT_EMPLOYER"):
        _workplace(timing=WorkplaceRmdTimingRule.LATER_OF_RETIREMENT)
    with pytest.raises(ValidationError, match="is_five_percent_owner=false"):
        _workplace(
            status=WorkplacePlanStatus.CURRENT_EMPLOYER,
            timing=WorkplaceRmdTimingRule.LATER_OF_RETIREMENT,
            five_percent_owner=True,
        )


def test_unknown_status_fails_only_when_rmd_timing_is_needed() -> None:
    unknown = _workplace(status=WorkplacePlanStatus.UNKNOWN)
    below_age = calculate_rmd_obligations(
        YEAR,
        (_owner(birth_year=1960),),
        (_401k("plan", "237000", unknown),),
        _rules(),
    )
    assert below_age.obligations[0].required_amount == Decimal("0.00")

    with pytest.raises(ValueError, match="unknown workplace-plan status"):
        calculate_rmd_obligations(YEAR, (_owner(),), (_401k("plan", "237000", unknown),), _rules())


def test_dataset_keeps_qcd_eligibility_limited_to_iras() -> None:
    rules = _rules()
    assert rules.account_eligibility.account_policies[AccountType.TRADITIONAL_IRA].qcd_eligible
    assert not rules.account_eligibility.account_policies[AccountType.TRADITIONAL_401K].qcd_eligible


def test_legacy_ira_only_dataset_values_remain_loadable() -> None:
    values = _rules().model_dump()
    values["account_eligibility"].pop("account_policies")
    legacy_rules = RmdQcdRules.model_validate(values)

    result = calculate_rmd_obligations(YEAR, (_owner(),), (_ira("ira", "237000"),), legacy_rules)
    assert result.obligations[0].group_type is RmdObligationGroupType.IRA_OWNER_AGGREGATE
