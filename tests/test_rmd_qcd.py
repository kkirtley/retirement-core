from datetime import date
from decimal import Decimal
from pathlib import Path

from retirement_core.domain.enums import AccountType, QcdAllocationMethod, QcdTargetMode
from retirement_core.domain.models import QcdPolicyInput
from retirement_core.engine.rmd_qcd import (
    RmdAccountInput,
    RmdOwnerInput,
    calculate_qcd,
    calculate_rmd,
)
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.rmd_qcd import RmdQcdRules

YEAR = 2026


def rules() -> RmdQcdRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("rmd_qcd", "US-FED", YEAR)
    return RmdQcdRules.from_dataset(dataset)


def owner(owner_id: str, birth_year: int = 1950) -> RmdOwnerInput:
    return RmdOwnerInput(owner_id=owner_id, date_of_birth=date(birth_year, 1, 1))


def ira(account_id: str, owner_id: str, balance: str) -> RmdAccountInput:
    return RmdAccountInput(
        account_id=account_id,
        owner_id=owner_id,
        account_type=AccountType.TRADITIONAL_IRA,
        prior_year_end_balance=Decimal(balance),
    )


def test_rmd_uses_versioned_start_age_divisor_and_account_eligibility() -> None:
    regulatory_rules = rules()
    person = owner("a")
    accounts = (
        ira("ira", "a", "274000.00"),
        RmdAccountInput(
            account_id="401k",
            owner_id="a",
            account_type=AccountType.TRADITIONAL_401K,
            prior_year_end_balance=Decimal("100000.00"),
        ),
    )

    result = calculate_rmd(YEAR, (person,), accounts, regulatory_rules)
    applicable_divisor = regulatory_rules.uniform_lifetime_table[result.owners[0].attained_age]

    assert result.household_rmd == (Decimal("274000.00") / applicable_divisor).quantize(
        Decimal("0.01")
    )
    account_results = {item.account_id: item for item in result.owners[0].accounts}
    assert account_results["401k"].eligible is False
    assert account_results["401k"].required_minimum_distribution == Decimal("0.00")


def test_rmd_is_zero_before_dataset_start_age() -> None:
    result = calculate_rmd(YEAR, (owner("young", 1990),), (ira("ira", "young", "100000"),), rules())

    assert result.household_rmd == Decimal("0.00")


def test_fixed_floor_policy_is_limited_by_legal_owner_and_account_capacity() -> None:
    regulatory_rules = rules()
    people = (owner("eligible"), owner("ineligible", 1990))
    accounts = (ira("eligible-ira", "eligible", "9000"), ira("young-ira", "ineligible", "50000"))
    rmd = calculate_rmd(YEAR, people, accounts, regulatory_rules)
    policy = QcdPolicyInput(
        enabled=True,
        annual_qcd_floor=Decimal("12000"),
        target_mode=QcdTargetMode.FIXED_FLOOR,
        allocation_method=QcdAllocationMethod.OWNER_PRIORITY,
        owner_priority=["ineligible", "eligible"],
    )

    result = calculate_qcd(policy, people, accounts, rmd, regulatory_rules)

    assert result.configured_household_target == Decimal("12000.00")
    assert result.actual_qcd == Decimal("9000.00")
    assert result.unmet_target == Decimal("3000.00")
    assert next(item for item in result.owners if item.owner_id == "ineligible").actual_qcd == 0


def test_household_rmd_target_is_allocated_by_each_owners_rmd() -> None:
    regulatory_rules = rules()
    people = (owner("a"), owner("b"))
    accounts = (ira("a-ira", "a", "274000"), ira("b-ira", "b", "137000"))
    rmd = calculate_rmd(YEAR, people, accounts, regulatory_rules)
    policy = QcdPolicyInput(
        enabled=True,
        annual_qcd_floor=Decimal("5000"),
        target_mode=QcdTargetMode.MAX_OF_FLOOR_AND_HOUSEHOLD_RMD,
        allocation_method=QcdAllocationMethod.PROPORTIONAL_TO_OWNER_RMD,
    )

    result = calculate_qcd(policy, people, accounts, rmd, regulatory_rules)

    assert result.configured_household_target == rmd.household_rmd
    assert result.actual_qcd == rmd.household_rmd
    assert result.remaining_taxable_rmd == Decimal("0.00")
    assert (
        result.owners[0].actual_qcd
        == result.owners[0].remaining_taxable_rmd + rmd.owners[0].required_minimum_distribution
    )


def test_qcd_obeys_dataset_statutory_limit_and_owner_exclusion_reduction() -> None:
    regulatory_rules = rules()
    people = (owner("a"),)
    accounts = (ira("ira", "a", "999999"),)
    rmd = calculate_rmd(YEAR, people, accounts, regulatory_rules)
    reduction = Decimal("1000")
    policy = QcdPolicyInput(
        enabled=True, annual_qcd_floor=Decimal("999999"), target_mode=QcdTargetMode.FIXED_FLOOR
    )

    result = calculate_qcd(policy, people, accounts, rmd, regulatory_rules, {"a": reduction})

    assert result.actual_qcd == regulatory_rules.statutory_qcd_maximum_per_owner - reduction


def test_paused_policy_produces_no_qcd() -> None:
    regulatory_rules = rules()
    people = (owner("a"),)
    accounts = (ira("ira", "a", "100000"),)
    rmd = calculate_rmd(YEAR, people, accounts, regulatory_rules)
    policy = QcdPolicyInput(
        enabled=True,
        annual_qcd_floor=Decimal("12000"),
        target_mode=QcdTargetMode.FIXED_FLOOR,
        paused_years={YEAR},
    )

    result = calculate_qcd(policy, people, accounts, rmd, regulatory_rules)

    assert result.configured_household_target == Decimal("0.00")
    assert result.actual_qcd == Decimal("0.00")


def test_proportional_policy_can_allocate_before_rmd_age_without_bypassing_qcd_age() -> None:
    regulatory_rules = rules()
    people = (owner("a", 1954), owner("young", 1990))
    accounts = (ira("a-ira", "a", "20000"), ira("young-ira", "young", "20000"))
    rmd = calculate_rmd(YEAR, people, accounts, regulatory_rules)
    policy = QcdPolicyInput(
        enabled=True,
        annual_qcd_floor=Decimal("6000"),
        target_mode=QcdTargetMode.FIXED_FLOOR,
    )

    result = calculate_qcd(policy, people, accounts, rmd, regulatory_rules)

    assert rmd.household_rmd == Decimal("0.00")
    assert result.actual_qcd == Decimal("6000.00")
    assert next(item for item in result.owners if item.owner_id == "young").actual_qcd == 0
