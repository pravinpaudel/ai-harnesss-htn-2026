"""Answer policy gate: turns the model's submit_answer into a contract-valid AnswerResponse.

The model proposes; this module decides. It rebuilds every citation from repository spans,
drops anything that fails verification, forces operand citations for calculations, checks
that stated numbers are backed by evidence, escalates to `conflict` when cited evidence is
part of an open validation finding, and derives evidence_status. contracts.models.AnswerResponse's
own validator is the final check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from contracts.models import (
    AnswerResponse, AnswerStatus, AnswerValue, CalculationResult, Citation, ConflictClaim, ConflictRef,
    DeclineReason, EvidenceKind, EvidenceStatus, Period, PeriodType, Provenance, SourceSpan, Usage,
    ValidationFinding,
)
from app.reasoning.context import RunContext
from app.reasoning.verifier import verify_span
from app.tools.registry import SubmitAnswerArgs

_MARKER = re.compile(r"\[(\d+)\]")
_ORDERING = re.compile(r"\b(rank\w*|order\w*|largest|smallest|biggest|bigger|larger|smaller|higher|lower|highest|"
                       r"lowest|compare\w*|versus|vs\.?)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])[-−]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?(?:%|bps|[KMB]\b|x\b)?")


@dataclass
class PolicyNotes:
    """What the gate changed, recorded as a tool_event for the audit trail."""
    dropped_citations: list[str] = field(default_factory=list)
    added_citations: list[str] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    status_changes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


class _Citations:
    def __init__(self, ctx: RunContext, notes: PolicyNotes):
        self.ctx, self.notes = ctx, notes
        self.items: dict[int, Citation] = {}
        self.by_span: dict[tuple, int] = {}

    def _key(self, s: SourceSpan) -> tuple:
        return (s.document_name, s.char_start, s.char_end)

    def add(self, span: SourceSpan, kind: EvidenceKind, evidence_id, n: Optional[int] = None,
            label: str = "") -> Optional[int]:
        key = self._key(span)
        if key in self.by_span:
            return self.by_span[key]
        ok, why = verify_span(self.ctx.repo, self.ctx.dataset_version_id, span)
        if not ok:
            self.notes.dropped_citations.append(f"{label or span.document_name}: {why}")
            return None
        if n is None or n in self.items:
            n = max(self.items, default=0) + 1
        self.items[n] = Citation(citation_id=n, span=span, evidence_kind=kind, evidence_id=evidence_id,
                                 verified=True)
        self.by_span[key] = n
        return n


def _norm_number(tok: str) -> Optional[float]:
    t = tok.replace("−", "-").replace("$", "").replace(",", "").replace("%", "").replace("bps", "").strip()
    mult = 1.0
    if t and t[-1] in "KMBx":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "x": 1.0}[t[-1]]
        t = t[:-1].strip()
    try:
        return float(t) * mult
    except ValueError:
        return None


def _supported_numbers(texts: list[str], calcs: list[CalculationResult]) -> set[float]:
    out: set[float] = set()
    for t in texts:
        for m in _NUMBER.finditer(t):
            v = _norm_number(m.group(0))
            if v is not None:
                out.update({round(v, 6), round(abs(v), 6)})
                bare = _norm_number(re.sub(r"[KMB]$", "", m.group(0).strip()))
                if bare is not None:
                    out.add(round(abs(bare), 6))
    for c in calcs:
        for v in [c.result, *[o.value for o in c.operands]]:
            if v is not None:
                out.update({round(v, 6), round(abs(v), 6)})
    return out


def _is_trivial(tok: str, v: float) -> bool:
    t = tok.strip().rstrip(",.;:")
    if re.fullmatch(r"\d{4}", t) and 1900 <= v <= 2100:
        return True  # years
    return bool(re.fullmatch(r"\d{1,2}", t))  # small counts, quarter numbers, footnote-like digits


def _claims_from_finding(f: ValidationFinding, cites: _Citations) -> list[ConflictClaim]:
    """Claim A = the finding's first span (the asserted claim); claim B = the remaining spans."""
    ns = [cites.add(s, EvidenceKind.fact, None, label=f"finding {f.rule.value}") for s in f.spans[:6]]
    ns = [n for n in ns if n]
    if len(ns) < 2:
        return []
    return [ConflictClaim(text=f"Stated: {f.observed or f.spans[0].exact_text}", citation_ids=[ns[0]]),
            ConflictClaim(text=f"Other evidence: {f.expected or 'see cited sources'}", citation_ids=ns[1:])]


def _entity_label(named: str, labels: set[str]) -> Optional[str]:
    """'TD (TD Bank)' / 'RY — Royal Bank of Canada' -> the dataset label it starts with."""
    head = re.split(r"[\s(—–,]", named.strip(), maxsplit=1)[0].strip("*")
    return head if head in labels else None


def finalize(ctx: RunContext, sub: SubmitAnswerArgs, *, model: str, prompt_version: str,
             embedding_model: Optional[str], extra_limitations: list[str] = ()) -> tuple[AnswerResponse, PolicyNotes]:
    notes = PolicyNotes()
    cites = _Citations(ctx, notes)
    limitations = list(sub.limitations) + list(extra_limitations)
    status = sub.status
    decline_reason = sub.decline_reason

    # 1. citations from handles (model's numbering kept where possible)
    renumber: dict[int, int] = {}
    cited_fact_ids = []
    calc_handles = list(dict.fromkeys(sub.calculation_handles))
    finding_handles = list(dict.fromkeys(sub.conflict_finding_handles))
    folded: set[int] = set()      # markers that pointed at a calculation/finding instead of a source
    for ref in sub.citations:
        if ref.handle in ctx.calculations or ref.handle in ctx.findings:
            (calc_handles if ref.handle in ctx.calculations else finding_handles).append(ref.handle)
            folded.add(ref.n)
            continue
        ev = ctx.evidence.get(ref.handle)
        if ev is None:
            notes.dropped_citations.append(f"[{ref.n}] {ref.handle}: not a handle returned by a tool")
            continue
        kind = ev.kind if isinstance(ev.kind, EvidenceKind) else (
            EvidenceKind(ev.kind) if ev.kind in EvidenceKind._value2member_map_ else EvidenceKind.chunk)
        n = cites.add(ev.span, kind, ev.evidence_id, n=ref.n, label=f"[{ref.n}] {ref.handle}")
        if n is not None:
            renumber[ref.n] = n
            if ev.fact is not None:
                cited_fact_ids.append(ev.fact.fact_id)

    # 2. calculations: every operand cited
    calcs: list[CalculationResult] = []
    for h in dict.fromkeys(calc_handles):
        calc = ctx.calculations.get(h)
        if calc is None:
            notes.dropped_citations.append(f"{h}: not a calculation handle")
            continue
        calcs.append(calc)
        for o in calc.operands:
            before = len(cites.items)
            n = cites.add(o.span, EvidenceKind.fact, o.fact_id, label=f"operand {o.name}")
            if n is not None and len(cites.items) > before:
                notes.added_citations.append(f"[{n}] operand {o.name}")
            if o.fact_id:
                cited_fact_ids.append(o.fact_id)
        if calc.rejected:
            limitations.append(f"Calculation rejected: {calc.rejection_reason}")

    # 3. answer text: remap markers, strip markers without a verified citation
    def remap(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[{renumber[n]}]" if n in renumber else ""

    answer = _MARKER.sub(remap, sub.answer).replace("  ", " ").strip()
    if len(renumber) < len({int(x) for x in _MARKER.findall(sub.answer)} - folded):
        limitations.append("Some footnotes were removed because their sources could not be verified.")

    # 4. numbers in the answer must be backed by cited evidence or a calculation
    cited_texts = [c.span.exact_text for c in cites.items.values()]
    for ev in ctx.evidence.values():
        if ev.fact and any(c.span.char_start == ev.span.char_start and c.span.document_name == ev.span.document_name
                           for c in cites.items.values()):
            cited_texts.append(" ".join(str(x) for x in (ev.fact.value.value, ev.fact.value.value_low,
                                                          ev.fact.value.value_high) if x is not None))
    supported = _supported_numbers(cited_texts, calcs)
    for m in _NUMBER.finditer(_MARKER.sub("", answer)):
        tok = m.group(0)
        v = _norm_number(tok)
        if v is None or _is_trivial(tok, abs(v)):
            continue
        if not any(abs(abs(v) - s) <= max(0.006, abs(s) * 0.0005) for s in supported):
            notes.unsupported_numbers.append(tok.strip())
    if notes.unsupported_numbers:
        limitations.append("Not found in cited evidence: " + ", ".join(sorted(set(notes.unsupported_numbers))))
        if status == AnswerStatus.answered:
            status = AnswerStatus.partial
            notes.status_changes.append("answered -> partial (unsupported numbers)")

    # 5. contradictions: model-surfaced findings + automatic escalation from cited facts
    conflicts: list[ConflictRef] = []
    seen_findings = set()
    findings = [ctx.findings[h] for h in dict.fromkeys(finding_handles) if h in ctx.findings]
    if cited_fact_ids:
        cited = set(cited_fact_ids)
        for f in ctx.repo.list_findings(ctx.dataset_version_id, fact_ids=list(dict.fromkeys(cited_fact_ids))):
            # relevant only if the answer relies on the finding's own claim, or compares 2+ of its facts
            # (citing ABX's revenue is not affected by TECK.B's revenue being in CAD in the same column)
            if (f.fact_ids and f.fact_ids[0] in cited) or len(cited & set(f.fact_ids)) >= 2:
                findings.append(f)
    for f in findings:
        if f.finding_id in seen_findings:
            continue
        seen_findings.add(f.finding_id)
        claims = _claims_from_finding(f, cites)
        if claims:
            conflicts.append(ConflictRef(finding_id=f.finding_id, rule=f.rule, explanation=f.explanation,
                                         claims=claims))
        else:
            limitations.append(f"Related finding ({f.rule.value}): {f.explanation}")
    if conflicts and status in (AnswerStatus.answered, AnswerStatus.partial):
        notes.status_changes.append(f"{status.value} -> conflict (cited evidence has open findings)")
        status = AnswerStatus.conflict
    if status == AnswerStatus.conflict and not conflicts:
        status = AnswerStatus.partial if cites.items else AnswerStatus.declined
        notes.status_changes.append(f"conflict -> {status.value} (no verifiable contradiction)")
        limitations.append("The model reported a conflict that is not backed by a recorded finding.")

    # 5b. an ordering across different currencies is not comparable without a cited conversion
    currencies = {v.currency for v in sub.values if v.currency}
    for ev in ctx.evidence.values():
        if ev.fact and ev.fact.value.currency and cites.by_span.get(
                (ev.span.document_name, ev.span.char_start, ev.span.char_end)):
            currencies.add(ev.fact.value.currency)
    converted = any(c.operation.value in ("rank", "compare") and not c.rejected and len(
        {o.currency for o in c.operands if o.currency}) > 1 for c in calcs)
    if (len(currencies) > 1 and _ORDERING.search(ctx.question) and not converted
            and status == AnswerStatus.answered):
        status = AnswerStatus.partial
        limitations.append("The values are in different currencies (" + ", ".join(sorted(currencies))
                           + ") and the dataset states no conversion, so they cannot be ranked or compared "
                             "directly.")
        notes.status_changes.append("answered -> partial (ordering across currencies)")

    # 5c. identification answers: citations must be about the entity the answer names
    named = next((v.value_text for v in sub.values if v.label.strip().lower() in ("company", "entity") and v.value_text),
                 None)
    if named and cites.items:
        found = ctx.entities_in(named)
        label = found[0] if found else _entity_label(named, ctx.entity_labels())
        if label:
            off = []
            for n, c in cites.items.items():
                sc = ctx.span_context(c.span)
                if sc.entity == label:
                    continue
                if sc.entity is None and label in ctx.entities_in(c.span.exact_text):
                    continue
                off.append((n, sc.entity))
            for n, other in off:
                limitations.append(f"Citation [{n}] is about {other or 'another section'}, not {label}.")
            if off and len(off) * 2 >= len(cites.items) and status == AnswerStatus.answered:
                status = AnswerStatus.partial
                notes.status_changes.append(f"answered -> partial ({len(off)} of {len(cites.items)} citations "
                                            f"are not about {label})")

    # 6. no verified evidence -> decline
    if status in (AnswerStatus.answered, AnswerStatus.partial) and not cites.items:
        notes.status_changes.append(f"{status.value} -> declined (no verified citations)")
        status = AnswerStatus.declined
    if status == AnswerStatus.declined and decline_reason is None:
        decline_reason = DeclineReason.insufficient_evidence
    if status != AnswerStatus.declined:
        decline_reason = None
    if status == AnswerStatus.partial and not limitations:
        limitations.append("Only part of the question is supported by the cited evidence.")

    # 7. values keep only verified citation ids, and only a currency the cited evidence states
    span_currency: dict[int, Optional[str]] = {}
    for n, c in cites.items.items():
        for ev in ctx.evidence.values():
            if ev.fact and (ev.span.document_name, ev.span.char_start, ev.span.char_end) == \
                    (c.span.document_name, c.span.char_start, c.span.char_end):
                span_currency[n] = ev.fact.value.currency
    values = []
    for v in sub.values:
        ids = [renumber[n] for n in v.citation_ns if n in renumber]
        if not ids:
            continue
        currency = v.currency
        if currency and currency not in {span_currency.get(n) for n in ids}:
            limitations.append(f"Currency {currency} removed from '{v.label}': the cited source does not state it.")
            notes.status_changes.append(f"value '{v.label}': currency {currency} dropped")
            currency = None
        values.append(AnswerValue(label=v.label, value=v.value, value_text=v.value_text, unit=v.unit,
                                  currency=currency,
                                  period=Period(label=v.period_label, type=PeriodType.unspecified)
                                  if v.period_label else None,
                                  citation_ids=ids))

    evidence_status = {
        AnswerStatus.answered: EvidenceStatus.fully_supported,
        AnswerStatus.conflict: EvidenceStatus.conflicting,
        AnswerStatus.partial: EvidenceStatus.partial_support,
        AnswerStatus.declined: EvidenceStatus.unsupported,
    }[status]

    resp = AnswerResponse(
        run_id=ctx.run_id, status=status, answer=answer or "No supported answer.", values=values,
        citations=sorted(cites.items.values(), key=lambda c: c.citation_id), calculation_trace=calcs,
        conflicts=conflicts, limitations=list(dict.fromkeys(limitations)), decline_reason=decline_reason,
        evidence_status=evidence_status, dataset_version=ctx.dataset_version_id,
        provenance=Provenance(dataset_id=ctx.dataset_id, source_hash=ctx.source_hash or "unknown",
                              parser_version=ctx.parser_version, prompt_version=prompt_version, model=model,
                              embedding_model=embedding_model,
                              usage=Usage(input_tokens=ctx.usage.input_tokens, output_tokens=ctx.usage.output_tokens,
                                          cost_usd=round(ctx.usage.cost_usd, 6), latency_ms=ctx.elapsed_ms(),
                                          tool_rounds=ctx.usage.tool_rounds)),
    )
    return resp, notes


def budget_exhausted(ctx: RunContext, reason: str, *, model: str, prompt_version: str,
                     embedding_model: Optional[str]) -> tuple[AnswerResponse, PolicyNotes]:
    sub = SubmitAnswerArgs(status=AnswerStatus.declined, answer="No answer: the run stopped before a verified answer was submitted.",
                           citations=[], values=[], calculation_handles=[], conflict_finding_handles=[],
                           limitations=[f"Run stopped: {reason}"], decline_reason=DeclineReason.budget_exhausted)
    return finalize(ctx, sub, model=model, prompt_version=prompt_version, embedding_model=embedding_model)

