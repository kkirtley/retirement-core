from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "retirement-core"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://retirement:retirement@localhost:5432/retirement"
    rules_path: Path = Field(default=Path("./data/rules"))
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
