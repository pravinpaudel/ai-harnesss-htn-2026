"""Typed settings. Developer B's section is EngineSettings; Developer A adds its own section here."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


class EngineSettings(BaseSettings):
    """Research engine settings, read from the environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Read-only engine role: SELECT on evidence, INSERT/UPDATE on answer_run/tool_event/research_memory.
    database_url_engine: str = "postgresql+psycopg://htn_engine:htn_engine@localhost:5432/htn"

    openai_api_key: Optional[SecretStr] = None
    htn_model: Optional[str] = None                # chat/tool model; decided by the team
    htn_embedding_model: Optional[str] = None      # used after Checkpoint 1

    htn_max_tool_rounds: int = 6
    htn_max_tokens: int = 60_000
    htn_max_cost_usd: float = 0.50
    htn_max_latency_ms: int = 60_000
    htn_prompt_version: str = "answer_v1"

    # JSON in the environment, e.g. HTN_PRICES='{"my-model": {"input_per_mtok": 1.0, "output_per_mtok": 4.0}}'
    htn_prices: dict[str, ModelPrice] = {}

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.htn_prices.get(model)
        if price is None:
            return 0.0
        return (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000


@lru_cache
def engine_settings() -> EngineSettings:
    return EngineSettings()
