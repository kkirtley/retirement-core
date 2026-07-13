from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from retirement_core.domain.enums import FilingStatus
from retirement_core.rules.models import Provenance, RuleDataset


class SelfEmploymentTaxDatasetProvider(Protocol):
    def get_dataset(self, dataset_type: str, jurisdiction: str, year: int) -> RuleDataset: ...


class FederalSelfEmploymentTaxRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: str
    tax_year: int
    net_earnings_adjustment_factor: Decimal = Field(ge=0, le=1)
    social_security_tax_rate: Decimal = Field(ge=0, le=1)
    medicare_tax_rate: Decimal = Field(ge=0, le=1)
    social_security_wage_base: Decimal = Field(ge=0)
    minimum_net_earnings_filing_threshold: Decimal = Field(ge=0)
    employer_equivalent_deduction_rate: Decimal = Field(ge=0, le=1)
    additional_medicare_tax_rate: Decimal = Field(ge=0, le=1)
    additional_medicare_thresholds: dict[FilingStatus, Decimal]
    provenance: Provenance

    @classmethod
    def from_dataset(cls, dataset: RuleDataset) -> FederalSelfEmploymentTaxRules:
        if (
            dataset.dataset_type != "federal_self_employment_tax"
            or dataset.jurisdiction != "US-FED"
        ):
            raise ValueError("Expected a US-FED federal_self_employment_tax rule dataset")
        if dataset.tax_year is None:
            raise ValueError("Federal self-employment tax dataset must define tax_year")
        return cls(
            dataset_id=dataset.dataset_id,
            tax_year=dataset.tax_year,
            provenance=dataset.provenance,
            **dataset.values,
        )

    @classmethod
    def load_for_tax_year(
        cls, provider: SelfEmploymentTaxDatasetProvider, tax_year: int
    ) -> FederalSelfEmploymentTaxRules:
        """Load only the explicitly requested year; providers do not carry rules forward."""
        return cls.from_dataset(
            provider.get_dataset("federal_self_employment_tax", "US-FED", tax_year)
        )
