"""Typed settings for ingest/API (Developer A) and the research engine (Developer B)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ingest, API, and worker settings. Environment values use the HARNESS_ prefix."""

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
    mcp_dataset_name: str = "mcp-financial-data"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # A question occupies a worker for 10-20s; past this many at once the API says "busy" instead of
    # queueing every browser tab behind the same threadpool.
    api_max_concurrent_queries: int = 4

    @property
    def allowed_cors_origins(self) -> tuple[str, ...]:
        """Return the comma-separated browser origins configured for the API."""
        return tuple(origin.strip() for origin in self.cors_origins.split(",") if origin.strip())


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


class EngineSettings(BaseSettings):
    """Research engine settings, read from the environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Unset = the ingest database (HARNESS_DATABASE_URL); set it to the read-only htn_engine role in production.
    database_url_engine: Optional[str] = None

    openai_api_key: Optional[SecretStr] = None
    htn_model: Optional[str] = None
    htn_embedding_model: Optional[str] = None

    @field_validator("database_url_engine", "htn_model", "htn_embedding_model", "openai_api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, v):
        # An empty line in .env (e.g. `HTN_EMBEDDING_MODEL=`) means "not set", so fallbacks still apply.
        return None if isinstance(v, str) and not v.strip() else v

    htn_max_tool_rounds: int = 6
    htn_max_tokens: int = 150_000   # cumulative across turns (each turn resends the context)
    htn_max_cost_usd: float = 0.50
    htn_max_latency_ms: int = 60_000
    htn_prompt_version: str = "answer_v1"
    htn_llm_timeout_s: float = 60.0

    # JSON in the environment, e.g. HTN_PRICES='{"my-model": {"input_per_mtok": 1.0, "output_per_mtok": 4.0}}'
    htn_prices: dict[str, ModelPrice] = {}

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.htn_prices.get(model)
        if price is None:
            return 0.0
        return (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000


settings = Settings()


@lru_cache
def engine_settings() -> EngineSettings:
    eng = EngineSettings()
    updates: dict[str, object] = {}
    if eng.database_url_engine is None:
        updates["database_url_engine"] = settings.database_url
    if eng.htn_model is None and settings.openai_model:
        updates["htn_model"] = settings.openai_model
    if eng.openai_api_key is None and settings.openai_api_key is not None:
        updates["openai_api_key"] = settings.openai_api_key
    if eng.htn_embedding_model is None and settings.openai_embedding_model:
        updates["htn_embedding_model"] = settings.openai_embedding_model
    return eng.model_copy(update=updates) if updates else eng
