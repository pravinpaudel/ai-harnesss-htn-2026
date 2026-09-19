from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Environment values use the HARNESS_ prefix."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="HARNESS_", extra="ignore")

    database_url: str = "postgresql+psycopg://harness:harness@localhost:5432/harness"
    raw_storage_path: Path = Path(".data/raw")
    parser_version: str = "0.1.0"
    job_max_attempts: int = Field(default=3, ge=1, le=10)


settings = Settings()
