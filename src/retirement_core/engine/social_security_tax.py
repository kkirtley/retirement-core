from decimal import ROUND_HALF_UP, Decimal

from retirement_core.domain.models import SocialSecurityTaxationResult
from retirement_core.rules.models import FederalSocialSecurityTaxRules

CENT = Decimal("0.01")


def calculate_taxable_social_security(
    gross_social_security: Decimal,
    other_provisional_income: Decimal,
    rules: FederalSocialSecurityTaxRules,
) -> SocialSecurityTaxationResult:
    """Calculate taxable benefits from currently modeled supported sources only."""
    if gross_social_security < 0:
        raise ValueError("Gross Social Security cannot be negative")
    if other_provisional_income < 0:
        raise ValueError("Other provisional income cannot be negative")

    half_benefits = gross_social_security * rules.first_tier_rate
    provisional_income = other_provisional_income + half_benefits
    maximum_taxable = gross_social_security * rules.maximum_taxable_rate

    if provisional_income <= rules.base_amount:
        taxable_benefits = Decimal("0")
    elif provisional_income <= rules.adjusted_base_amount:
        taxable_benefits = min(
            half_benefits,
            (provisional_income - rules.base_amount) * rules.first_tier_rate,
        )
    else:
        first_tier_amount = min(
            half_benefits,
            (rules.adjusted_base_amount - rules.base_amount) * rules.first_tier_rate,
        )
        taxable_benefits = min(
            maximum_taxable,
            first_tier_amount
            + (provisional_income - rules.adjusted_base_amount) * rules.second_tier_rate,
        )

    return SocialSecurityTaxationResult(
        gross_social_security=gross_social_security.quantize(CENT, rounding=ROUND_HALF_UP),
        half_social_security=half_benefits.quantize(CENT, rounding=ROUND_HALF_UP),
        other_provisional_income=other_provisional_income.quantize(CENT, rounding=ROUND_HALF_UP),
        provisional_income=provisional_income.quantize(CENT, rounding=ROUND_HALF_UP),
        base_amount=rules.base_amount,
        adjusted_base_amount=rules.adjusted_base_amount,
        taxable_social_security=taxable_benefits.quantize(CENT, rounding=ROUND_HALF_UP),
        maximum_taxable_social_security=maximum_taxable.quantize(CENT, rounding=ROUND_HALF_UP),
    )
