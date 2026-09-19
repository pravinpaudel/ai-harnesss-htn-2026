"""Builds the research engine from settings. Shared by the CLI and the HTTP API."""

from __future__ import annotations

from app.settings import EngineSettings, engine_settings


class EngineConfigError(RuntimeError):
    """A required setting (model, API key) is missing."""


def build_repo(settings: EngineSettings | None = None):
    from app.retrieval.repository import PgEvidenceRepository

    return PgEvidenceRepository((settings or engine_settings()).database_url_engine)


def build_engine(audit: bool = True, settings: EngineSettings | None = None, repo=None):
    from app.audit.recorder import MemoryRecorder, PgRecorder
    from app.llm.client import OpenAIResponsesClient
    from app.reasoning.engine import ResearchEngine

    s = settings or engine_settings()
    if not s.htn_model:
        raise EngineConfigError("HARNESS_OPENAI_MODEL (or HTN_MODEL) is not set")
    if not s.openai_api_key:
        raise EngineConfigError("OPENAI_API_KEY is not set")
    repo = repo or build_repo(s)
    key = s.openai_api_key.get_secret_value()
    llm = OpenAIResponsesClient(s.htn_model, api_key=key, timeout=s.htn_llm_timeout_s)
    embedder = None
    if s.htn_embedding_model:
        from app.llm.embeddings import OpenAIQueryEmbedder
        embedder = OpenAIQueryEmbedder(s.htn_embedding_model, api_key=key)
    recorder = PgRecorder(repo.engine) if audit else MemoryRecorder()
    return ResearchEngine(repo, llm, s, recorder, embedder=embedder)
