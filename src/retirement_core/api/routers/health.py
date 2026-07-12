from fastapi import APIRouter

from retirement_core import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "engine_version": __version__}
