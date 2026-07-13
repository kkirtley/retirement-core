from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

from retirement_core.domain.enums import FilingStatus
from retirement_core.rules.self_employment_tax import FederalSelfEmploymentTaxRules

CENT = Decimal("0.01")


class OwnerSelfEmploymentTaxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    tax_year: int
    net_business_profit: Decimal
    net_earnings: Decimal
    filing_threshold: Decimal
    w2_social_security_wages: Decimal
    remaining_social_security_wage_base: Decimal
    social_security_tax_base: Decimal
    social_security_tax: Decimal
    medicare_tax: Decimal
    regular_self_employment_tax: Decimal
    deductible_employer_equivalent_tax: Decimal
    dataset_id: str
    rule_provenance: str


class AdditionalMedicareTaxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    household_medicare_wages: Decimal
    tax_year: int
    filing_status: FilingStatus
    household_self_employment_medicare_earnings: Decimal
    threshold: Decimal
    wage_excess_subject_to_tax: Decimal
    self_employment_excess_subject_to_tax: Decimal
    total_additional_medicare_tax: Decimal
    withholding: Decimal
    dataset_id: str
    rule_provenance: str


def calculate_regular_self_employment_tax(
    tax_year: int,
    owner_id: str,
    net_business_profit: Decimal,
    w2_social_security_wages: Decimal,
    rules: FederalSelfEmploymentTaxRules,
) -> OwnerSelfEmploymentTaxResult:
    if tax_year != rules.tax_year:
        raise ValueError("Self-employment tax rules tax year does not match calculation year")
    if net_business_profit < 0:
        raise ValueError("Business losses are unsupported")
    if w2_social_security_wages < 0:
        raise ValueError("W-2 Social Security wages cannot be negative")
    earnings = (net_business_profit * rules.net_earnings_adjustment_factor).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    taxable_earnings = (
        earnings if earnings >= rules.minimum_net_earnings_filing_threshold else Decimal("0")
    )
    remaining = max(Decimal("0"), rules.social_security_wage_base - w2_social_security_wages)
    ss_base = min(taxable_earnings, remaining)
    ss_tax = (ss_base * rules.social_security_tax_rate).quantize(CENT, rounding=ROUND_HALF_UP)
    medicare_tax = (taxable_earnings * rules.medicare_tax_rate).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    regular = ss_tax + medicare_tax
    return OwnerSelfEmploymentTaxResult(
        owner_id=owner_id,
        tax_year=tax_year,
        net_business_profit=net_business_profit,
        net_earnings=earnings,
        filing_threshold=rules.minimum_net_earnings_filing_threshold,
        w2_social_security_wages=w2_social_security_wages,
        remaining_social_security_wage_base=remaining,
        social_security_tax_base=ss_base,
        social_security_tax=ss_tax,
        medicare_tax=medicare_tax,
        regular_self_employment_tax=regular,
        deductible_employer_equivalent_tax=(
            regular * rules.employer_equivalent_deduction_rate
        ).quantize(CENT, rounding=ROUND_HALF_UP),
        dataset_id=rules.dataset_id,
        rule_provenance=rules.provenance.source_title or rules.provenance.publisher,
    )


def calculate_additional_medicare_tax(
    tax_year: int,
    filing_status: FilingStatus,
    medicare_wages_by_source: dict[str, Decimal],
    self_employment_medicare_earnings_by_source: dict[str, Decimal],
    withholding: Decimal,
    rules: FederalSelfEmploymentTaxRules,
) -> AdditionalMedicareTaxResult:
    if tax_year != rules.tax_year:
        raise ValueError("Self-employment tax rules tax year does not match calculation year")
    if any(
        amount < 0
        for amount in (
            *medicare_wages_by_source.values(),
            *self_employment_medicare_earnings_by_source.values(),
            withholding,
        )
    ):
        raise ValueError("Additional Medicare Tax inputs cannot be negative")
    wages = sum(medicare_wages_by_source.values(), Decimal("0"))
    se_medicare = sum(self_employment_medicare_earnings_by_source.values(), Decimal("0"))
    threshold = rules.additional_medicare_thresholds[filing_status]
    wage_excess = max(Decimal("0"), wages - threshold)
    remaining_threshold = max(Decimal("0"), threshold - wages)
    se_excess = max(Decimal("0"), se_medicare - remaining_threshold)
    additional = ((wage_excess + se_excess) * rules.additional_medicare_tax_rate).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return AdditionalMedicareTaxResult(
        household_medicare_wages=wages,
        tax_year=tax_year,
        filing_status=filing_status,
        household_self_employment_medicare_earnings=se_medicare,
        threshold=threshold,
        wage_excess_subject_to_tax=wage_excess,
        self_employment_excess_subject_to_tax=se_excess,
        total_additional_medicare_tax=additional,
        withholding=withholding,
        dataset_id=rules.dataset_id,
        rule_provenance=rules.provenance.source_title or rules.provenance.publisher,
    )
