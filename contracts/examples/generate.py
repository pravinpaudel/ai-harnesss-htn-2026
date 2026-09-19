"""Generate example AnswerResponse JSON files from the fixture (answered+calculation, conflict, declined).

Run from the repo root after building the fixture:
    uv run --python 3.12 --with "pydantic>=2" python -m contracts.examples.generate

The examples show Developer B's exact output shape and give eval/CLI tests a known-good
object. Spans come from contracts/fixture/expected_facts.csv, so quotes are verbatim.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from uuid import UUID

from contracts.models import (
    AnswerResponse, AnswerStatus, AnswerValue, CalculationResult, Citation, ConflictClaim, ConflictRef,
    DeclineReason, EvidenceKind, EvidenceStatus, FindingRule, Operand, Operation, Period, PeriodType,
    Provenance, SourceSpan, Unit, Usage,
)

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "contracts" / "fixture"
OUT = Path(__file__).resolve().parent

DATASET_ID = UUID("00000000-0000-4000-8000-00000000d001")
VERSION_ID = UUID("00000000-0000-4000-8000-00000000d002")

facts = {r["fact_key"]: r for r in csv.DictReader(open(FIX / "expected_facts.csv", encoding="utf-8"))}
sources = json.loads((FIX / "sources.json").read_text(encoding="utf-8"))
SOURCE_HASH = hashlib.sha256("".join(sorted(s["sha256"] for s in sources.values())).encode()).hexdigest()


def span(key: str) -> SourceSpan:
    r = facts[key]
    return SourceSpan(document_name=r["document_name"], document_hash=sources[r["document_name"]]["sha256"],
                      line_start=int(r["line_start"]), line_end=int(r["line_end"]),
                      char_start=int(r["char_start"]), char_end=int(r["char_end"]),
                      exact_text=r["original_value"])


def cite(n: int, key: str) -> Citation:
    return Citation(citation_id=n, span=span(key), evidence_kind=EvidenceKind.fact, verified=True)


def prov(inp: int, out: int, cost: float, ms: int, rounds: int) -> Provenance:
    return Provenance(dataset_id=DATASET_ID, source_hash=SOURCE_HASH, parser_version="example",
                      prompt_version="example", model="example-model",
                      usage=Usage(input_tokens=inp, output_tokens=out, cost_usd=cost, latency_ms=ms,
                                  tool_rounds=rounds))


q1 = Period(label="Q1 2026", type=PeriodType.calendar)
q2 = Period(label="Q2 2026", type=PeriodType.calendar)
calc = CalculationResult(
    operation=Operation.percent_change,
    formula="(152.6M - 165.5M) / 165.5M x 100",
    operands=[
        Operand(name="IVN revenue Q1 2026", value=165.5e6, unit=Unit.money, period=q1,
                span=span("m:IVN:revenue:Q1 2026")),
        Operand(name="IVN revenue Q2 2026", value=152.6e6, unit=Unit.money, period=q2,
                span=span("m:IVN:revenue:Q2 2026")),
    ],
    result=-7.79, unit=Unit.pct, rounding=2,
)

examples = {
    "answered-calculation.json": AnswerResponse(
        run_id=UUID("00000000-0000-4000-8000-0000000a0001"), status=AnswerStatus.answered,
        answer="Ivanhoe's revenue fell 7.79% from Q1 2026 to Q2 2026, from $165.5M [1] to $152.6M [2].",
        values=[AnswerValue(label="IVN revenue change, Q1 2026 to Q2 2026", value=-7.79, unit=Unit.pct,
                            citation_ids=[1, 2])],
        citations=[cite(1, "m:IVN:revenue:Q1 2026"), cite(2, "m:IVN:revenue:Q2 2026")],
        calculation_trace=[calc],
        limitations=["Revenue is IVN's consolidated revenue; Kamoa-Kakula is equity-accounted and excluded."],
        evidence_status=EvidenceStatus.fully_supported, dataset_version=VERSION_ID,
        provenance=prov(4200, 180, 0.012, 3400, 2)),
    "conflict.json": AnswerResponse(
        run_id=UUID("00000000-0000-4000-8000-0000000a0002"), status=AnswerStatus.conflict,
        answer="The report contradicts itself. The screening table says Ivanhoe beat EPS estimates in "
               "3 of 8 quarters [1], but the quarterly summaries show 2 beats [2][3] and 1 quarter that "
               "met the estimate [4].",
        citations=[cite(1, "m:IVN:eps_beat_count:screen"), cite(2, "m:IVN:beat_miss:Q1 2025"),
                   cite(3, "m:IVN:beat_miss:Q3 2025"), cite(4, "m:IVN:beat_miss:Q4 2025")],
        conflicts=[ConflictRef(
            finding_id=UUID("00000000-0000-4000-8000-0000000f0001"), rule=FindingRule.count_claim,
            explanation="Screening count (3/8) does not match the quarterly records (2 beat, 1 met, 5 miss).",
            claims=[ConflictClaim(text="Screening table: 3 of 8 beats", citation_ids=[1]),
                    ConflictClaim(text="Quarterly summaries: 2 beats (Q1 2025, Q3 2025), 1 met (Q4 2025)",
                                  citation_ids=[2, 3, 4])])],
        evidence_status=EvidenceStatus.conflicting, dataset_version=VERSION_ID,
        provenance=prov(5100, 210, 0.015, 4100, 3)),
    "declined-false-premise.json": AnswerResponse(
        run_id=UUID("00000000-0000-4000-8000-0000000a0003"), status=AnswerStatus.declined,
        answer="The question's premise is not supported: Shopify reported EPS of $0.42 [1] against an "
               "estimate of $0.39 [2], which the report records as a beat [3].",
        citations=[cite(1, "t:SHOP:eps:Q2 2026"), cite(2, "t:SHOP:eps_estimate:Q2 2026"),
                   cite(3, "t:SHOP:beat_miss:Q2 2026")],
        decline_reason=DeclineReason.false_premise,
        evidence_status=EvidenceStatus.unsupported, dataset_version=VERSION_ID,
        provenance=prov(3000, 120, 0.008, 2600, 1)),
}

for name, obj in examples.items():
    (OUT / name).write_text(obj.model_dump_json(indent=1, exclude_none=True) + "\n", encoding="utf-8")
    print("wrote", name)
