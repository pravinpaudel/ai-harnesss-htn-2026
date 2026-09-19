"""Query embedders for semantic search. Chunk embeddings are written at ingest (Developer A);
the engine only embeds the query, with the same model, so vectors are comparable."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Optional, Protocol


class QueryEmbedder(Protocol):
    model: str
    dim: int

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIQueryEmbedder:
    def __init__(self, model: str, api_key: Optional[str] = None, dim: int = 1536, **client_kwargs: Any):
        from openai import OpenAI

        client_kwargs.setdefault("timeout", 20.0)
        client_kwargs.setdefault("max_retries", 1)
        self.model, self.dim = model, dim
        self._client = OpenAI(api_key=api_key, **client_kwargs)
        self._cache: dict[str, list[float]] = {}

    def embed_query(self, text: str) -> list[float]:
        if text not in self._cache:
            resp = self._client.embeddings.create(model=self.model, input=text, dimensions=self.dim)
            self._cache[text] = list(resp.data[0].embedding)
        return self._cache[text]


class HashingEmbedder:
    """Deterministic bag-of-words embedder for tests: similar wording -> similar vectors."""

    model = "hashing-test"

    def __init__(self, dim: int = 1536):
        self.dim = dim

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if len(word) < 3:
                continue
            h = int(hashlib.md5(word[:6].encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
