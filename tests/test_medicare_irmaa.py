from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.domain.medicare import (
    IrmaaMagiComposition,
    IrmaaTaxRecordInput,
    MedicarePersonInput,
)
from retirement_core.engine.medicare_irmaa import calculate_annual_irmaa, calculate_irmaa_magi
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import MedicareIrmaaRules


@pytest.fixture(scope="module")
def rules() -> MedicareIrmaaRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset(
        "medicare_irmaa", "US-FED", 2026
    )
    return MedicareIrmaaRules.from_dataset(dataset)


def record(year: int, status: FilingStatus, magi: str) -> IrmaaTaxRecordInput:
    return IrmaaTaxRecordInput(
        tax_year=year,
        filing_status=status,
        federal_adjusted_gross_income=Decimal(magi),
    )


@pytest.mark.parametrize(
    ("status", "magi", "tier", "part_b", "part_d"),
    [
        (FilingStatus.SINGLE, "109000.00", 0, "0.00", "0.00"),
        (FilingStatus.SINGLE, "109000.01", 1, "81.20", "14.50"),
        (FilingStatus.SINGLE, "500000.00", 5, "487.00", "91.00"),
        (FilingStatus.MARRIED_FILING_JOINTLY, "218000.00", 0, "0.00", "0.00"),
        (FilingStatus.MARRIED_FILING_JOINTLY, "218000.01", 1, "81.20", "14.50"),
        (FilingStatus.MARRIED_FILING_JOINTLY, "750000.00", 5, "487.00", "91.00"),
    ],
)
def test_tier_boundaries(
    rules: MedicareIrmaaRules,
    status: FilingStatus,
    magi: str,
    tier: int,
    part_b: str,
    part_d: str,
) -> None:
    result = calculate_annual_irmaa(
        rules,
        [MedicarePersonInput(owner_id="owner", part_b_enrollment_date=date(2026, 1, 1))],
        {2024: record(2024, status, magi)},
        {},
    )
    assert result.tier_index == tier
    assert result.people[0].part_b_irmaa_monthly == Decimal(part_b)
    assert result.people[0].part_d_irmaa_monthly == Decimal(part_d)


def test_magi_is_federal_agi_plus_tax_exempt_interest() -> None:
    composition = IrmaaMagiComposition(
        tax_year=2026,
        filing_status=FilingStatus.MARRIED_FILING_JOINTLY,
        taxable_pension=Decimal("30000"),
        taxable_social_security=Decimal("12000"),
        taxable_roth_conversions=Decimal("40000"),
        taxable_rmd=Decimal("10000"),
        taxable_interest=Decimal("1000"),
        other_supported_agi=Decimal("2000"),
        tax_exempt_interest=Decimal("3000"),
        qcd=Decimal("15000"),
    )
    result = calculate_irmaa_magi(composition)
    assert result.federal_adjusted_gross_income == Decimal("95000")
    assert result.tax_exempt_interest == Decimal("3000")
    assert result.irmaa_magi == Decimal("98000")
    assert composition.qcd == Decimal("15000")


def test_completed_projection_record_has_priority(rules: MedicareIrmaaRules) -> None:
    result = calculate_annual_irmaa(
        rules,
        [],
        {2024: record(2024, FilingStatus.SINGLE, "120000")},
        {2024: record(2024, FilingStatus.SINGLE, "100000")},
    )
    assert result.magi.source == "completed_projection"
    assert result.magi.irmaa_magi == Decimal("120000")


def test_historical_record_is_fallback(rules: MedicareIrmaaRules) -> None:
    result = calculate_annual_irmaa(
        rules,
        [],
        {},
        {2024: record(2024, FilingStatus.SINGLE, "100000")},
    )
    assert result.magi.source == "historical_tax_record"


def test_missing_lookback_magi_fails(rules: MedicareIrmaaRules) -> None:
    with pytest.raises(ValueError, match="requires MAGI for tax year 2024"):
        calculate_annual_irmaa(rules, [], {}, {})


def test_enrollment_month_included_without_daily_proration(rules: MedicareIrmaaRules) -> None:
    result = calculate_annual_irmaa(
        rules,
        [
            MedicarePersonInput(
                owner_id="owner",
                part_b_enrollment_date=date(2026, 7, 31),
                part_d_enrollment_date=date(2026, 10, 15),
                part_d_plan_monthly_premium=Decimal("40.00"),
            )
        ],
        {2024: record(2024, FilingStatus.SINGLE, "120000")},
        {},
    )
    person = result.people[0]
    assert person.part_b_months == 6
    assert person.part_d_months == 3
    assert person.annual_part_b_base == Decimal("1217.40")
    assert person.annual_part_b_irmaa == Decimal("487.20")
    assert person.annual_part_d_plan == Decimal("120.00")
    assert person.annual_part_d_irmaa == Decimal("43.50")


def test_part_d_enrollment_requires_explicit_plan_premium() -> None:
    with pytest.raises(ValueError, match="requires an explicit plan premium"):
        MedicarePersonInput(owner_id="owner", part_d_enrollment_date=date(2026, 1, 1))


def test_two_spouses_are_calculated_separately(rules: MedicareIrmaaRules) -> None:
    result = calculate_annual_irmaa(
        rules,
        [
            MedicarePersonInput(
                owner_id="kevin",
                part_b_enrollment_date=date(2025, 1, 1),
                part_d_enrollment_date=date(2025, 1, 1),
                part_d_plan_monthly_premium=Decimal("30.00"),
            ),
            MedicarePersonInput(
                owner_id="joan",
                part_b_enrollment_date=date(2026, 9, 1),
                part_d_enrollment_date=date(2026, 9, 1),
                part_d_plan_monthly_premium=Decimal("45.00"),
            ),
        ],
        {2024: record(2024, FilingStatus.MARRIED_FILING_JOINTLY, "250000")},
        {},
    )
    assert [(person.owner_id, person.part_b_months) for person in result.people] == [
        ("kevin", 12),
        ("joan", 4),
    ]
    assert result.household_part_b_base == Decimal("3246.40")
    assert result.household_part_b_irmaa == Decimal("1299.20")
    assert result.household_part_d_plan == Decimal("540.00")
    assert result.household_part_d_irmaa == Decimal("232.00")


def test_2028_uses_2026_magi() -> None:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset(
        "medicare_irmaa", "US-FED", 2026
    )
    future_rules = MedicareIrmaaRules.from_dataset(
        dataset.model_copy(
            update={
                "dataset_id": "test-2028",
                "premium_year": 2028,
                "effective_from": date(2028, 1, 1),
                "effective_to": date(2028, 12, 31),
            }
        )
    )
    result = calculate_annual_irmaa(
        future_rules,
        [],
        {2026: record(2026, FilingStatus.MARRIED_FILING_JOINTLY, "300000")},
        {},
    )
    assert result.magi_tax_year == 2026
    assert result.magi.source == "completed_projection"
    assert result.magi.irmaa_magi == Decimal("300000")
