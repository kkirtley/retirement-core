from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.engine.self_employment_tax import (
    calculate_additional_medicare_tax,
    calculate_regular_self_employment_tax,
)
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.self_employment_tax import FederalSelfEmploymentTaxRules


@pytest.fixture(scope="module")
def rules() -> FederalSelfEmploymentTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset(
        "federal_self_employment_tax", "US-FED", 2026
    )
    return FederalSelfEmploymentTaxRules.from_dataset(dataset)


def test_regular_tax_coordinates_wage_base_and_retains_provenance(
    rules: FederalSelfEmploymentTaxRules,
) -> None:
    result = calculate_regular_self_employment_tax(
        2026, "owner", Decimal("100000"), Decimal("50000"), rules
    )
    assert result.net_earnings == Decimal("92350.00")
    assert result.remaining_social_security_wage_base == Decimal("134500.00")
    assert result.social_security_tax_base == Decimal("92350.00")
    assert result.medicare_tax == Decimal("2678.15")
    assert result.deductible_employer_equivalent_tax == Decimal("7064.78")
    assert result.dataset_id == "US-FED-SE-TAX-2026-v1"


@pytest.mark.parametrize("wages,expected", [("184500", "0"), ("200000", "0")])
def test_wage_base_cannot_be_negative(
    rules: FederalSelfEmploymentTaxRules, wages: str, expected: str
) -> None:
    result = calculate_regular_self_employment_tax(
        2026, "owner", Decimal("1000"), Decimal(wages), rules
    )
    assert result.remaining_social_security_wage_base == Decimal(expected)
    assert result.social_security_tax_base == Decimal(expected)
    assert result.medicare_tax > 0


def test_threshold_and_loss_fail_closed(rules: FederalSelfEmploymentTaxRules) -> None:
    result = calculate_regular_self_employment_tax(
        2026, "owner", Decimal("100"), Decimal("0"), rules
    )
    assert result.regular_self_employment_tax == 0
    with pytest.raises(ValueError, match="losses"):
        calculate_regular_self_employment_tax(2026, "owner", Decimal("-1"), Decimal("0"), rules)


def test_additional_medicare_statutory_ordering_and_withholding(
    rules: FederalSelfEmploymentTaxRules,
) -> None:
    result = calculate_additional_medicare_tax(
        2026,
        FilingStatus.MARRIED_FILING_JOINTLY,
        {"spouse-a-wages": Decimal("240000"), "spouse-b-wages": Decimal("10000")},
        {"business": Decimal("10000")},
        Decimal("90"),
        rules,
    )
    assert result.wage_excess_subject_to_tax == 0
    assert result.self_employment_excess_subject_to_tax == Decimal("10000")
    assert result.total_additional_medicare_tax == Decimal("90.00")
    assert result.withholding == Decimal("90")


def test_additional_medicare_wages_only_and_missing_year(
    rules: FederalSelfEmploymentTaxRules,
) -> None:
    result = calculate_additional_medicare_tax(
        2026,
        FilingStatus.MARRIED_FILING_JOINTLY,
        {"wages": Decimal("260000")},
        {},
        Decimal("0"),
        rules,
    )
    assert result.wage_excess_subject_to_tax == Decimal("10000")
    assert result.total_additional_medicare_tax == Decimal("90.00")
    with pytest.raises(ValueError, match="does not match"):
        calculate_regular_self_employment_tax(2027, "owner", Decimal("1"), Decimal("0"), rules)


def test_missing_future_dataset_fails_closed() -> None:
    with pytest.raises(FileNotFoundError):
        FederalSelfEmploymentTaxRules.load_for_tax_year(
            JsonRuleDatasetProvider(Path("data/rules")), 2027
        )
