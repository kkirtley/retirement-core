from retirement_core.domain.enums import AccountType
from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.projection import run_projection
from retirement_core.rules.interfaces import RuleDatasetProvider
from retirement_core.rules.missouri_tax import MissouriTaxRules
from retirement_core.rules.models import FederalTaxRules
from retirement_core.rules.rmd_qcd import RmdQcdRules


class ProjectionService:
    def __init__(self, rule_provider: RuleDatasetProvider) -> None:
        self._rule_provider = rule_provider

    def run(self, request: ProjectionRequest) -> ProjectionResult:
        federal_tax_rules = None
        if request.plan.start_date.year <= 2026 <= request.plan.end_date.year:
            dataset = self._rule_provider.get_dataset("federal_tax", "US-FED", 2026)
            federal_tax_rules = FederalTaxRules.from_dataset(dataset, request.plan.filing_status)
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
        return run_projection(
            request,
            federal_tax_rules,
            rmd_qcd_rules_by_year,
            missouri_tax_rules_by_year,
        )
