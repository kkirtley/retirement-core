from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.models import MissouriOwnerTaxResult, MissouriTaxResult
from retirement_core.rules.missouri_tax import MissouriTaxRules

ZERO = Decimal("0")


class MissouriOwnerIncome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    date_of_birth: date
    public_pension: Decimal = Field(default=ZERO, ge=0)
    private_pension: Decimal = Field(default=ZERO, ge=0)
    taxable_wages: Decimal = Field(default=ZERO, ge=0)
    taxable_ira_withdrawal: Decimal = Field(default=ZERO, ge=0)
    taxable_rmd: Decimal = Field(default=ZERO, ge=0)
    taxable_roth_conversion: Decimal = Field(default=ZERO, ge=0)
    gross_social_security: Decimal = Field(default=ZERO, ge=0)
    taxable_social_security_retirement: Decimal = Field(default=ZERO, ge=0)
    taxable_social_security_disability: Decimal = Field(default=ZERO, ge=0)


def calculate_missouri_income_tax(
    owners: tuple[MissouriOwnerIncome, ...],
    federal_income_tax: Decimal,
    rules: MissouriTaxRules,
) -> MissouriTaxResult:
    if len(owners) != 2 or len({owner.owner_id for owner in owners}) != 2:
        raise ValueError("Missouri married filing combined requires two distinct owners")
    if federal_income_tax < 0:
        raise ValueError("Federal tax cannot be negative")
    year_end = date(rules.tax_year, 12, 31)
    owner_agi: dict[str, Decimal] = {}
    owner_ss_subtraction: dict[str, Decimal] = {}
    public_subtraction = ZERO
    preliminary_private = ZERO
    for owner in owners:
        taxable_ss = (
            owner.taxable_social_security_retirement + owner.taxable_social_security_disability
        )
        owner_agi[owner.owner_id] = (
            owner.taxable_wages
            + owner.public_pension
            + owner.private_pension
            + owner.taxable_ira_withdrawal
            + owner.taxable_rmd
            + (
                owner.taxable_roth_conversion
                if rules.roth_conversion.included_in_missouri_agi
                else ZERO
            )
            + taxable_ss
        )
        age = (
            year_end.year
            - owner.date_of_birth.year
            - (
                (year_end.month, year_end.day)
                < (owner.date_of_birth.month, owner.date_of_birth.day)
            )
        )
        retirement_subtraction = (
            owner.taxable_social_security_retirement * rules.social_security_subtraction_rate
            if age >= rules.social_security_retirement_age
            else ZERO
        )
        disability_subtraction = (
            owner.taxable_social_security_disability * rules.social_security_subtraction_rate
        )
        ss_subtraction = retirement_subtraction + disability_subtraction
        owner_ss_subtraction[owner.owner_id] = ss_subtraction
        public_cap = rules.public_pension_maximum_per_owner
        if rules.public_pension_reduced_by_owner_social_security:
            public_cap = max(ZERO, public_cap - ss_subtraction)
        public_subtraction += min(owner.public_pension, public_cap)
        preliminary_private += min(
            owner.private_pension + owner.taxable_ira_withdrawal + owner.taxable_rmd,
            rules.private_retirement_maximum_per_owner,
        )

    gross_basis = sum(owner_agi.values(), ZERO)
    social_security_subtraction = sum(owner_ss_subtraction.values(), ZERO)
    private_phaseout_income = max(
        ZERO,
        gross_basis
        - sum(
            (
                owner.taxable_social_security_retirement + owner.taxable_social_security_disability
                for owner in owners
            ),
            ZERO,
        )
        - rules.private_retirement_combined_threshold,
    )
    private_subtraction = max(
        ZERO,
        preliminary_private - private_phaseout_income * rules.private_retirement_phaseout_rate,
    )
    federal_percentage = next(
        band.percentage
        for band in rules.federal_deduction_bands
        if band.upper_income is None or gross_basis <= band.upper_income
    )
    federal_deduction = min(
        federal_income_tax * federal_percentage,
        rules.federal_deduction_maximum,
    )
    aged_count = sum(
        1
        for owner in owners
        if (
            year_end.year
            - owner.date_of_birth.year
            - (
                (year_end.month, year_end.day)
                < (owner.date_of_birth.month, owner.date_of_birth.day)
            )
        )
        >= rules.additional_age_threshold
    )
    standard_deduction = rules.standard_deduction + rules.additional_age_amount * aged_count
    total_adjustments = (
        social_security_subtraction
        + public_subtraction
        + private_subtraction
        + federal_deduction
        + standard_deduction
    )
    taxable_income = max(ZERO, gross_basis - total_adjustments)
    owner_results: list[MissouriOwnerTaxResult] = []
    for owner in owners:
        percentage = owner_agi[owner.owner_id] / gross_basis if gross_basis > 0 else ZERO
        owner_taxable = taxable_income * percentage
        tax = _calculate_bracket_tax(owner_taxable, rules)
        owner_results.append(
            MissouriOwnerTaxResult(
                owner_id=owner.owner_id,
                missouri_agi=owner_agi[owner.owner_id],
                income_percentage=percentage,
                taxable_income=owner_taxable,
                tax=tax,
            )
        )
    total_tax = sum((owner.tax for owner in owner_results), ZERO)
    return MissouriTaxResult(
        dataset_id=rules.dataset_id,
        dataset_version=rules.dataset_version,
        gross_income_basis=gross_basis,
        social_security_subtraction=social_security_subtraction,
        public_pension_subtraction=public_subtraction,
        private_retirement_subtraction=private_subtraction,
        federal_tax_deduction=federal_deduction,
        standard_deduction=standard_deduction,
        total_adjustments_and_subtractions=total_adjustments,
        taxable_income=taxable_income,
        total_tax=total_tax,
        owners=tuple(owner_results),
    )


def _calculate_bracket_tax(taxable_income: Decimal, rules: MissouriTaxRules) -> Decimal:
    tax = ZERO
    for bracket in rules.brackets:
        taxable_at_rate = (
            min(taxable_income, bracket.upper_bound)
            if bracket.upper_bound is not None
            else taxable_income
        )
        amount = max(ZERO, taxable_at_rate - bracket.lower_bound)
        tax += amount * bracket.rate
        if bracket.upper_bound is None or taxable_income <= bracket.upper_bound:
            break
    return (tax / rules.tax_rounding_increment).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    ) * rules.tax_rounding_increment
