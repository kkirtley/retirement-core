from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.projection import run_projection
from retirement_core.rules.interfaces import RuleDatasetProvider
from retirement_core.rules.models import FederalTaxRules


class ProjectionService:
    def __init__(self, rule_provider: RuleDatasetProvider) -> None:
        self._rule_provider = rule_provider

    def run(self, request: ProjectionRequest) -> ProjectionResult:
        federal_tax_rules = None
        if request.plan.start_date.year <= 2026 <= request.plan.end_date.year:
            dataset = self._rule_provider.get_dataset("federal_tax", "US-FED", 2026)
            federal_tax_rules = FederalTaxRules.from_dataset(dataset, request.plan.filing_status)
        return run_projection(request, federal_tax_rules)
