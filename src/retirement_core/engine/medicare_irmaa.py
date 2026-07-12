from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from retirement_core.domain.medicare import (
    AnnualIrmaaResult,
    IrmaaMagiComposition,
    IrmaaMagiResult,
    IrmaaTaxRecordInput,
    MedicarePersonInput,
    MedicarePersonPremiumResult,
)
from retirement_core.rules.models import IrmaaTier, MedicareIrmaaRules

CENT = Decimal("0.01")


def calculate_irmaa_magi(composition: IrmaaMagiComposition) -> IrmaaTaxRecordInput:
    """Build IRMAA MAGI as federal AGI plus tax-exempt interest; QCD stays excluded."""
    return IrmaaTaxRecordInput(
        tax_year=composition.tax_year,
        filing_status=composition.filing_status,
        federal_adjusted_gross_income=composition.federal_adjusted_gross_income,
        tax_exempt_interest=composition.tax_exempt_interest,
    )


def resolve_lookback_magi(
    premium_year: int,
    lookback_years: int,
    completed_projection_tax_records: Mapping[int, IrmaaTaxRecordInput],
    historical_tax_records: Mapping[int, IrmaaTaxRecordInput],
) -> IrmaaMagiResult:
    tax_year = premium_year - lookback_years
    record = completed_projection_tax_records.get(tax_year)
    source: Literal["completed_projection", "historical_tax_record"] = "completed_projection"
    if record is None:
        record = historical_tax_records.get(tax_year)
        source = "historical_tax_record"
    if record is None:
        raise ValueError(f"IRMAA premium year {premium_year} requires MAGI for tax year {tax_year}")
    if record.tax_year != tax_year:
        raise ValueError("IRMAA tax record year does not match its lookup key")
    return IrmaaMagiResult(
        tax_year=tax_year,
        filing_status=record.filing_status,
        source=source,
        federal_adjusted_gross_income=record.federal_adjusted_gross_income,
        tax_exempt_interest=record.tax_exempt_interest,
        irmaa_magi=record.irmaa_magi,
    )


def calculate_annual_irmaa(
    rules: MedicareIrmaaRules,
    people: list[MedicarePersonInput],
    completed_projection_tax_records: Mapping[int, IrmaaTaxRecordInput],
    historical_tax_records: Mapping[int, IrmaaTaxRecordInput],
) -> AnnualIrmaaResult:
    magi = resolve_lookback_magi(
        rules.premium_year,
        rules.magi_lookback_years,
        completed_projection_tax_records,
        historical_tax_records,
    )
    try:
        tiers = rules.filing_statuses[magi.filing_status].tiers
    except KeyError as error:
        raise ValueError(
            f"IRMAA rules do not support filing status {magi.filing_status.value}"
        ) from error
    tier_index, tier = next(
        (index, tier) for index, tier in enumerate(tiers) if _contains(tier, magi.irmaa_magi)
    )

    person_results = tuple(
        _calculate_person(rules.premium_year, rules.part_b_standard_monthly_premium, tier, person)
        for person in people
    )
    part_b_base = sum((item.annual_part_b_base for item in person_results), Decimal("0"))
    part_b_irmaa = sum((item.annual_part_b_irmaa for item in person_results), Decimal("0"))
    part_d_plan = sum((item.annual_part_d_plan for item in person_results), Decimal("0"))
    part_d_irmaa = sum((item.annual_part_d_irmaa for item in person_results), Decimal("0"))
    return AnnualIrmaaResult(
        premium_year=rules.premium_year,
        magi_tax_year=magi.tax_year,
        rule_dataset_id=rules.dataset_id,
        magi=magi,
        tier_index=tier_index,
        people=person_results,
        household_part_b_base=part_b_base,
        household_part_b_irmaa=part_b_irmaa,
        household_part_d_plan=part_d_plan,
        household_part_d_irmaa=part_d_irmaa,
        household_total=part_b_base + part_b_irmaa + part_d_plan + part_d_irmaa,
    )


def _contains(tier: IrmaaTier, amount: Decimal) -> bool:
    lower_ok = tier.lower_bound is None or (
        amount >= tier.lower_bound if tier.lower_inclusive else amount > tier.lower_bound
    )
    upper_ok = tier.upper_bound is None or (
        amount <= tier.upper_bound if tier.upper_inclusive else amount < tier.upper_bound
    )
    return lower_ok and upper_ok


def _enrollment_months(enrollment_date: date | None, premium_year: int) -> int:
    if enrollment_date is None or enrollment_date.year > premium_year:
        return 0
    if enrollment_date.year < premium_year:
        return 12
    return 13 - enrollment_date.month


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _calculate_person(
    premium_year: int,
    part_b_base: Decimal,
    tier: IrmaaTier,
    person: MedicarePersonInput,
) -> MedicarePersonPremiumResult:
    part_b_months = _enrollment_months(person.part_b_enrollment_date, premium_year)
    part_d_months = _enrollment_months(person.part_d_enrollment_date, premium_year)
    part_d_plan_monthly = person.part_d_plan_monthly_premium or Decimal("0")
    annual_part_b_base = _money(part_b_base * part_b_months)
    annual_part_b_irmaa = _money(tier.part_b_irmaa_monthly * part_b_months)
    annual_part_d_plan = _money(part_d_plan_monthly * part_d_months)
    annual_part_d_irmaa = _money(tier.part_d_irmaa_monthly * part_d_months)
    return MedicarePersonPremiumResult(
        owner_id=person.owner_id,
        part_b_months=part_b_months,
        part_d_months=part_d_months,
        part_b_base_monthly=part_b_base,
        part_b_irmaa_monthly=tier.part_b_irmaa_monthly,
        part_d_plan_monthly=part_d_plan_monthly,
        part_d_irmaa_monthly=tier.part_d_irmaa_monthly,
        annual_part_b_base=annual_part_b_base,
        annual_part_b_irmaa=annual_part_b_irmaa,
        annual_part_d_plan=annual_part_d_plan,
        annual_part_d_irmaa=annual_part_d_irmaa,
        annual_total=(
            annual_part_b_base + annual_part_b_irmaa + annual_part_d_plan + annual_part_d_irmaa
        ),
    )
