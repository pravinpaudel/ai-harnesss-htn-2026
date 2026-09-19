"""Identification support: derived span context and multi-clue candidate ranking."""

from dataclasses import replace

from app.reasoning.context import RunContext
from app.reasoning.router import route
from app.retrieval.context import Outline, entities_mentioned, entity_from_path, period_of
from app.tools.registry import FindCandidatesArgs, find_candidates
from tests.engine.fixture_data import load
from tests.engine.memory_repo import MemoryEvidenceRepository

DOC = """# Report
## 6. Company Performance
### IVN — Ivanhoe Mines Ltd
<details>
<summary>Q2 2026 — Revenue $152.6M; EPS $0.03</summary>

**1. Headline Results**
Revenue missed on logistics.
</details>
### SHOP — Shopify Inc. — Quarterly Performance {#shop-quarterly}
Text.
"""


def test_outline_paths_blocks_and_entities():
    o = Outline.parse(DOC)
    assert o.path(8) == ["Report", "6. Company Performance", "IVN — Ivanhoe Mines Ltd"]
    assert o.block(8).startswith("Q2 2026 — Revenue")
    assert period_of(o.block(8)) == "Q2 2026"
    assert o.block(11) is None and o.path(11)[-1] == "SHOP — Shopify Inc. — Quarterly Performance"
    assert entity_from_path(o.path(11), {"IVN", "SHOP"}) == "SHOP"
    assert entities_mentioned("| Aug 25 | BMO | quarterly note |", {"BMO", "RY", "NA"}) == ["BMO"]


def _ctx(repo, question="q"):
    i = repo.version_info()
    return RunContext(repo, i.dataset_id, i.dataset_version_id, i.source_hash, i.parser_version, question)


def test_find_candidates_ranks_the_entity_matching_all_clues():
    ctx = _ctx(MemoryEvidenceRepository())
    out = find_candidates(ctx, FindCandidatesArgs(clues=["lower KK payable sales", "DRC logistical constraints",
                                                         "Kipushi record zinc"]))
    top = out["candidates"][0]
    assert top["entity"] == "IVN" and top["likely_period"] == "Q2 2026"
    assert all(ctx.evidence[e["handle"]].span.document_name == "mining-excerpt.md" for e in top["evidence"])


def test_context_is_derived_when_store_has_no_heading_path():
    data = load("nopaths")
    data.chunks = [replace(c, span=c.span) for c in data.chunks]
    repo = MemoryEvidenceRepository(data)
    ctx = _ctx(repo)
    hit = repo.search_lexical(ctx.dataset_version_id, "logistical constraints")[0]
    blank = hit.span.model_copy(update={"heading_path": []})
    sc = ctx.span_context(blank)
    assert sc.entity == "IVN" and period_of(sc.block) == "Q2 2026"


def test_router_detects_identification_questions():
    ctx = _ctx(MemoryEvidenceRepository(), "Which Big 6 bank reported cumulative acquisition synergies far exceeding "
                                           "year-1 targets, yet saw its stock decline sharply?")
    assert route(ctx).kind == "identification"


def test_table_row_snippet_keeps_its_entity():
    from app.tools.registry import _best_part
    table = ("| Rank | Ticker | Why |\n|---|---|---|\n| 2 | CM | 8/8 beats, 9 consecutive quarters |\n"
             "| 3 | NA | CWB synergies at 176% of target, best efficiency ratio (49.8%) |")
    assert _best_part(table, "synergies exceeding target best efficiency").startswith("| 3 | NA |")


def test_citations_about_another_entity_downgrade_identification_answer(run_script):
    from contracts.models import AnswerStatus
    from tests.engine.conftest import pick, submit
    ans, _, _ = run_script("Which miner had revenue of $152.6M in Q2 2026 amid DRC logistical constraints?", [
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "It is SHOP [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))],
                values=[{"label": "company", "value": None, "value_text": "SHOP (Shopify)", "unit": "text",
                         "currency": None, "period_label": None, "citation_ns": [1]}])],
    ])
    assert ans.status == AnswerStatus.partial
    assert any("not SHOP" in lim for lim in ans.limitations)


def test_prose_snippet_keeps_the_explanation_after_the_match():
    from app.tools.registry import _best_part
    text = ("**1. Headline Results**\nRevenue of $152.6M (vs consensus $202.1M — significant miss). EPS of $0.03 "
            "missed. Revenue miss driven by lower KK payable sales and DRC logistical constraints.")
    assert "logistical" in _best_part(text, "Q2 2026 revenue miss consensus")


def test_ticker_matches_company_name_prefix_only_for_long_tickers():
    from app.eval.runner import _mentions
    assert _mentions("Shopify grew faster", "SHOP")
    assert not _mentions("a quarterly result", "RY")
    assert _mentions("RY (Royal Bank)", "RY")


def test_entity_matcher_uses_names_not_ticker_shape():
    from app.retrieval.context import EntityMatcher
    m = EntityMatcher({"RY": ["royal bank of canada"], "Acme Corp": ["acme corporation"], "NA": ["national bank"]})
    assert m.find("Royal Bank of Canada beat estimates") == ["RY"]
    assert m.find("a quarterly note on the national average") == []        # 'RY'/'NA' never match inside words
    assert m.find("Shares of Acme Corporation fell; National Bank rose") == ["Acme Corp", "NA"]


def test_subjects_are_entities_with_their_own_sections_with_fallback():
    ctx = _ctx(MemoryEvidenceRepository())
    assert ctx.subjects() == {"IVN", "SHOP"}                  # only these have ### sections in the excerpts
    assert ctx.entities_in("| **Revenue Growth** | WPM (+85%) |") == ["WPM"]   # falls back to other entities
    assert ctx.entities_in("Ivanhoe Mines and WPM") == ["IVN"]                 # sections' owners win


def test_find_candidates_prefers_evidence_inside_the_likely_period():
    ctx = _ctx(MemoryEvidenceRepository())
    out = find_candidates(ctx, FindCandidatesArgs(clues=["payable sales logistical constraints",
                                                         "Kipushi record zinc 70,177t"]))
    top = out["candidates"][0]
    assert top["entity"] == "IVN" and top["likely_period"] == "Q2 2026"
    assert all(e.get("in_period") for e in top["evidence"])
