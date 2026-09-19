"""Embedding boundary; failures never block raw evidence persistence."""
from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    model: str
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class NoopEmbeddingProvider:
    """Safe default for local ingest before an embedding provider is configured."""
    model = "unconfigured"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return []
