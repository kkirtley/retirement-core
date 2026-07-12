from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.engine.federal_tax import calculate_federal_income_tax
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules


@pytest.fixture(scope="module")
def federal_tax_rules() -> FederalTaxRules:
    provider = JsonRuleDatasetProvider(Path("data/rules"))
    dataset = provider.get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.mark.parametrize(
    ("gross_income", "taxable_income", "expected_tax", "marginal_rate"),
    [
        ("0.00", "0.00", "0.00", None),
        ("32199.99", "0.00", "0.00", None),
        ("32200.00", "0.00", "0.00", None),
        ("57000.00", "24800.00", "2480.00", "0.10"),
        ("57000.01", "24800.01", "2480.00", "0.12"),
        ("133000.00", "100800.00", "11600.00", "0.12"),
        ("133000.01", "100800.01", "11600.00", "0.22"),
        ("243600.00", "211400.00", "35932.00", "0.22"),
        ("243600.01", "211400.01", "35932.00", "0.24"),
        ("435750.00", "403550.00", "82048.00", "0.24"),
        ("435750.01", "403550.01", "82048.00", "0.32"),
        ("544650.00", "512450.00", "116896.00", "0.32"),
        ("544650.01", "512450.01", "116896.00", "0.35"),
        ("800900.00", "768700.00", "206583.50", "0.35"),
        ("800900.01", "768700.01", "206583.50", "0.37"),
        ("150000.00", "117800.00", "15340.00", "0.22"),
        ("1000000.00", "967800.00", "280250.50", "0.37"),
    ],
)
def test_2026_mfj_ordinary_income_tax(
    federal_tax_rules: FederalTaxRules,
    gross_income: str,
    taxable_income: str,
    expected_tax: str,
    marginal_rate: str | None,
) -> None:
    result = calculate_federal_income_tax(Decimal(gross_income), federal_tax_rules)

    assert result.gross_income == Decimal(gross_income)
    assert result.standard_deduction == Decimal("32200.00")
    assert result.taxable_income == Decimal(taxable_income)
    assert result.total_federal_tax == Decimal(expected_tax)
    assert sum((bracket.tax for bracket in result.tax_by_bracket), Decimal("0")) == Decimal(
        expected_tax
    )
    assert (result.marginal_bracket.rate if result.marginal_bracket is not None else None) == (
        Decimal(marginal_rate) if marginal_rate is not None else None
    )


def test_2026_rules_include_authoritative_provenance(
    federal_tax_rules: FederalTaxRules,
) -> None:
    assert federal_tax_rules.dataset_id == "US-FED-2026-v1"
    assert federal_tax_rules.tax_year == 2026
    assert federal_tax_rules.provenance.publisher == "Internal Revenue Service"
    assert federal_tax_rules.provenance.source_title == "Revenue Procedure 2025-32"
    assert federal_tax_rules.provenance.source_reference == "Sections 4.01 and 4.14(1)"
    assert federal_tax_rules.provenance.effective_date is not None
    assert federal_tax_rules.provenance.effective_date.isoformat() == "2026-01-01"


def test_negative_gross_ordinary_income_is_rejected(
    federal_tax_rules: FederalTaxRules,
) -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        calculate_federal_income_tax(Decimal("-0.01"), federal_tax_rules)
