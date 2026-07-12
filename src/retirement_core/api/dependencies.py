from functools import lru_cache

from retirement_core.application.services import ProjectionService


@lru_cache
def get_projection_service() -> ProjectionService:
    return ProjectionService()
