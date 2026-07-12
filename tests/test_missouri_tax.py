from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.engine.missouri_tax import MissouriOwnerIncome, calculate_missouri_income_tax
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.missouri_tax import MissouriTaxRules


@pytest.fixture(scope="module")
def rules() -> MissouriTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_applicable_dataset(
        "missouri_tax", "US-MO", 2026
    )
    return MissouriTaxRules.from_dataset(dataset)


def owner(owner_id: str = "owner", **values: object) -> MissouriOwnerIncome:
    return MissouriOwnerIncome(
        owner_id=owner_id,
        date_of_birth=date(1950, 1, 1),
        **values,
    )


def calculate(
    rules: MissouriTaxRules,
    owners: tuple[MissouriOwnerIncome, ...],
    federal_tax: str = "0",
):
    if len(owners) == 1:
        owners = (*owners, owner("spouse"))
    return calculate_missouri_income_tax(owners, Decimal(federal_tax), rules)


def test_zero_income(rules: MissouriTaxRules) -> None:
    result = calculate(rules, (owner(), owner("spouse")))

    assert result.gross_income_basis == 0
    assert result.taxable_income == 0
    assert result.total_tax == 0


def test_public_pension_uses_official_2026_indexed_maximum(
    rules: MissouriTaxRules,
) -> None:
    result = calculate(rules, (owner(public_pension=Decimal("60000")),))

    assert result.public_pension_subtraction == rules.public_pension_maximum_per_owner
    assert result.social_security_subtraction == 0


def test_public_pension_plus_social_security_shares_owner_cap(
    rules: MissouriTaxRules,
) -> None:
    result = calculate(
        rules,
        (
            owner(
                public_pension=Decimal("50000"),
                gross_social_security=Decimal("24000"),
                taxable_social_security_retirement=Decimal("20000"),
            ),
        ),
    )

    assert result.social_security_subtraction == Decimal("20000")
    assert result.public_pension_subtraction == Decimal("28967")
    assert result.social_security_subtraction + result.public_pension_subtraction == Decimal(
        "48967"
    )


def test_social_security_does_not_reduce_other_owners_public_pension_cap(
    rules: MissouriTaxRules,
) -> None:
    result = calculate(
        rules,
        (
            owner("public-owner", public_pension=Decimal("50000")),
            owner(
                "ss-owner",
                gross_social_security=Decimal("24000"),
                taxable_social_security_retirement=Decimal("20000"),
            ),
        ),
    )

    assert result.social_security_subtraction == Decimal("20000")
    assert result.public_pension_subtraction == rules.public_pension_maximum_per_owner


@pytest.mark.parametrize("field", ["private_pension", "taxable_ira_withdrawal", "taxable_rmd"])
def test_private_pension_and_rmd_use_private_retirement_subtraction(
    field: str, rules: MissouriTaxRules
) -> None:
    result = calculate(rules, (owner(**{field: Decimal("10000")}),))

    assert result.private_retirement_subtraction == Decimal("6000")
    assert result.gross_income_basis == Decimal("10000")


def test_private_retirement_subtraction_phases_out(rules: MissouriTaxRules) -> None:
    result = calculate(rules, (owner(private_pension=Decimal("40000")),))

    assert result.private_retirement_subtraction == 0


def test_federal_tax_deduction_uses_enacted_percentage(rules: MissouriTaxRules) -> None:
    result = calculate(
        rules,
        (owner(private_pension=Decimal("50000")),),
        federal_tax="10000",
    )

    assert result.federal_tax_deduction == Decimal("2500")


@pytest.mark.parametrize("taxable_amount", ["10000", "4000"])
def test_roth_conversion_includes_only_taxable_amount_without_retirement_subtraction(
    taxable_amount: str, rules: MissouriTaxRules
) -> None:
    result = calculate(
        rules,
        (owner(taxable_roth_conversion=Decimal(taxable_amount)),),
    )

    assert result.gross_income_basis == Decimal(taxable_amount)
    assert result.private_retirement_subtraction == 0


def test_roth_conversion_increases_private_pension_phaseout(rules: MissouriTaxRules) -> None:
    result = calculate(
        rules,
        (
            owner(
                private_pension=Decimal("10000"),
                taxable_roth_conversion=Decimal("30000"),
            ),
        ),
    )

    assert result.gross_income_basis == Decimal("40000")
    assert result.private_retirement_subtraction == 0


def test_separate_spouse_income_allocation_and_whole_dollar_tax(
    rules: MissouriTaxRules,
) -> None:
    result = calculate(
        rules,
        (
            owner("a", public_pension=Decimal("100000")),
            owner("b", private_pension=Decimal("50000")),
        ),
    )

    assert result.owners[0].income_percentage == Decimal("100000") / Decimal("150000")
    assert result.total_tax == sum((item.tax for item in result.owners), Decimal("0"))
    assert all(item.tax == item.tax.quantize(Decimal("1")) for item in result.owners)


def test_component_statuses_identify_provisional_rate_and_return_mechanics(
    rules: MissouriTaxRules,
) -> None:
    assert (
        rules.component_statuses["rate_formula"].status
        == "official_2026_withholding_formula_used_as_projected_return_rate"
    )
    assert (
        rules.component_statuses["return_rounding_and_combined_mechanics"].status
        == "provisionally_carried_forward_from_2025_instructions"
    )
    assert rules.roth_conversion.included_in_missouri_agi is True
    assert rules.roth_conversion.private_retirement_subtraction_eligible is False
    assert rules.roth_conversion.classification == "excluded_by_RSMo_143.124_rollover_rule"
