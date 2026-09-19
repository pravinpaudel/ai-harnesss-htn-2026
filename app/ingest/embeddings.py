"""Embedding boundary; failures never block raw evidence persistence."""
from __future__ import annotations

from typing import Any, Optional, Protocol


class EmbeddingProvider(Protocol):
    model: str
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class NoopEmbeddingProvider:
    """Safe default for local ingest before an embedding provider is configured."""
    model = "unconfigured"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []


class OpenAIEmbeddingProvider:
    """Chunk embeddings via the OpenAI embeddings API.

    `dim` must match `chunk.embedding vector(1536)` in contracts/schema.sql, and the research
    engine must embed queries with the same model (HARNESS_OPENAI_EMBEDDING_MODEL) so vectors compare.
    """

    max_chars = 20_000          # well under the model's per-input token limit

    def __init__(self, model: str, api_key: Optional[str] = None, dim: int = 1536, **client_kwargs: Any):
        from openai import OpenAI

        client_kwargs.setdefault("timeout", 60.0)
        client_kwargs.setdefault("max_retries", 2)
        self.model, self.dim = model, dim
        self._client = OpenAI(api_key=api_key, **client_kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(model=self.model, dimensions=self.dim,
                                              input=[t[: self.max_chars] or " " for t in texts])
        return [list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]


def default_embedding_provider() -> EmbeddingProvider:
    """OpenAI embeddings when a key and model are configured, otherwise the no-op provider."""
    from app.settings import settings

    if settings.openai_api_key and settings.openai_embedding_model:
        return OpenAIEmbeddingProvider(settings.openai_embedding_model,
                                       api_key=settings.openai_api_key.get_secret_value())
    return NoopEmbeddingProvider()
