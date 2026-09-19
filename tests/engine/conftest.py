from __future__ import annotations

import os
from typing import Any, Callable

import pytest

from app.audit.recorder import MemoryRecorder
from app.llm.client import ScriptedLLM
from app.reasoning.engine import ResearchEngine
from app.settings import EngineSettings
from tests.engine.memory_repo import MemoryEvidenceRepository


@pytest.fixture(scope="session")
def memory_repo() -> MemoryEvidenceRepository:
    return MemoryEvidenceRepository()


@pytest.fixture
def settings() -> EngineSettings:
    return EngineSettings(_env_file=None, database_url_engine="postgresql+psycopg://unused/unused",
                          htn_model="scripted", openai_api_key=None, htn_max_tool_rounds=6)


@pytest.fixture
def run_script(memory_repo, settings) -> Callable[..., Any]:
    """run_script(question, turns) -> (AnswerResponse, ScriptedLLM, MemoryRecorder)."""

    def _run(question: str, turns: list, repo=None, **overrides):
        s = settings.model_copy(update=overrides)
        llm = ScriptedLLM(turns)
        rec = MemoryRecorder()
        eng = ResearchEngine(repo or memory_repo, llm, s, rec)
        return eng.ask(question), llm, rec

    return _run


# ---------------------------------------------------------------- helpers --

def pick(tool: str, call: int = 0, **match: Any) -> Callable[[dict], str]:
    """Lambda for ScriptedLLM: handle of the first row in the `call`-th output of `tool` matching `match`."""

    def _get(seen: dict) -> str:
        out = seen[tool][call]
        rows = out.get("facts") or out.get("hits") or out.get("findings") or [out]
        for r in rows:
            if all(str(r.get(k)) == str(v) or (k == "source" and str(r.get(k, "")).startswith(str(v)))
                   for k, v in match.items()):
                return r["handle"]
        raise AssertionError(f"no {tool} row matching {match}: {rows}")

    return _get


def submit(status: str, answer: str, citations: list, values: list = (), calcs: list = (), conflicts: list = (),
           limitations: list = (), decline_reason: str | None = None) -> tuple[str, dict]:
    return ("submit_answer", {"status": status, "answer": answer,
                              "citations": [{"n": n, "handle": h} for n, h in citations],
                              "values": list(values), "calculation_handles": list(calcs),
                              "conflict_finding_handles": list(conflicts), "limitations": list(limitations),
                              "decline_reason": decline_reason})


def db_url() -> str:
    return os.environ.get("HTN_TEST_ADMIN_URL", "postgresql+psycopg://postgres:postgres@localhost:55432/htn_test")
