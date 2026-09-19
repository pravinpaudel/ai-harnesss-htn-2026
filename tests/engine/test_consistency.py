"""Rounded restatements are not contradictions; invented currencies are removed."""

from contracts.models import AnswerStatus, FactFilter
from app.reasoning.consistency import agree, comparable_groups
from app.reasoning.context import RunContext
from app.tools.registry import FindFactsArgs, find_facts
from tests.engine.conftest import pick, submit


def _ivn_q2_revenue(repo):
    v = repo.resolve_version()
    ivn = repo.resolve_entity(v, "IVN")[0]
    return repo.find_facts(FactFilter(dataset_version_id=v, entity_ids=[ivn.entity_id], metric_text="revenue",
                                      period_labels=["Q2 2026"], roles=["actual"], units=["money"]))


def test_rounded_restatement_agrees(memory_repo):
    facts = _ivn_q2_revenue(memory_repo)
    assert {f.value.original_value for f in facts} == {"$153M", "$152.6M"}
    groups = comparable_groups(facts)
    assert len(groups) == 1 and agree(*groups[0])


def test_real_difference_does_not_agree(memory_repo):
    f153, f1526 = sorted(_ivn_q2_revenue(memory_repo), key=lambda f: f.value.value)[::-1]
    wrong = f1526.model_copy(update={"value": f1526.value.model_copy(update={"value": 151.0e6,
                                                                             "original_value": "$151.0M"})})
    assert not agree(f153, wrong)


def test_find_facts_tells_the_model_which_value_to_use(memory_repo):
    info = memory_repo.version_info()
    ctx = RunContext(memory_repo, info.dataset_id, info.dataset_version_id, info.source_hash, "x", "q")
    out = find_facts(ctx, FindFactsArgs(entity="Ivanhoe", metric="revenue", period="Q2 2026", role="actual"))
    note = out["same_quantity_notes"][0]
    assert note["agree_within_rounding"] is True
    assert ctx.evidence[note["use"]].fact.value.original_value == "$152.6M"


def test_gate_removes_currency_not_in_source(run_script):
    ans, _, _ = run_script("What was Ivanhoe's revenue in Q2 2026?", [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))],
                values=[{"label": "revenue", "value": 152600000, "value_text": None, "unit": "money",
                         "currency": "CAD", "period_label": "Q2 2026", "citation_ns": [1]}])],
    ])
    assert ans.status == AnswerStatus.answered
    assert ans.values[0].currency is None
    assert any("Currency CAD removed" in lim for lim in ans.limitations)
