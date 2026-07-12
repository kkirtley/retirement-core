from decimal import Decimal
from pathlib import Path

import pytest

from retirement_core.domain.enums import FilingStatus
from retirement_core.engine.social_security_tax import calculate_taxable_social_security
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules


@pytest.fixture(scope="module")
def federal_tax_rules() -> FederalTaxRules:
    dataset = JsonRuleDatasetProvider(Path("data/rules")).get_dataset("federal_tax", "US-FED", 2026)
    return FederalTaxRules.from_dataset(dataset, FilingStatus.MARRIED_FILING_JOINTLY)


@pytest.mark.parametrize(
    (
        "case",
        "gross_benefits",
        "other_income",
        "provisional_income",
        "taxable_benefits",
    ),
    [
        ("no Social Security", "0", "0", "0", "0"),
        ("below first threshold", "20000", "20000", "30000", "0"),
        ("inside 50 percent range", "20000", "25000", "35000", "1500"),
        ("inside 85 percent range", "20000", "40000", "50000", "11100"),
        ("85 percent benefit cap", "20000", "100000", "110000", "17000"),
        ("Roth conversion raises taxation", "20000", "30000", "40000", "4000"),
        ("pension raises taxation", "20000", "30000", "40000", "4000"),
    ],
    ids=lambda value: value if isinstance(value, str) and not value.isdigit() else None,
)
def test_2026_mfj_taxable_social_security(
    federal_tax_rules: FederalTaxRules,
    case: str,
    gross_benefits: str,
    other_income: str,
    provisional_income: str,
    taxable_benefits: str,
) -> None:
    del case
    result = calculate_taxable_social_security(
        Decimal(gross_benefits),
        Decimal(other_income),
        federal_tax_rules.social_security_taxation,
    )

    assert result.provisional_income_scope == "currently_modeled_supported_sources_only"
    assert result.provisional_income == Decimal(provisional_income)
    assert result.taxable_social_security == Decimal(taxable_benefits)
    assert result.taxable_social_security <= result.maximum_taxable_social_security


def test_social_security_rule_source_is_attributed(
    federal_tax_rules: FederalTaxRules,
) -> None:
    source = federal_tax_rules.provenance.additional_sources[0]
    assert source.source_title.startswith("26 U.S.C. Section 86")
    assert source.source_reference == "Subsections (a), (b), and (c)"
