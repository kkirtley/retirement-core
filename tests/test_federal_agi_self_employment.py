from decimal import Decimal

import pytest

from retirement_core.domain.models import ProjectionRequest
from retirement_core.domain.tax import AnnualSelfEmploymentTaxResult
from retirement_core.engine.federal_agi import (
    build_annual_federal_agi,
    supported_provisional_income_before_social_security,
)


def _request() -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Synthetic",
                "filing_status": "married_filing_jointly",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "people": [
                    {"id": "a", "name": "A", "date_of_birth": "1960-01-01"},
                    {"id": "b", "name": "B", "date_of_birth": "1960-01-01"},
                ],
                "accounts": [],
            }
        }
    )


def _se(
    owner: str = "a", profit: str = "10000", deduction: str = "706.48"
) -> AnnualSelfEmploymentTaxResult:
    return AnnualSelfEmploymentTaxResult(
        owner_id=owner,
        net_business_profit=Decimal(profit),
        regular_self_employment_tax=Decimal("1412.95"),
        deductible_employer_equivalent_tax=Decimal(deduction),
        dataset_id="US-FED-SE-TAX-2026-v1",
        rule_provenance="synthetic phase-1 result",
    )


def _agi(results: tuple[AnnualSelfEmploymentTaxResult, ...] = ()):
    return build_annual_federal_agi(_request(), 2026, [], [], [], None, [], results)


def test_existing_agi_is_unchanged_without_self_employment() -> None:
    agi = _agi()
    assert agi.federal_adjusted_gross_income == 0
    assert agi.self_employment_details == ()


def test_self_employment_profit_and_deduction_affect_agi_and_irmaa() -> None:
    agi = _agi((_se(),))
    assert agi.taxable_self_employment_profit == Decimal("10000")
    assert agi.deductible_self_employment_tax == Decimal("706.48")
    assert agi.net_self_employment_agi_contribution == Decimal("9293.52")
    assert agi.federal_adjusted_gross_income == Decimal("9293.52")
    assert agi.irmaa_magi == Decimal("9293.52")
    assert supported_provisional_income_before_social_security(agi) == Decimal("9293.52")
    assert agi.self_employment_details[0].se_tax_dataset_id == "US-FED-SE-TAX-2026-v1"


def test_multiple_owners_aggregate_without_double_deduction() -> None:
    agi = _agi((_se("a"), _se("b", "20000", "1000")))
    assert agi.taxable_self_employment_profit == Decimal("30000")
    assert agi.deductible_self_employment_tax == Decimal("1706.48")
    assert agi.federal_adjusted_gross_income == Decimal("28293.52")


def test_invalid_and_duplicate_results_fail_closed() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        _agi((_se(), _se()))
    with pytest.raises(ValueError, match="cannot exceed"):
        _se(deduction="2000")
