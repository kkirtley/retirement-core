from retirement_core.domain.enums import AccountType
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.projection import run_projection
from retirement_core.rules.interfaces import RuleDatasetProvider
from retirement_core.rules.missouri_tax import MissouriTaxRules
from retirement_core.rules.models import FederalTaxRules, MedicareIrmaaRules
from retirement_core.rules.rmd_qcd import RmdQcdRules


class ProjectionService:
    def __init__(self, rule_provider: RuleDatasetProvider) -> None:
        self._rule_provider = rule_provider

    def run(self, request: ProjectionRequest) -> ProjectionResult:
        federal_tax_rules_by_year: dict[int, FederalTaxRules] = {}
        for year in range(request.plan.start_date.year, request.plan.end_date.year + 1):
            try:
                dataset = self._rule_provider.get_dataset("federal_tax", "US-FED", year)
            except FileNotFoundError:
                continue
            federal_tax_rules_by_year[year] = FederalTaxRules.from_dataset(
                dataset, request.plan.filing_status
            )
        rmd_qcd_rules_by_year: dict[int, RmdQcdRules] = {}
        pretax_account_types = {AccountType.TRADITIONAL_IRA, AccountType.TRADITIONAL_401K}
        requires_rmd_qcd = bool(request.plan.people) and any(
            account.account_type in pretax_account_types for account in request.plan.accounts
        )
        if requires_rmd_qcd:
            for year in range(request.plan.start_date.year, request.plan.end_date.year + 1):
                try:
                    dataset = self._rule_provider.get_applicable_dataset("rmd_qcd", "US-FED", year)
                except FileNotFoundError as error:
                    raise ValueError(
                        f"No applicable RMD/QCD rule dataset exists for projection year {year}"
                    ) from error
                rmd_qcd_rules_by_year[year] = RmdQcdRules.from_dataset(dataset)
        missouri_tax_rules_by_year: dict[int, MissouriTaxRules] = {}
        if (
            request.plan.state_residency is not None
            and request.plan.state_residency.state_code == "MO"
        ):
            for year in range(request.plan.start_date.year, request.plan.end_date.year + 1):
                try:
                    dataset = self._rule_provider.get_applicable_dataset(
                        "missouri_tax", "US-MO", year
                    )
                except FileNotFoundError as error:
                    raise ValueError(
                        f"No applicable Missouri tax rule dataset exists for {year}"
                    ) from error
                missouri_tax_rules_by_year[year] = MissouriTaxRules.from_dataset(dataset)
        medicare_irmaa_rules_by_year: dict[int, MedicareIrmaaRules] = {}
        if request.plan.medicare is not None:
            for year in range(request.plan.start_date.year, request.plan.end_date.year + 1):
                if not any(
                    (
                        person.part_b_enrollment_date is not None
                        and person.part_b_enrollment_date.year <= year
                    )
                    or (
                        person.part_d_enrollment_date is not None
                        and person.part_d_enrollment_date.year <= year
                    )
                    for person in request.plan.medicare.people
                ):
                    continue
                try:
                    dataset = self._rule_provider.get_dataset("medicare_irmaa", "US-FED", year)
                except FileNotFoundError as error:
                    raise ValueError(
                        f"No applicable Medicare/IRMAA rule dataset exists for {year}"
                    ) from error
                medicare_irmaa_rules_by_year[year] = MedicareIrmaaRules.from_dataset(dataset)
        return run_projection(
            request,
            rmd_qcd_rules_by_year=rmd_qcd_rules_by_year,
            missouri_tax_rules_by_year=missouri_tax_rules_by_year,
            medicare_irmaa_rules_by_year=medicare_irmaa_rules_by_year,
            federal_tax_rules_by_year=federal_tax_rules_by_year,
        )
