from decimal import ROUND_HALF_UP, Decimal

from retirement_core.domain.models import (
    FederalBracketTax,
    FederalIncomeTaxResult,
    FederalMarginalBracket,
)
from retirement_core.rules.models import FederalTaxRules

CENT = Decimal("0.01")


def calculate_federal_income_tax(
    gross_ordinary_income: Decimal,
    rules: FederalTaxRules,
) -> FederalIncomeTaxResult:
    """Calculate federal tax on ordinary income before the standard deduction.

    ``gross_ordinary_income`` is not AGI and is not taxable income. It may include a
    taxable Social Security amount calculated by the caller, but this function does not
    calculate that amount. Capital gains, credits, itemized deductions, and other tax
    features outside this phase are excluded.
    """
    if gross_ordinary_income < 0:
        raise ValueError("Gross ordinary income cannot be negative")
    taxable_income = max(gross_ordinary_income - rules.standard_deduction, Decimal("0"))
    bracket_taxes: list[FederalBracketTax] = []
    marginal_bracket: FederalMarginalBracket | None = None

    if taxable_income > 0:
        for bracket in rules.ordinary_income_brackets:
            upper = bracket.upper_bound
            taxable_at_rate = min(taxable_income, upper) if upper is not None else taxable_income
            income_taxed = max(taxable_at_rate - bracket.lower_bound, Decimal("0"))
            if income_taxed > 0:
                bracket_taxes.append(
                    FederalBracketTax(
                        lower_bound=bracket.lower_bound,
                        upper_bound=upper,
                        rate=bracket.rate,
                        income_taxed=income_taxed,
                        tax=(income_taxed * bracket.rate).quantize(CENT, rounding=ROUND_HALF_UP),
                    )
                )
            if upper is None or taxable_income <= upper:
                marginal_bracket = FederalMarginalBracket(
                    lower_bound=bracket.lower_bound,
                    upper_bound=bracket.upper_bound,
                    rate=bracket.rate,
                )
                break

    total_tax = sum((bracket.tax for bracket in bracket_taxes), Decimal("0"))
    return FederalIncomeTaxResult(
        gross_income=gross_ordinary_income,
        standard_deduction=rules.standard_deduction,
        taxable_income=taxable_income,
        tax_by_bracket=tuple(bracket_taxes),
        total_federal_tax=total_tax,
        marginal_bracket=marginal_bracket,
    )
