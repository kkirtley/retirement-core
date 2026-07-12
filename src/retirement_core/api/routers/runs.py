from fastapi import APIRouter, Depends

from retirement_core.api.dependencies import get_projection_service
from retirement_core.application.services import ProjectionService
from retirement_core.domain.models import ProjectionRequest, ProjectionResult

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=ProjectionResult)
def create_run(
    request: ProjectionRequest,
    service: ProjectionService = Depends(get_projection_service),
) -> ProjectionResult:
    return service.run(request)
