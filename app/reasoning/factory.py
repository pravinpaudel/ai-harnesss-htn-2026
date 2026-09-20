"""Builds the research engine from settings. Shared by the CLI and the HTTP API.

Connection pools and API clients are process-wide, not per call: an HTTP server answering several
questions must not open a database pool and an OpenAI client for each one.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.settings import EngineSettings, engine_settings


class EngineConfigError(RuntimeError):
    """A required setting (model, API key) is missing."""


@lru_cache(maxsize=8)
def _repo_for(url: str):
    from app.retrieval.repository import PgEvidenceRepository

    return PgEvidenceRepository(url)


@lru_cache(maxsize=8)
def _llm_for(model: str, key: str, timeout: float):
    from app.llm.client import OpenAIResponsesClient

    return OpenAIResponsesClient(model, api_key=key, timeout=timeout)


@lru_cache(maxsize=8)
def _embedder_for(model: str, key: str):
    from app.llm.embeddings import OpenAIQueryEmbedder

    return OpenAIQueryEmbedder(model, api_key=key)


def build_repo(settings: EngineSettings | None = None):
    """The shared read-only repository for a database URL (one SQLAlchemy pool per URL)."""
    return _repo_for((settings or engine_settings()).database_url_engine)


def build_engine(audit: bool = True, settings: EngineSettings | None = None, repo=None, recorder=None):
    """A research engine over shared clients. `recorder` overrides the default audit recorder."""
    from app.audit.recorder import MemoryRecorder, PgRecorder
    from app.reasoning.engine import ResearchEngine

    s = settings or engine_settings()
    if not s.htn_model:
        raise EngineConfigError("HARNESS_OPENAI_MODEL (or HTN_MODEL) is not set")
    if not s.openai_api_key:
        raise EngineConfigError("OPENAI_API_KEY is not set")
    repo = repo or build_repo(s)
    key = s.openai_api_key.get_secret_value()
    llm = _llm_for(s.htn_model, key, s.htn_llm_timeout_s)
    embedder = _embedder_for(s.htn_embedding_model, key) if s.htn_embedding_model else None
    if recorder is None:
        recorder = PgRecorder(repo.engine) if audit else MemoryRecorder()
    return ResearchEngine(repo, llm, s, recorder, embedder=embedder)


@lru_cache(maxsize=1)
def shared_engine(audit: bool = True):
    """One engine for the whole process, for callers that answer many questions (the HTTP API)."""
    return build_engine(audit=audit)
