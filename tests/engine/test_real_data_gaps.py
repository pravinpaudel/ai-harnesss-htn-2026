"""Robustness on datasets with thinner extraction (found in the Checkpoint 1 run on Developer A's ingest)."""

from dataclasses import replace

from contracts.models import AnswerStatus
from app.reasoning.context import RunContext
from app.tools.registry import FindFactsArgs, find_facts
from tests.engine.conftest import pick, submit
from tests.engine.fixture_data import load
from tests.engine.memory_repo import MemoryEvidenceRepository


def _no_period_labels() -> MemoryEvidenceRepository:
    data = load("noperiods")
    data.facts = [replace(f, period_label=None, period_end=None, period_type="unspecified") for f in data.facts]
    return MemoryEvidenceRepository(data)


def test_period_filter_falls_back_to_source_row():
    repo = _no_period_labels()
    info = repo.version_info()
    ctx = RunContext(repo, info.dataset_id, info.dataset_version_id, info.source_hash, "x", "q")
    out = find_facts(ctx, FindFactsArgs(entity="SHOP", metric="Revenue", period="Q2 2026", role="actual"))
    originals = {r["original"] for r in out["facts"]}
    assert "$3.58B" in originals and "$3.17B" not in originals      # Q2 2026 row only, not Q1 2026
    assert "source row" in out["note"]
    assert all("row" in r for r in out["facts"])                    # row context shown when labels are missing


def test_ranking_across_currencies_is_downgraded(run_script):
    data = load("nofindings")
    data.findings = []            # like a dataset whose validators have not run yet
    ans, _, _ = run_script("Rank the six mining companies by market capitalization.", [
        [("find_facts", {"entity": "FM", "metric": "Mkt Cap", "role": "actual"}),
         ("find_facts", {"entity": "TECK.B", "metric": "Mkt Cap", "role": "actual"})],
        [submit("answered", "TECK.B is $32B [1] and FM is ~C$30B [2], so TECK.B ranks higher.",
                [(1, pick("find_facts", 1, source="mining-excerpt.md:20")),
                 (2, pick("find_facts", 0, source="mining-excerpt.md:21"))])],
    ], repo=MemoryEvidenceRepository(data))
    assert ans.status == AnswerStatus.partial
    assert any("different currencies (CAD, USD)" in lim for lim in ans.limitations)
