from fastapi import FastAPI

from retirement_core.api.routers import health, plans, runs
from retirement_core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Retirement calculation, optimization, and reporting API",
    )
    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(plans.router, prefix=settings.api_prefix)
    app.include_router(runs.router, prefix=settings.api_prefix)
    return app
