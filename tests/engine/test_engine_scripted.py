"""End-to-end engine runs over the fixture with a scripted model (no network, no database).

Each test drives the real router, tools, policy gate and verifier; only the model's choices
are scripted. The four Checkpoint 1 question types are covered, plus policy edge cases.
"""

from pathlib import Path

import jsonschema
import json

from contracts.models import AnswerResponse, AnswerStatus, DeclineReason, EvidenceStatus
from app.eval.runner import load_cases, score
from tests.engine.conftest import pick, submit
from tests.engine.memory_repo import MemoryEvidenceRepository

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts/json-schema/AnswerResponse.json").read_text())
CASES = {c.case_id: c for c in load_cases(ROOT / "contracts/fixture/questions.json")}


def check_contract(ans: AnswerResponse) -> None:
    jsonschema.validate(json.loads(ans.model_dump_json()), SCHEMA)
    AnswerResponse.model_validate_json(ans.model_dump_json())


# ------------------------------------------------------------ CP1: lookup --

def test_fx01_lookup(run_script):
    ans, llm, rec = run_script(CASES["FX01"].question, [
        [("find_facts", {"entity": "Ivanhoe", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Ivanhoe's revenue in Q2 2026 was $152.6M [1].",
                [(1, pick("find_facts", source="mining-excerpt.md:95"))],
                values=[{"label": "revenue", "value": 152600000, "value_text": None, "unit": "money",
                         "currency": None, "period_label": "Q2 2026", "citation_ns": [1]}])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.answered and ans.evidence_status == EvidenceStatus.fully_supported
    assert ans.citations[0].span.exact_text == "$152.6M" and ans.citations[0].span.line_start == 95
    assert score(CASES["FX01"], ans).passed
    assert "IVN" in json.dumps(llm.user_input)  # router seeded the entity match


# ------------------------------------------------------- CP1: calculation --

def test_fx04_calculation_cites_operands(run_script):
    ans, _, _ = run_script(CASES["FX04"].question, [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "role": "actual"})],
        [("calculate", {"operation": "percent_change", "rounding": 2, "allow_mixed_currency": False,
                        "operand_handles": [pick("find_facts", source="mining-excerpt.md:90"),
                                            pick("find_facts", source="mining-excerpt.md:95")]})],
        [submit("answered", "Revenue fell 7.79% from Q1 2026 to Q2 2026.", citations=[],
                calcs=[lambda seen: seen["calculate"][0]["handle"]],
                values=[{"label": "percent change", "value": -7.79, "value_text": None, "unit": "pct",
                         "currency": None, "period_label": None, "citation_ns": []}])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.answered
    calc = ans.calculation_trace[0]
    assert calc.result == -7.79
    cited_lines = {c.span.line_start for c in ans.citations}
    assert {90, 95} <= cited_lines                     # operands were auto-cited by the policy gate
    assert score(CASES["FX04"], ans).passed


# ---------------------------------------------------------- CP1: conflict --

def test_fx06_conflict_is_escalated_from_findings(run_script):
    ans, _, _ = run_script(CASES["FX06"].question, [
        [("find_facts", {"entity": "IVN", "metric": "EPS Beat Rate", "role": "count"})],
        # The model claims a plain answer; the gate must escalate because the fact has an open finding.
        [submit("answered", "Ivanhoe beat estimates in 3 of 8 quarters [1].",
                [(1, pick("find_facts", source="mining-excerpt.md:132"))])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.conflict and ans.evidence_status == EvidenceStatus.conflicting
    assert ans.conflicts[0].rule.value == "count_claim"
    assert len(ans.conflicts[0].claims) == 2
    lines = {c.span.line_start for c in ans.citations}
    assert {132, 70, 80} <= lines


def test_fx06_model_surfaced_conflict_passes_eval(run_script):
    ans, _, _ = run_script(CASES["FX06"].question, [
        [("find_facts", {"entity": "IVN", "metric": "EPS Beat Rate", "role": "count"}),
         ("find_facts", {"entity": "IVN", "metric": "beat", "role": "attribute"})],
        [("list_validation_findings", {"entity": "IVN", "handles": None})],
        [submit("conflict", "The screening table says 3 of 8 beats [1], but the quarterly summaries show 2 beats "
                            "[2][3].",
                [(1, pick("find_facts", 0, source="mining-excerpt.md:132")),
                 (2, pick("find_facts", 1, source="mining-excerpt.md:70")),
                 (3, pick("find_facts", 1, source="mining-excerpt.md:80"))],
                conflicts=[lambda seen: seen["list_validation_findings"][0]["findings"][0]["handle"]])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.conflict
    assert score(CASES["FX06"], ans).passed


# ------------------------------------------------------- CP1: unsupported --

def test_fx09_unsupported_metric_declines(run_script):
    ans, _, _ = run_script(CASES["FX09"].question, [
        [("check_coverage", {"entity": "Ivanhoe", "metric": "AISC", "period": "Q2 2026"})],
        [submit("declined", "The dataset has no all-in sustaining cost for Ivanhoe.", [],
                decline_reason="insufficient_evidence")],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.declined and ans.decline_reason == DeclineReason.insufficient_evidence
    assert ans.evidence_status == EvidenceStatus.unsupported
    assert score(CASES["FX09"], ans).passed


def test_fx10_false_premise_cites_correction(run_script):
    ans, _, _ = run_script(CASES["FX10"].question, [
        [("find_facts", {"entity": "Shopify", "metric": "EPS", "period": "Q2 2026"})],
        [submit("declined", "The premise is wrong: Shopify reported $0.42 [1] against a $0.39 estimate [2].",
                [(1, pick("find_facts", source="technology-excerpt.md:45", role="actual")),
                 (2, pick("find_facts", source="technology-excerpt.md:45", role="estimate"))],
                decline_reason="false_premise")],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.declined and ans.decline_reason == DeclineReason.false_premise
    assert len(ans.citations) == 2
    assert score(CASES["FX10"], ans).passed


# ------------------------------------------------------------ policy gate --

def test_invented_handle_is_dropped_and_answer_declines(run_script):
    ans, _, _ = run_script("What was Ivanhoe's revenue in Q2 2026?", [
        [submit("answered", "It was $152.6M [1].", [(1, "E999")])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.declined and not ans.citations


def test_unsupported_number_downgrades_to_partial(run_script):
    ans, _, _ = run_script("What was Ivanhoe's revenue in Q2 2026?", [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1], up from $140.0M.",
                [(1, pick("find_facts", source="mining-excerpt.md:95"))])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.partial
    assert any("140.0M" in lim for lim in ans.limitations)


def test_tampered_snapshot_fails_verification(memory_repo, run_script):
    doc = memory_repo.data.docs["mining-excerpt.md"]
    tampered = MemoryEvidenceRepository(memory_repo.data,
                                        tamper={doc.name: doc.text.replace("$152.6M", "$999.9M")})
    ans, _, _ = run_script("What was Ivanhoe's revenue in Q2 2026?", [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))])],
    ], repo=tampered)
    assert ans.status == AnswerStatus.declined and not ans.citations


def test_mixed_currency_calculation_is_rejected(run_script):
    ans, _, _ = run_script("Is First Quantum bigger than Teck by market cap?", [
        [("find_facts", {"entity": "FM", "metric": "Mkt Cap", "role": "actual"}),
         ("find_facts", {"entity": "TECK.B", "metric": "Mkt Cap", "role": "actual"})],
        [("compare_values", {"operand_handles": [pick("find_facts", 0, source="mining-excerpt.md:21"),
                                                 pick("find_facts", 1, source="mining-excerpt.md:20")]})],
        [submit("declined", "The two market caps are in different currencies.", [],
                calcs=[lambda seen: seen["compare_values"][0]["handle"]], decline_reason="incompatible_currency")],
    ])
    check_contract(ans)
    assert ans.calculation_trace[0].rejected
    assert ans.decline_reason == DeclineReason.incompatible_currency


# ------------------------------------------------------------ loop limits --

def test_round_cap_forces_submit_then_stops(run_script):
    loop = [[("inspect_dataset", {})]] * 10
    ans, llm, rec = run_script("anything", loop, htn_max_tool_rounds=3)
    assert llm.forced[-1] == "submit_answer"
    assert ans.status == AnswerStatus.declined and ans.decline_reason == DeclineReason.budget_exhausted
    assert ans.provenance.usage.tool_rounds == 3


def test_token_budget_exhaustion(run_script):
    ans, _, _ = run_script("anything", [[("inspect_dataset", {})]] * 5, htn_max_tokens=1500)
    assert ans.status == AnswerStatus.declined and "token budget" in ans.limitations[0]


def test_audit_trail_records_every_step(run_script):
    ans, _, rec = run_script(CASES["FX01"].question, [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))])],
    ])
    kinds = [e["kind"] for e in rec.events[ans.run_id]]
    assert kinds[0] == "policy" and "tool_call" in kinds and kinds[-1] == "verify"
    assert rec.runs[ans.run_id]["response"] is ans


def test_calculation_handle_cited_as_source_is_folded(run_script):
    ans, _, _ = run_script(CASES["FX04"].question, [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "role": "actual"})],
        [("calculate", {"operation": "percent_change", "rounding": 2, "allow_mixed_currency": False,
                        "operand_handles": [pick("find_facts", source="mining-excerpt.md:90"),
                                            pick("find_facts", source="mining-excerpt.md:95")]})],
        [submit("answered", "From Q1 2026 to Q2 2026, revenue fell 7.79% [1].",
                [(1, lambda seen: seen["calculate"][0]["handle"])])],
    ])
    check_contract(ans)
    assert ans.status == AnswerStatus.answered, ans.limitations
    assert not ans.limitations
    assert ans.calculation_trace and {90, 95} <= {c.span.line_start for c in ans.citations}


def test_budget_reached_forces_a_final_answer_instead_of_discarding_work(run_script):
    ans, llm, rec = run_script(CASES["FX01"].question, [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))])],
    ], htn_max_tokens=1000)
    assert llm.forced == ["submit_answer"]
    assert ans.status == AnswerStatus.answered and ans.citations
    assert any(e["name"] == "budget" for e in rec.events[ans.run_id])


def test_invalid_submit_gets_one_retry(run_script):
    good = submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))])
    broken = ("submit_answer", {k: v for k, v in good[1].items() if k != "values"})
    ans, llm, rec = run_script(CASES["FX01"].question, [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [broken],
        [good],
    ])
    assert ans.status == AnswerStatus.answered
    assert llm.forced[-1] == "submit_answer"
    assert any(e["name"] == "invalid_submit" for e in rec.events[ans.run_id])
