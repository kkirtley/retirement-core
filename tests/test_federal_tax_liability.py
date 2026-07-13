from decimal import Decimal

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.domain.models import FederalIncomeTaxResult
from retirement_core.engine.federal_tax_liability import aggregate_federal_tax_liability
from retirement_core.engine.self_employment_tax import (
    AdditionalMedicareTaxResult,
    OwnerSelfEmploymentTaxResult,
)


def _ordinary(amount: str = "100") -> FederalIncomeTaxResult:
    return FederalIncomeTaxResult(
        gross_income=Decimal("0"),
        standard_deduction=Decimal("0"),
        taxable_income=Decimal("0"),
        tax_by_bracket=(),
        total_federal_tax=Decimal(amount),
        marginal_bracket=None,
    )


def _se(owner: str = "a", tax_year: int = 2026, tax: str = "20") -> OwnerSelfEmploymentTaxResult:
    return OwnerSelfEmploymentTaxResult(
        owner_id=owner,
        tax_year=tax_year,
        net_business_profit=Decimal("1000"),
        net_earnings=Decimal("923.50"),
        filing_threshold=Decimal("400"),
        w2_social_security_wages=Decimal("0"),
        remaining_social_security_wage_base=Decimal("184500"),
        social_security_tax_base=Decimal("923.50"),
        social_security_tax=Decimal("10"),
        medicare_tax=Decimal("10"),
        regular_self_employment_tax=Decimal(tax),
        deductible_employer_equivalent_tax=Decimal("10"),
        dataset_id="se-2026",
        rule_provenance="SE rules",
    )


def _additional(
    year: int = 2026, status: FilingStatus = FilingStatus.MARRIED_FILING_JOINTLY, tax: str = "9"
) -> AdditionalMedicareTaxResult:
    return AdditionalMedicareTaxResult(
        household_medicare_wages=Decimal("0"),
        tax_year=year,
        filing_status=status,
        household_self_employment_medicare_earnings=Decimal("0"),
        threshold=Decimal("250000"),
        wage_excess_subject_to_tax=Decimal("0"),
        self_employment_excess_subject_to_tax=Decimal("0"),
        total_additional_medicare_tax=Decimal(tax),
        withholding=Decimal("3"),
        dataset_id="se-2026",
        rule_provenance="SE rules",
    )


def _aggregate(
    se: tuple[OwnerSelfEmploymentTaxResult, ...] = (),
    additional: AdditionalMedicareTaxResult | None = None,
):
    return aggregate_federal_tax_liability(
        2026,
        FilingStatus.MARRIED_FILING_JOINTLY,
        _ordinary(),
        "fed-2026",
        "Federal rules",
        se,
        additional,
    )


def test_ordinary_only_and_all_components() -> None:
    assert _aggregate().total_federal_tax_liability == Decimal("100")
    result = _aggregate((_se(), _se("b", tax="30")), _additional())
    assert result.total_federal_tax_liability == Decimal("159")
    assert result.regular_self_employment_tax == Decimal("50")
    assert result.additional_medicare_tax_withholding == Decimal("3")
    assert result.regular_self_employment_details[0].deductible_employer_equivalent_tax == Decimal(
        "10"
    )


def test_component_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        _aggregate((_se(), _se()))
    with pytest.raises(ValueError, match="year"):
        _aggregate((_se(tax_year=2027),))
    with pytest.raises(ValueError, match="filing status"):
        _aggregate((), _additional(status=FilingStatus.SINGLE))
    invalid = _additional()
    with pytest.raises(ValueError, match="structurally inconsistent"):
        _aggregate((), invalid.model_copy(update={"dataset_id": ""}))
