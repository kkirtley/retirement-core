from retirement_core.domain.models import ProjectionRequest, ProjectionResult
from retirement_core.engine.projection import run_projection


class ProjectionService:
    def run(self, request: ProjectionRequest) -> ProjectionResult:
        return run_projection(request)
