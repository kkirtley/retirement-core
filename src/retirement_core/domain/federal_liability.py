from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from retirement_core.domain.enums import FilingStatus


class FederalRegularSelfEmploymentLiabilityDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    net_business_profit: Decimal = Field(ge=0)
    regular_self_employment_tax: Decimal = Field(ge=0)
    deductible_employer_equivalent_tax: Decimal = Field(ge=0)
    dataset_id: str
    rule_provenance: str


class FederalTaxLiabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tax_year: int
    filing_status: FilingStatus
    ordinary_federal_income_tax: Decimal = Field(ge=0)
    regular_self_employment_tax: Decimal = Field(ge=0)
    additional_medicare_tax: Decimal = Field(ge=0)
    gross_federal_tax_liability: Decimal = Field(ge=0)
    regular_self_employment_details: tuple[FederalRegularSelfEmploymentLiabilityDetail, ...]
    ordinary_income_tax_dataset_id: str
    ordinary_income_tax_rule_provenance: str
    self_employment_tax_dataset_id: str | None = None
    self_employment_tax_rule_provenance: str | None = None
    additional_medicare_tax_dataset_id: str | None = None
    additional_medicare_tax_rule_provenance: str | None = None
    additional_medicare_tax_withholding: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> FederalTaxLiabilityResult:
        if self.gross_federal_tax_liability != (
            self.ordinary_federal_income_tax
            + self.regular_self_employment_tax
            + self.additional_medicare_tax
        ):
            raise ValueError("Federal tax liability total must equal its components")
        return self
