"""All 10 fixture cases through the real OpenAI Responses API (needs OPENAI_API_KEY and HTN_MODEL).

    uv run pytest -m live tests/engine/test_live.py -s
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.eval.runner import load_cases, run
from app.llm.client import OpenAIResponsesClient
from app.reasoning.engine import ResearchEngine
from app.settings import EngineSettings

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[2]
CP1 = {"FX01", "FX04", "FX06", "FX09"}


@pytest.fixture(scope="module")
def live_engine(memory_repo):
    s = EngineSettings()
    if not (s.openai_api_key and s.htn_model):
        pytest.skip("set OPENAI_API_KEY and HTN_MODEL to run live tests")
    llm = OpenAIResponsesClient(s.htn_model, api_key=s.openai_api_key.get_secret_value())
    return ResearchEngine(memory_repo, llm, s)


def test_fixture_suite_live(live_engine):
    cases = load_cases(ROOT / "contracts/fixture/questions.json")
    report = run(cases, live_engine.ask, suite="fixture-live",
                 on_result=lambda r, a: print(f"{'PASS' if r.passed else 'FAIL'} {r.case_id} {a.status.value} "
                                              f"{r.detail or ''}"))
    assert report.citation_validity == 1.0
    failed_cp1 = [c.case_id for c in report.cases if c.case_id in CP1 and not c.passed]
    assert not failed_cp1, f"Checkpoint 1 cases failed: {failed_cp1}"
