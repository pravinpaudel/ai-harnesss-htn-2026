"""Evaluation runner: EvaluationCase list -> EvaluationReport, using the same engine as `htn ask`."""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from contracts.models import (
    AnswerResponse, AnswerStatus, CaseResult, EvaluationCase, EvaluationReport, QuestionCategory,
)

_MONEY = re.compile(r"(-?\$?-?\d[\d,]*(?:\.\d+)?)\s?([KMB])?\b")


def load_cases(path: Path) -> list[EvaluationCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw["cases"] if isinstance(raw, dict) else raw
    return [EvaluationCase.model_validate(c) for c in items]


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _MONEY.finditer(text.replace("−", "-")):
        try:
            v = float(m.group(1).replace("$", "").replace(",", ""))
        except ValueError:
            continue
        out.append(v * {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2) or "", 1.0))
    return out


def _mentions(text: str, expected: str) -> bool:
    """Whole-word match. Short all-caps tokens (tickers like RY, NA, CM) must match case-sensitively,
    otherwise 'RY' would match 'quarterly' and 'NA' would match 'national'."""
    exact = expected.isupper() and len(expected) <= 6
    pattern = rf"(?<![A-Za-z0-9]){re.escape(expected)}(?![A-Za-z0-9])"
    if re.search(pattern, text, 0 if exact else re.IGNORECASE):
        return True
    # tickers of 4+ letters may appear as the start of the company name ("SHOP" -> "Shopify")
    return exact and len(expected) >= 4 and re.search(rf"(?<![A-Za-z0-9]){re.escape(expected)}",
                                                    text, re.IGNORECASE) is not None


def score(case: EvaluationCase, ans: AnswerResponse) -> CaseResult:
    e = case.expect
    status_ok = ans.status in e.acceptable_statuses
    if ans.status == AnswerStatus.declined and e.decline_reasons:
        status_ok = status_ok and ans.decline_reason in e.decline_reasons

    candidates = [v.value for v in ans.values if v.value is not None]
    candidates += [c.result for c in ans.calculation_trace if c.result is not None]
    candidates += _numbers_in(ans.answer)
    texts = [str(v.value_text or "") for v in ans.values] + [ans.answer]
    values_ok = True
    for ev in e.values:
        if ev.value is not None:
            tol = max(ev.tolerance, 1e-9)
            if not any(abs(c - ev.value) <= tol or abs(abs(c) - abs(ev.value)) <= tol for c in candidates):
                values_ok = False
        if ev.value_text and not any(_mentions(t, ev.value_text) for t in texts):
            values_ok = False
    if e.ordered_items:
        order = next((c.result_items for c in ans.calculation_trace if c.result_items), [])
        values_ok = values_ok and order[: len(e.ordered_items)] == e.ordered_items
    mention_ok = all(m.lower() in ans.answer.lower() for m in e.must_mention)
    calc_ok = (not e.requires_calculation) or any(not c.rejected for c in ans.calculation_trace)
    rules_ok = (not e.finding_rules or ans.status != AnswerStatus.conflict
                or any(c.rule in e.finding_rules for c in ans.conflicts))
    def hit(a) -> bool:
        return any(c.span.document_name == a.document_name and c.span.line_start <= a.line_end
                   and c.span.line_end >= a.line_start for c in ans.citations)

    anchors_ok = all(hit(a) for a in e.must_cite) and (not e.must_cite_any or any(hit(a) for a in e.must_cite_any))
    citations_valid = all(c.verified for c in ans.citations)

    passed = status_ok and values_ok and mention_ok and calc_ok and rules_ok and anchors_ok and citations_valid
    failure = None
    if not passed:
        if not citations_valid:
            failure = "policy_or_citation_failure"
        elif e.requires_calculation and not calc_ok:
            failure = "calculation_issue"
        elif not status_ok:
            failure = "policy_or_citation_failure"
        elif not anchors_ok or not values_ok:
            failure = "retrieval_miss"
        else:
            failure = "benchmark_ambiguity"
    detail = ", ".join(k for k, ok in (("status", status_ok), ("values", values_ok), ("mentions", mention_ok),
                                       ("calculation", calc_ok), ("finding rule", rules_ok),
                                       ("anchors", anchors_ok)) if not ok)
    return CaseResult(case_id=case.case_id, category=case.category, run_id=ans.run_id, passed=passed,
                      status_ok=status_ok, values_ok=values_ok and mention_ok and calc_ok,
                      citations_valid=citations_valid, anchors_hit=anchors_ok, failure_class=failure,
                      usage=ans.provenance.usage, detail=(f"failed: {detail}; got {ans.status.value}" if detail else None))


def run(cases: list[EvaluationCase], ask: Callable[[str], AnswerResponse], *, suite: str,
        on_result: Optional[Callable[[CaseResult, AnswerResponse], None]] = None) -> EvaluationReport:
    started = datetime.now(timezone.utc)
    results: list[CaseResult] = []
    answers: list[AnswerResponse] = []
    for case in cases:
        ans = ask(case.question)
        res = score(case, ans)
        results.append(res)
        answers.append(ans)
        if on_result:
            on_result(res, ans)
    finished = datetime.now(timezone.utc)

    by_cat: dict[QuestionCategory, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r.passed)
    conflict_cases = [(r, a) for r, a in zip(results, answers) if r.category == QuestionCategory.conflict]
    unsupported = [(r, a) for r, a in zip(results, answers)
                   if r.category in (QuestionCategory.decline, QuestionCategory.false_premise)]
    lat = sorted(a.provenance.usage.latency_ms for a in answers) or [0]
    cites = [c for a in answers for c in a.citations]
    return EvaluationReport(
        report_id=uuid4(), dataset_version_id=answers[0].dataset_version if answers else uuid4(), suite=suite,
        started_at=started, finished_at=finished, cases=results,
        accuracy_by_category={k: sum(v) / len(v) for k, v in by_cat.items()},
        citation_validity=(sum(c.verified for c in cites) / len(cites)) if cites else 1.0,
        conflict_recall=(sum(a.status == AnswerStatus.conflict for _, a in conflict_cases) / len(conflict_cases))
        if conflict_cases else 1.0,
        unsupported_answer_rate=(sum(a.status == AnswerStatus.answered for _, a in unsupported) / len(unsupported))
        if unsupported else 0.0,
        mean_latency_ms=statistics.fmean(lat), p95_latency_ms=float(lat[min(len(lat) - 1, int(0.95 * len(lat)))]),
        mean_tool_rounds=statistics.fmean([a.provenance.usage.tool_rounds for a in answers] or [0]),
        mean_cost_usd=statistics.fmean([a.provenance.usage.cost_usd for a in answers] or [0]),
    )
