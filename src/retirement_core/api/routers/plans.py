from fastapi import APIRouter

from retirement_core.domain.models import PlanInput

router = APIRouter(prefix="/plans", tags=["plans"])


@router.post("/validate")
def validate_plan(plan: PlanInput) -> dict[str, object]:
    return {"valid": True, "errors": [], "warnings": [], "schema_version": plan.schema_version}
