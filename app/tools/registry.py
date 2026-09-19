"""Typed, read-only tools exposed to the model.

Each tool has a Pydantic argument model (its JSON schema is sent to the Responses API in
strict mode) and a handler that takes the RunContext. Results are compact dicts that
refer to evidence by handle; they never ask the model to copy quote text.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from contracts.models import (
    AnswerStatus, Basis, CalculationRequest, DeclineReason, EvidenceKind, Fact, FactFilter, Operand,
    Operation, Role, Unit,
)
from app.reasoning.consistency import agree, comparable_groups, most_precise
from app.reasoning.context import RunContext
from app.retrieval.context import period_of
from app.tools.calculator import calculate


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ args --

class InspectDatasetArgs(_Args):
    pass


class ResolveEntityArgs(_Args):
    name: str = Field(description="Company or entity name, ticker, or alias as the user wrote it")


class FindFactsArgs(_Args):
    entity: Optional[str] = Field(None, description="Entity label or alias; null for all entities")
    metric: Optional[str] = Field(None, description="Words from the metric name or column label, e.g. 'revenue', 'Rev YoY', 'market cap'")
    role: Optional[Role] = Field(None, description="actual, estimate, rank, count, trend, attribute, ...")
    period: Optional[str] = Field(None, description="Period label prefix as written in the dataset, e.g. 'Q2 2026'")
    basis: Optional[Basis] = None
    currency: Optional[str] = Field(None, description="3-letter code, e.g. CAD")
    limit: int = Field(40, ge=1, le=200)


class SearchEvidenceArgs(_Args):
    query: str = Field(description="Keywords or a phrase to search narrative text, table cells and facts")
    entity: Optional[str] = Field(None, description="Only return evidence from this entity's own sections")
    period: Optional[str] = Field(None, description="Only return evidence from this period's block, e.g. 'Q3 FY2026'")
    k: int = Field(6, ge=1, le=15)


class FindCandidatesArgs(_Args):
    clues: list[str] = Field(description="3-6 short, distinctive clues copied or paraphrased from the question, e.g. "
                                         "'fifth consecutive quarter of positive operating leverage', 'goodwill "
                                         "impairment divestitures', 'stock fell on the release date'")
    k_per_clue: int = Field(12, ge=3, le=30)


class GetSourceSpanArgs(_Args):
    handle: Optional[str] = Field(None, description="Evidence handle to expand to its full source lines")
    document_name: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None


class CalculateArgs(_Args):
    operation: Operation
    operand_handles: list[str] = Field(description="Fact handles. For difference/percent_change/ratio the first "
                                                   "is the starting value / denominator")
    rounding: int = Field(2, ge=0, le=6)
    allow_mixed_currency: bool = Field(False, description="Only true when a handle is a source-stated conversion")


class CompareValuesArgs(_Args):
    operand_handles: list[str]


class ListFindingsArgs(_Args):
    entity: Optional[str] = None
    handles: Optional[list[str]] = Field(None, description="Fact handles to check for recorded contradictions")


class CheckCoverageArgs(_Args):
    entity: Optional[str] = None
    metric: Optional[str] = None
    period: Optional[str] = None


class CitationRef(_Args):
    n: int = Field(ge=1, description="Footnote number used as [n] in the answer")
    handle: str = Field(description="Evidence handle (E…) returned by a tool")


class SubmittedValue(_Args):
    label: str
    value: Optional[float] = Field(None, description="Fully expanded number: 152.6M -> 152600000; 7.8% -> 7.8")
    value_text: Optional[str] = None
    unit: Unit
    currency: Optional[str] = None
    period_label: Optional[str] = None
    citation_ns: list[int]


class SubmitAnswerArgs(_Args):
    status: AnswerStatus
    answer: str = Field(description="Concise answer; every material claim ends with [n] footnote markers")
    citations: list[CitationRef]
    values: list[SubmittedValue]
    calculation_handles: list[str] = Field(description="C… handles of calculations the answer relies on")
    conflict_finding_handles: list[str] = Field(description="F… handles of contradictions the answer surfaces")
    limitations: list[str]
    decline_reason: Optional[DeclineReason] = None


# ------------------------------------------------------------- handlers --

def _entity_ids(ctx: RunContext, name: Optional[str]) -> tuple[list, list[str]]:
    if not name:
        return [], []
    cands = ctx.repo.resolve_entity(ctx.dataset_version_id, name)
    return [c.entity_id for c in cands[:1]], [c.label for c in cands[:5]]


def _fact_row(ctx: RunContext, f: Fact) -> dict:
    h = ctx.add_evidence(EvidenceKind.fact, f.fact_id, f.span, fact=f, text=f.value.original_value)
    v = f.value
    row = {"handle": h, "entity": f.entity_label, "metric": f.metric_label or f.metric, "role": f.role.value,
           "value": v.value, "original": v.original_value, "unit": v.unit.value,
           "source": f"{f.span.document_name}:{f.span.line_start}"}
    for k, val in (("low", v.value_low), ("high", v.value_high), ("text", v.value_text), ("currency", v.currency),
                   ("basis", f.basis.value if f.basis else None), ("period", f.period.label),
                   ("estimate", v.is_estimate or None)):
        if val is not None:
            row[k] = val
    return row


def inspect_dataset(ctx: RunContext, a: InspectDatasetArgs) -> dict:
    p = ctx.repo.profile(ctx.dataset_version_id)
    return {"documents": [d.name for d in p.documents],
            "entities": [e.label for e in p.entities],
            "metrics": [m.label for m in p.metrics[:60]],
            "periods": [x.label for x in p.periods[:60]],
            "currencies": [c.label for c in p.currencies],
            "open_findings": {k.value if hasattr(k, "value") else k: v for k, v in p.findings_by_rule.items()},
            "warnings": p.extraction_warnings[:10]}


def resolve_entity(ctx: RunContext, a: ResolveEntityArgs) -> dict:
    c = ctx.repo.resolve_entity(ctx.dataset_version_id, a.name)
    return {"query": a.name, "candidates": [{"label": e.label, "aliases": e.aliases[:6]} for e in c[:5]],
            "found": bool(c)}


def find_facts(ctx: RunContext, a: FindFactsArgs) -> dict:
    ids, labels = _entity_ids(ctx, a.entity)
    if a.entity and not ids:
        return {"facts": [], "note": f"entity {a.entity!r} not found in this dataset"}
    flt = FactFilter(dataset_version_id=ctx.dataset_version_id, entity_ids=ids, metric_text=a.metric,
                     roles=[a.role] if a.role else [], bases=[a.basis] if a.basis else [],
                     period_labels=[a.period] if a.period else [],
                     currencies=[a.currency] if a.currency else [], limit=a.limit)
    facts = ctx.repo.find_facts(flt)
    matched_by_row = False
    if not facts and a.period:
        # Datasets without period labels on facts: match the period text in the fact's source row instead.
        broad = ctx.repo.find_facts(flt.model_copy(update={"period_labels": [], "limit": 500}))
        facts = [f for f in broad if _row_mentions(ctx, f, a.period)][: a.limit]
        matched_by_row = bool(facts)
    rows = [_fact_row(ctx, f) for f in facts]
    for f, row in zip(facts, rows):
        if not f.period.label:  # give the model the row it came from so it can see the period/context
            row["row"] = _source_row(ctx, f)[:220]
    out = {"entity_resolved_to": labels[:1] or None, "count": len(facts), "facts": rows}
    if matched_by_row:
        out["note"] = (f"No fact carries the period label {a.period!r}; these facts were matched because their "
                       "source row mentions it. Check each row before relying on it.")
    notes = _precision_notes(ctx, facts)
    if notes:
        out["same_quantity_notes"] = notes
    return out


def _source_row(ctx: RunContext, f: Fact) -> str:
    key = (f.span.document_name, f.span.line_start, f.span.line_end)
    if key not in ctx.row_cache:
        ctx.row_cache[key] = ctx.repo.read_lines(ctx.dataset_version_id, *key).exact_text
    return ctx.row_cache[key]


def _row_mentions(ctx: RunContext, f: Fact, period: str) -> bool:
    norm = lambda t: " ".join(t.lower().split())  # noqa: E731
    return norm(period) in norm(_source_row(ctx, f))


def _precision_notes(ctx: RunContext, facts: list[Fact]) -> list[dict]:
    """Deterministic guidance when several facts state the same quantity at different precisions."""
    notes = []
    for group in comparable_groups(facts):
        handles = [ctx.add_evidence(EvidenceKind.fact, f.fact_id, f.span, fact=f) for f in group]
        best = most_precise(group)
        best_h = ctx.add_evidence(EvidenceKind.fact, best.fact_id, best.span, fact=best)
        if all(agree(best, f) for f in group):
            notes.append({"handles": handles, "agree_within_rounding": True, "use": best_h,
                          "note": "These state the same value at different precisions (e.g. rounded in a summary "
                                  "table). This is NOT a contradiction; answer with the most precise one."})
        else:
            notes.append({"handles": handles, "agree_within_rounding": False,
                          "note": "These disagree beyond rounding. Check list_validation_findings; if none is "
                                  "recorded, report both values and mark the answer partial."})
    return notes


def search_evidence(ctx: RunContext, a: SearchEvidenceArgs) -> dict:
    scoped = bool(a.entity or a.period)
    want_entity = None
    if a.entity:
        cands = ctx.repo.resolve_entity(ctx.dataset_version_id, a.entity)
        want_entity = cands[0].label if cands else a.entity
    hits = ctx.search(a.query, k=150 if scoped else a.k)
    rows = []
    for h in hits:
        sc = ctx.span_context(h.span)
        if want_entity and sc.entity != want_entity:
            continue
        if a.period and not (period_of(sc.block) or "").lower().startswith(a.period.lower()):
            continue
        fact = h.fact
        if fact is None and h.kind == EvidenceKind.fact:
            fact = ctx.repo.get_fact(ctx.dataset_version_id, h.evidence_id)
        handle = ctx.add_evidence(h.kind, h.evidence_id, h.span, fact=fact, text=h.text)
        part = _best_part(h.text, a.query)
        row = {"handle": handle, "kind": h.kind.value, "text": part[:480],
               "source": f"{h.span.document_name}:{h.span.line_start}-{h.span.line_end}", "where": sc.label[:140]}
        if sc.entity:
            row["entity"] = sc.entity
        else:
            named = ctx.entities_in(part)
            if named:
                row["mentions"] = named[:3]   # a multi-entity table or list: this row is about these
        rows.append(row)
        if len(rows) >= a.k:
            break
    out = {"hits": rows}
    if scoped and not rows:
        out["note"] = "No match inside that entity/period; try other words or drop the period filter."
    return out


def _best_part(text: str, clue: str) -> str:
    """The most relevant piece of a hit. Table rows are kept whole so the row's entity label stays
    attached to its numbers; prose is cut to the best sentence."""
    terms = {t for t in re.findall(r"[a-z0-9$%.]+", clue.lower()) if len(t) > 2}
    score = lambda p: sum(t in p.lower() for t in terms)  # noqa: E731
    lines = [ln for ln in text.split("\n") if ln.strip() and not re.fullmatch(r"[|\-: ]+", ln.strip())]
    best_line = max(lines, key=score) if lines else text
    if best_line.lstrip().startswith("|"):
        return best_line.strip()
    parts = re.split(r"(?<=[.!?;])\s+", best_line)
    if not parts:
        return best_line
    # start at the best sentence and keep the sentences after it: explanations ("driven by ...")
    # usually follow the sentence that matches the question.
    i = max(range(len(parts)), key=lambda k: score(parts[k]))
    return " ".join(parts[i:]).strip()


def _snippet(text: str, clue: str, limit: int = 260) -> str:
    return _best_part(text, clue)[:limit]


_STOP = {"the", "and", "with", "for", "its", "was", "were", "that", "this", "from", "into", "while", "despite", "yet",
         "which", "their", "has", "had", "have", "but", "not", "all", "over", "after", "during", "than", "company",
         "companies", "quarter", "quarters", "record", "significant", "major", "strong", "saw", "reported", "achieved"}


def _terms(text: str) -> set[str]:
    """Distinctive terms, stemmed to 5 characters so 'crushed/crushing' and 'synergies/synergy' meet."""
    words = re.findall(r"[a-z]+|\d+(?:\.\d+)?", text.lower())
    return {w if w[0].isdigit() else w[:5] for w in words if w not in _STOP and (len(w) > 2 or w[0].isdigit())}


def _overlap(clue_terms: set[str], text: str) -> float:
    return len(clue_terms & _terms(text)) / len(clue_terms) if clue_terms else 0.0


def find_candidates(ctx: RunContext, a: FindCandidatesArgs) -> dict:
    """Search each clue separately and rank entities by how well their own sections match all clues.

    A hit counts for a clue in proportion to the share of the clue's distinctive terms it contains.
    Entities whose matches concentrate in one period (one quarter's narrative) get a bonus, because
    these questions describe a single reporting period.
    """
    best: dict[str, dict[str, dict]] = {}                       # entity -> clue -> best match
    by_period: dict[tuple[str, str], dict[str, float]] = {}     # (entity, period) -> clue -> weight
    for clue in a.clues:
        ct = _terms(clue)
        hits = ctx.search(clue, k=a.k_per_clue)
        for rank, h in enumerate(hits):
            ov = _overlap(ct, h.text)
            if ov < 0.34:
                continue
            sc = ctx.span_context(h.span)
            owners = [sc.entity] if sc.entity else ctx.entities_in(_best_part(h.text, clue))[:1]
            weight = ov * ov * (1.0 if sc.entity else 0.5) / (1 + 0.15 * rank)
            period = period_of(sc.block) if sc.entity else None
            for ent in owners:
                if period:
                    cell = by_period.setdefault((ent, period), {})
                    cell[clue] = max(cell.get(clue, 0.0), weight)
                cur = best.setdefault(ent, {}).get(clue)
                if cur and cur["w"] >= weight:
                    continue
                fact = ctx.repo.get_fact(ctx.dataset_version_id, h.evidence_id) if h.kind == EvidenceKind.fact else None
                handle = ctx.add_evidence(h.kind, h.evidence_id, h.span, fact=fact, text=h.text)
                best[ent][clue] = {"w": weight, "handle": handle, "where": sc.label[:140],
                                   "snippet": _snippet(h.text, clue), "overlap": round(ov, 2)}

    # Second pass: generic clues ("dividend increase") return many entities' passages, so a strong
    # candidate may have missing clues simply because other entities outranked it. Look for each
    # missing clue inside the leading candidates' own sections before scoring.
    prelim = sorted(best, key=lambda e: -sum(c["w"] for c in best[e].values()))[:5]
    for ent in prelim:
        for clue in a.clues:
            if clue in best[ent]:
                continue
            hit = _scoped_hit(ctx, clue, ent)
            if hit:
                hit["w"] *= 0.6          # found by a targeted search, so weaker evidence than a corpus-wide hit
                best[ent][clue] = hit
                if hit.get("period"):
                    cell = by_period.setdefault((ent, hit["period"]), {})
                    cell[clue] = max(cell.get(clue, 0.0), hit["w"])

    # A clue that strongly matches many entities ("record net income") says little about which one is
    # meant; weight each clue by how few entities it matches strongly.
    spread = Counter(clue for clues in best.values() for clue, m in clues.items() if m["overlap"] >= 0.6)
    idf = {clue: 1.0 / (max(1, spread.get(clue, 0)) ** 0.5) for clue in a.clues}
    for (e, p), w in by_period.items():
        for clue in w:
            w[clue] *= idf[clue]
    scored = []
    for ent, clues in best.items():
        overall = sum(c["w"] * idf[clue] for clue, c in clues.items())
        periods = [(p, sum(w.values()), len(w)) for (e, p), w in by_period.items() if e == ent]
        top_period = max(periods, key=lambda x: x[1], default=(None, 0.0, 0))
        strong = sum(c["overlap"] >= 0.6 for c in clues.values())
        # matching every clue beats matching a few well; a clue only counts fully when matched strongly
        coverage = sum(min(1.0, c["overlap"] / 0.6) for c in clues.values()) / max(1, len(a.clues))
        scored.append((ent, (overall + 0.5 * top_period[1]) * coverage, strong, top_period, clues))
    scored.sort(key=lambda x: (-x[1], -x[2], x[0]))
    out = []
    for rank_i, (ent, score, strong, (period, _, n_in_period), clues) in enumerate(scored[:5]):
        if period and rank_i < 2:
            # These questions describe one reporting period: prefer evidence from inside that period's block,
            # so citations point at the quarter's own narrative rather than summary tables or other periods.
            for clue in a.clues:
                hit = _scoped_hit(ctx, clue, ent, period)
                if hit:
                    clues[clue] = hit
            n_in_period = sum(v.get("in_period", False) for v in clues.values())
        out.append({
            "entity": ent, "score": round(score, 3), "clues_strongly_matched": strong, "of": len(a.clues),
            "likely_period": period, "clues_in_that_period": n_in_period,
            "evidence": [{"clue": c, "handle": v["handle"], "where": v["where"], "snippet": v["snippet"],
                          **({"in_period": True} if v.get("in_period") else {})}
                         for c, v in clues.items()],
            "missing_clues": [c for c in a.clues if c not in clues],
        })
    return {"candidates": out,
            "note": "Ranked by clue match strength and coverage, with a bonus for clues that fall in one period. "
                    "Verify the leader and any close runner-up before answering; cite one handle per clue, "
                    "preferring in_period evidence."}


def _scoped_hit(ctx: RunContext, clue: str, entity: str, period: Optional[str] = None) -> Optional[dict]:
    """Best evidence for a clue inside one entity's own sections (and one period's block, if given)."""
    ct = _terms(clue)
    best, best_ov = None, 0.34
    for h in ctx.search(clue, k=150):
        sc = ctx.span_context(h.span)
        if sc.entity != entity:
            continue
        if period and period_of(sc.block) != period:
            continue
        ov = _overlap(ct, h.text)
        if ov > best_ov:
            best, best_ov = (h, sc), ov
    if best is None:
        return None
    h, sc = best
    fact = ctx.repo.get_fact(ctx.dataset_version_id, h.evidence_id) if h.kind == EvidenceKind.fact else None
    handle = ctx.add_evidence(h.kind, h.evidence_id, h.span, fact=fact, text=h.text)
    hit = {"w": best_ov * best_ov, "handle": handle, "where": sc.label[:140], "snippet": _snippet(h.text, clue),
           "overlap": round(best_ov, 2), "period": period_of(sc.block)}
    if period:
        hit["in_period"] = True
    return hit


def get_source_span(ctx: RunContext, a: GetSourceSpanArgs) -> dict:
    if a.handle:
        ev = ctx.evidence.get(a.handle)
        if not ev:
            return {"error": f"unknown handle {a.handle}"}
        s = ctx.repo.read_lines(ctx.dataset_version_id, ev.span.document_name, ev.span.line_start, ev.span.line_end)
    elif a.document_name and a.line_start:
        s = ctx.repo.read_lines(ctx.dataset_version_id, a.document_name, a.line_start, a.line_end or a.line_start)
    else:
        return {"error": "give a handle, or document_name + line_start"}
    h = ctx.add_evidence("span", None, s, text=s.exact_text)
    return {"handle": h, "source": f"{s.document_name}:{s.line_start}-{s.line_end}", "text": s.exact_text[:2000]}


def _operands(ctx: RunContext, handles: list[str]) -> tuple[list[Operand], list[str]]:
    ops, errors = [], []
    for h in handles:
        f = ctx.fact_by_handle(h)
        if f is None:
            errors.append(f"{h} is not a fact handle")
        elif f.value.value is None:
            errors.append(f"{h} has no single numeric value ({f.value.original_value})")
        else:
            name = " ".join(x for x in (f.entity_label, f.metric_label or f.metric, f.period.label) if x)
            ops.append(Operand(name=name, fact_id=f.fact_id, value=f.value.value, unit=f.value.unit,
                               currency=f.value.currency, basis=f.basis, period=f.period, span=f.span))
    return ops, errors


def calculate_tool(ctx: RunContext, a: CalculateArgs) -> dict:
    ops, errors = _operands(ctx, a.operand_handles)
    if errors or not ops:
        return {"error": "; ".join(errors) or "no operands"}
    res = calculate(CalculationRequest(operation=a.operation, operands=ops, rounding=a.rounding,
                                       allow_mixed_currency=a.allow_mixed_currency))
    h = ctx.add_calculation(res)
    out = {"handle": h, "formula": res.formula, "result": res.result, "unit": res.unit.value}
    if res.result_items:
        out["order"] = res.result_items
    if res.rejected:
        out = {"handle": h, "rejected": True, "reason": res.rejection_reason}
    return out


def compare_values(ctx: RunContext, a: CompareValuesArgs) -> dict:
    return calculate_tool(ctx, CalculateArgs(operation=Operation.compare, operand_handles=a.operand_handles))


def list_validation_findings(ctx: RunContext, a: ListFindingsArgs) -> dict:
    ids, _ = _entity_ids(ctx, a.entity)
    fact_ids = [ctx.fact_by_handle(h).fact_id for h in (a.handles or []) if ctx.fact_by_handle(h)]
    found = ctx.repo.list_findings(ctx.dataset_version_id, entity_id=ids[0] if ids else None,
                                   fact_ids=fact_ids or None)
    rows = []
    for f in found:
        fh = ctx.add_finding(f)
        span_handles = [ctx.add_evidence("span", s.span_id, s, text=s.exact_text) for s in f.spans[:8]]
        rows.append({"handle": fh, "rule": f.rule.value, "explanation": f.explanation, "expected": f.expected,
                     "observed": f.observed, "evidence_handles": span_handles})
    return {"findings": rows}


def check_coverage(ctx: RunContext, a: CheckCoverageArgs) -> dict:
    ids, labels = _entity_ids(ctx, a.entity)
    out: dict[str, Any] = {"entity_known": (bool(ids) if a.entity else None), "entity": labels[:1] or None}
    if a.entity and not ids:
        out.update(facts=0, supported=False)
        return out
    facts = ctx.repo.find_facts(FactFilter(dataset_version_id=ctx.dataset_version_id, entity_ids=ids,
                                           metric_text=a.metric, period_labels=[a.period] if a.period else [],
                                           limit=200))
    periods = ctx.repo.list_periods(ctx.dataset_version_id, ids[0] if ids else None)
    metrics = ctx.repo.list_metrics(ctx.dataset_version_id, ids[0] if ids else None)
    out.update(facts=len(facts), supported=bool(facts), known_periods=periods[-12:], known_metrics=metrics[:40])
    return out


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    handler: Optional[Callable[[RunContext, Any], dict]]   # None = terminal


TOOLS: list[Tool] = [
    Tool("inspect_dataset", "List the dataset's documents, entities, metrics, periods, currencies and open "
         "contradiction counts. Use discovered terms only.", InspectDatasetArgs, inspect_dataset),
    Tool("resolve_entity", "Resolve a company name, ticker or alias to the dataset's entity label.",
         ResolveEntityArgs, resolve_entity),
    Tool("find_facts", "Typed facts with values, units, currency, period and a citable handle.",
         FindFactsArgs, find_facts),
    Tool("search_evidence", "Keyword search over narrative text, table cells and facts. Use for 'why'/'how' "
         "questions or when find_facts returns nothing.", SearchEvidenceArgs, search_evidence),
    Tool("find_candidates", "For 'which company...' questions: searches each clue separately and ranks entities by "
         "how many clues their own sections match, with the best evidence handle per clue.",
         FindCandidatesArgs, find_candidates),
    Tool("get_source_span", "Expand a handle (or document + lines) to exact source text.",
         GetSourceSpanArgs, get_source_span),
    Tool("calculate", "Deterministic arithmetic over fact handles (difference, percent_change, ratio, sum, "
         "average, median, rank, compare). Rejects mixed currencies/units/bases.", CalculateArgs, calculate_tool),
    Tool("compare_values", "Order fact handles by value; rejects incomparable values.",
         CompareValuesArgs, compare_values),
    Tool("list_validation_findings", "Recorded contradictions / data-quality findings for an entity or for "
         "specific fact handles.", ListFindingsArgs, list_validation_findings),
    Tool("check_coverage", "Whether the dataset has any facts for an entity/metric/period; lists known "
         "periods and metrics.", CheckCoverageArgs, check_coverage),
    Tool("submit_answer", "Submit the final answer. Cite only handles you received from tools.",
         SubmitAnswerArgs, None),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
SUBMIT = "submit_answer"


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON schema in OpenAI strict-mode shape: every property required, no defaults/titles."""
    schema = copy.deepcopy(model.model_json_schema())
    defs = schema.pop("$defs", {})

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            for bad in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                node.pop(bad, None)
            if node.get("type") == "object" or "properties" in node:
                node.setdefault("properties", {})
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            for v in list(node.values()):
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
        return node

    fix(schema)
    for d in defs.values():
        fix(d)
    if defs:
        schema["$defs"] = defs
    return schema


def tool_specs(only: Optional[set[str]] = None) -> list[dict]:
    return [{"type": "function", "name": t.name, "description": t.description,
             "parameters": strict_schema(t.args), "strict": True}
            for t in TOOLS if only is None or t.name in only]


def run_tool(ctx: RunContext, name: str, arguments: dict) -> dict:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None or tool.handler is None:
        return {"error": f"unknown tool {name}"}
    try:
        args = tool.args.model_validate(arguments)
    except Exception as e:  # invalid arguments go back to the model as an error
        return {"error": f"invalid arguments: {e}"}
    try:
        return tool.handler(ctx, args)
    except LookupError as e:
        return {"error": str(e)}

