from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Environment values use the HARNESS_ prefix."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="HARNESS_", extra="ignore")

    database_url: str = "postgresql+psycopg://harness:harness@localhost:5432/harness"
    raw_storage_path: Path = Path(".data/raw")
    parser_version: str = "0.1.0"
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = "gpt-5.6-luna"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_max_output_tokens: int = Field(default=1200, ge=1, le=16_000)
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    mcp_url: str | None = None
    mcp_financial_data_tool: str = "financialDataRetrieval"


settings = Settings()
