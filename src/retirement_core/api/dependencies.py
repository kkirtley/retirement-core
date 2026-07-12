from functools import lru_cache

from retirement_core.application.services import ProjectionService
from retirement_core.config import get_settings
from retirement_core.infrastructure.rules.json_provider import JsonRuleDatasetProvider


@lru_cache
def get_projection_service() -> ProjectionService:
    return ProjectionService(JsonRuleDatasetProvider(get_settings().rules_path))
