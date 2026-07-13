from decimal import Decimal

from retirement_core.domain.enums import FilingStatus
from retirement_core.domain.federal_liability import (
    FederalRegularSelfEmploymentLiabilityDetail,
    FederalTaxLiabilityResult,
)
from retirement_core.domain.models import FederalIncomeTaxResult
from retirement_core.engine.self_employment_tax import (
    AdditionalMedicareTaxResult,
    OwnerSelfEmploymentTaxResult,
)


def aggregate_federal_tax_liability(
    tax_year: int,
    filing_status: FilingStatus,
    ordinary_income_tax: FederalIncomeTaxResult,
    ordinary_income_tax_dataset_id: str,
    ordinary_income_tax_rule_provenance: str,
    self_employment_results: tuple[OwnerSelfEmploymentTaxResult, ...] = (),
    additional_medicare_tax: AdditionalMedicareTaxResult | None = None,
) -> FederalTaxLiabilityResult:
    if ordinary_income_tax.total_federal_tax < 0:
        raise ValueError("Ordinary federal income tax cannot be negative")
    if not ordinary_income_tax_dataset_id or not ordinary_income_tax_rule_provenance:
        raise ValueError("Ordinary income tax dataset ID and provenance are required")
    owners = [item.owner_id for item in self_employment_results]
    if len(owners) != len(set(owners)):
        raise ValueError("Duplicate self-employment results would double-count an owner")
    for item in self_employment_results:
        if item.tax_year != tax_year:
            raise ValueError("Self-employment tax year does not match liability tax year")
        if (
            item.net_business_profit < 0
            or item.regular_self_employment_tax < 0
            or item.deductible_employer_equivalent_tax < 0
        ):
            raise ValueError("Self-employment liability components cannot be negative")
        if item.deductible_employer_equivalent_tax > item.regular_self_employment_tax:
            raise ValueError("Deductible SE tax cannot exceed regular self-employment tax")
        if not item.dataset_id or not item.rule_provenance:
            raise ValueError("Self-employment dataset ID and provenance are required")
    if self_employment_results:
        dataset_ids = {item.dataset_id for item in self_employment_results}
        provenances = {item.rule_provenance for item in self_employment_results}
        if len(dataset_ids) != 1:
            raise ValueError("Self-employment results must use one consistent dataset ID")
        if len(provenances) != 1:
            raise ValueError("Self-employment results must use one consistent rule provenance")
    if additional_medicare_tax is not None:
        if additional_medicare_tax.tax_year != tax_year:
            raise ValueError("Additional Medicare tax year does not match liability tax year")
        if additional_medicare_tax.filing_status is not filing_status:
            raise ValueError(
                "Additional Medicare filing status does not match liability filing status"
            )
        if (
            additional_medicare_tax.total_additional_medicare_tax < 0
            or additional_medicare_tax.withholding < 0
            or not additional_medicare_tax.dataset_id
            or not additional_medicare_tax.rule_provenance
        ):
            raise ValueError("Additional Medicare result is structurally inconsistent")
    regular = sum(
        (item.regular_self_employment_tax for item in self_employment_results), Decimal("0")
    )
    additional = (
        additional_medicare_tax.total_additional_medicare_tax
        if additional_medicare_tax is not None
        else Decimal("0")
    )
    return FederalTaxLiabilityResult(
        tax_year=tax_year,
        filing_status=filing_status,
        ordinary_federal_income_tax=ordinary_income_tax.total_federal_tax,
        regular_self_employment_tax=regular,
        additional_medicare_tax=additional,
        gross_federal_tax_liability=ordinary_income_tax.total_federal_tax + regular + additional,
        regular_self_employment_details=tuple(
            FederalRegularSelfEmploymentLiabilityDetail(
                owner_id=item.owner_id,
                net_business_profit=item.net_business_profit,
                regular_self_employment_tax=item.regular_self_employment_tax,
                deductible_employer_equivalent_tax=item.deductible_employer_equivalent_tax,
                dataset_id=item.dataset_id,
                rule_provenance=item.rule_provenance,
            )
            for item in self_employment_results
        ),
        ordinary_income_tax_dataset_id=ordinary_income_tax_dataset_id,
        ordinary_income_tax_rule_provenance=ordinary_income_tax_rule_provenance,
        self_employment_tax_dataset_id=(
            self_employment_results[0].dataset_id if self_employment_results else None
        ),
        self_employment_tax_rule_provenance=(
            self_employment_results[0].rule_provenance if self_employment_results else None
        ),
        additional_medicare_tax_dataset_id=(
            additional_medicare_tax.dataset_id if additional_medicare_tax else None
        ),
        additional_medicare_tax_rule_provenance=(
            additional_medicare_tax.rule_provenance if additional_medicare_tax else None
        ),
        additional_medicare_tax_withholding=(
            additional_medicare_tax.withholding if additional_medicare_tax else Decimal("0")
        ),
    )
