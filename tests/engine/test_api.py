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


# --------------------------------------------------------------- streaming and load --

def _frames(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs, ignoring heartbeat comments."""
    import json as _json

    out = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith(":")]
        if not lines:
            continue
        event = next((ln.removeprefix("event: ") for ln in lines if ln.startswith("event: ")), "message")
        data = next((ln.removeprefix("data: ") for ln in lines if ln.startswith("data: ")), "null")
        out.append((event, _json.loads(data)))
    return out


def test_stream_sends_steps_then_the_answer(client):
    r = client.post("/v1/queries/stream", json={"question": "What was Ivanhoe's revenue in Q2 2026?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    frames = _frames(r.text)
    kinds = [event for event, _ in frames]
    assert kinds[0] == "run" and kinds[-1] == "done"
    assert "step" in kinds and kinds[-2] == "answer"

    run = frames[0][1]
    assert run["run_id"] and run["dataset_version"]
    steps = [data for event, data in frames if event == "step"]
    assert [s["seq"] for s in steps] == list(range(1, len(steps) + 1))
    assert {"tool_call", "llm"} <= {s["kind"] for s in steps}
    assert any(s["detail"] for s in steps)                      # steps carry a human-readable line

    answer = dict(frames[-2][1])
    assert str(AnswerResponse.model_validate(answer).run_id) == run["run_id"]
    assert answer["citations"][0]["span"]["exact_text"] == "$152.6M"


def test_stream_answer_matches_the_plain_route(client):
    plain = client.post("/v1/queries", json={"question": "What was Ivanhoe's revenue in Q2 2026?"}).json()
    streamed = dict(_frames(client.post("/v1/queries/stream",
                                        json={"question": "What was Ivanhoe's revenue in Q2 2026?"}).text)[-2][1])
    for field in ("status", "answer", "evidence_status"):
        assert streamed[field] == plain[field]


def test_a_heartbeat_does_not_swallow_a_step(monkeypatch):
    """A quiet gap sends a keep-alive and the next step still arrives."""
    import asyncio
    import queue as _queue

    from app.api import streaming

    monkeypatch.setattr(streaming, "HEARTBEAT_S", 0.05)
    sink: _queue.Queue = _queue.Queue()

    class SlowEngine:
        def ask(self, *a, **kw):
            import time

            time.sleep(0.2)                                     # quiet: forces a heartbeat
            sink.put(("step", {"seq": 1, "kind": "tool_call", "name": "find_facts", "detail": None,
                               "latency_ms": 1}))
            time.sleep(0.05)
            return _FakeAnswer()

    async def collect():
        return [chunk async for chunk in streaming.answer_stream(
            SlowEngine(), sink, "q", dataset="latest", version="latest", session_id=None, budget=None)]

    chunks = asyncio.run(collect())
    assert any(c.startswith(": keep-alive") for c in chunks)
    assert any(c.startswith("event: step") for c in chunks)
    assert chunks[-1].startswith("event: done")


class _FakeAnswer:
    """Only what the stream needs from an answer: an id and a JSON body."""

    run_id = "00000000-0000-0000-0000-000000000001"

    def model_dump_json(self, **kwargs) -> str:
        return '{"run_id": "00000000-0000-0000-0000-000000000001", "status": "answered"}' 


def test_too_many_concurrent_questions_get_503(client, monkeypatch):
    from app.api import queries as q

    monkeypatch.setattr(q, "_slots", __import__("threading").BoundedSemaphore(1))
    q._slots.acquire()                                          # the only slot is taken
    r = client.post("/v1/queries", json={"question": "anything"})
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    q._slots.release()
    assert client.post("/v1/queries", json={"question": "anything"}).status_code == 200


def test_api_shares_one_pool_and_one_model_client():
    from app.reasoning.factory import build_repo

    assert build_repo() is build_repo()


# ------------------------------------------------------------ datasets and history --

def test_list_datasets_for_a_picker(client):
    r = client.get("/v1/datasets")
    assert r.status_code == 200
    first = r.json()[0]
    assert first["name"] == "fixture" and first["status"] == "ready"
    assert first["documents"] == 2 and first["facts"] > 0
    assert first["ready_at"] and first["parser_version"]          # freshness line has what it needs


def test_list_runs_defaults_and_filters(client):
    assert client.get("/v1/runs").status_code == 200
    assert client.get("/v1/runs", params={"session_id": "abc", "limit": 5}).json() == []
    assert client.get("/v1/runs", params={"limit": 0}).status_code == 422
    assert client.get("/v1/conversations").json() == []
    assert client.get("/v1/conversations", params={"limit": 0}).status_code == 422
