"""Query API routes, with the engine and repository swapped for the fixture and a scripted model."""

import pytest
from fastapi.testclient import TestClient

from contracts.models import AnswerResponse
from app.api import queries
from app.api.main import app
from app.audit.recorder import MemoryRecorder
from app.llm.client import ScriptedLLM
from app.reasoning.engine import ResearchEngine
from tests.engine.conftest import pick, submit


@pytest.fixture
def client(memory_repo, settings):
    script = [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))])],
    ]
    app.dependency_overrides[queries.get_repo] = lambda: memory_repo
    app.dependency_overrides[queries.get_engine] = lambda: ResearchEngine(memory_repo, ScriptedLLM(script), settings,
                                                                          MemoryRecorder())
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_query_returns_contract_answer(client):
    r = client.post("/v1/queries", json={"question": "What was Ivanhoe's revenue in Q2 2026?"})
    assert r.status_code == 200
    ans = AnswerResponse.model_validate(r.json())
    assert ans.status.value == "answered" and ans.citations[0].span.exact_text == "$152.6M"


def test_budget_from_request_tightens_limits(client):
    r = client.post("/v1/queries", json={"question": "anything", "budget": {"max_tool_rounds": 1, "max_tokens": 500,
                                                                             "max_cost_usd": 0.1,
                                                                             "max_latency_ms": 60000}})
    assert r.status_code == 200 and r.json()["provenance"]["usage"]["tool_rounds"] <= 1


def test_conflicts_and_dataset_profile(client):
    r = client.get("/v1/conflicts", params={"rule": "count_claim"})
    assert r.status_code == 200 and r.json()[0]["observed"] == "3/8"
    assert client.get("/v1/conflicts", params={"rule": "nope"}).status_code == 422
    prof = client.get("/v1/datasets/fixture").json()
    assert len(prof["documents"]) == 2
    assert client.get("/v1/datasets/fixture", params={"version": "00000000-0000-0000-0000-000000000000"}).status_code == 404
