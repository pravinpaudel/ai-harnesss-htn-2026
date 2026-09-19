"""Load contracts/fixture into plain Python records (test-only; stands in for Developer A's ingest).

Used by the in-memory repository (unit tests without PostgreSQL) and by seed_fixture.py
(PostgreSQL integration tests). Uses the offsets already in expected_facts.csv; no parsing.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "contracts" / "fixture"
NS = uuid.UUID("7b0f7c9e-3c56-4c47-9f8e-2a1f3a6f0a11")


def uid(*parts: object) -> uuid.UUID:
    return uuid.uuid5(NS, "|".join(str(p) for p in parts))


@dataclass
class Doc:
    document_id: uuid.UUID
    name: str
    text: str
    sha256: str
    lines: list[str]

    def offset(self, line_no: int) -> int:
        return sum(len(x) + 1 for x in self.lines[: line_no - 1])

    def heading_path(self, line_no: int) -> list[str]:
        path: list[str] = []
        for i in range(line_no):
            m = re.match(r"^(#{1,3}) (.+?)(\s*\{#.*\})?\s*$", self.lines[i])
            if m:
                level = len(m.group(1))
                path = path[: level - 1] + [m.group(2)]
        return path


@dataclass
class SpanRec:
    span_id: uuid.UUID
    doc: Doc
    line_start: int
    line_end: int
    char_start: int
    char_end: int

    @property
    def exact_text(self) -> str:
        return self.doc.text[self.char_start:self.char_end]


@dataclass
class FactRec:
    key: str
    fact_id: uuid.UUID
    entity_label: Optional[str]
    metric: str
    metric_label: Optional[str]
    role: str
    basis: Optional[str]
    value: Optional[float]
    value_low: Optional[float]
    value_high: Optional[float]
    value_text: Optional[str]
    original_value: str
    unit: str
    currency: Optional[str]
    scale: float
    is_estimate: bool
    period_label: Optional[str]
    period_type: str
    period_end: Optional[date]
    span: SpanRec


@dataclass
class ChunkRec:
    chunk_id: uuid.UUID
    text: str
    span: SpanRec


@dataclass
class FindingRec:
    finding_id: uuid.UUID
    rule: str
    severity: str
    explanation: str
    fact_ids: list[uuid.UUID]
    spans: list[SpanRec]
    expected: str
    observed: str


@dataclass
class FixtureData:
    version_tag: str
    dataset_id: uuid.UUID
    dataset_version_id: uuid.UUID
    source_hash: str
    docs: dict[str, Doc] = field(default_factory=dict)
    entities: dict[str, uuid.UUID] = field(default_factory=dict)
    aliases: dict[str, set[str]] = field(default_factory=dict)      # entity label -> lowercased aliases
    facts: list[FactRec] = field(default_factory=list)
    chunks: list[ChunkRec] = field(default_factory=list)
    findings: list[FindingRec] = field(default_factory=list)


def _f(v: str) -> Optional[float]:
    return float(v) if v != "" else None


def load(version_tag: str = "v1") -> FixtureData:
    sources = json.loads((FIX / "sources.json").read_text(encoding="utf-8"))
    source_hash = hashlib.sha256("".join(sorted(s["sha256"] for s in sources.values())).encode()).hexdigest()
    data = FixtureData(version_tag, uid("dataset", "fixture"), uid("version", version_tag), source_hash)
    for name in sources:
        text = (FIX / "raw" / name).read_text(encoding="utf-8")
        data.docs[name] = Doc(uid(version_tag, "doc", name), name, text,
                              hashlib.sha256(text.encode("utf-8")).hexdigest(), text.split("\n"))

    def span(doc: Doc, ls: int, le: int, cs: int, ce: int) -> SpanRec:
        return SpanRec(uid(version_tag, "span", doc.name, cs, ce), doc, ls, le, cs, ce)

    by_key: dict[str, FactRec] = {}
    for r in csv.DictReader(open(FIX / "expected_facts.csv", encoding="utf-8")):
        doc = data.docs[r["document_name"]]
        if r["entity_label"] and r["entity_label"] not in data.entities:
            data.entities[r["entity_label"]] = uid(version_tag, "entity", r["entity_label"])
        f = FactRec(
            key=r["fact_key"], fact_id=uid(version_tag, "fact", r["fact_key"]), entity_label=r["entity_label"] or None,
            metric=r["metric_hint"], metric_label=r["metric_label"] or None, role=r["role"], basis=r["basis"] or None,
            value=_f(r["value"]), value_low=_f(r["value_low"]), value_high=_f(r["value_high"]),
            value_text=r["value_text"] or None, original_value=r["original_value"], unit=r["unit"],
            currency=r["currency"] or None, scale=float(r["scale"]), is_estimate=r["is_estimate"] == "true",
            period_label=r["period_label"] or None, period_type=r["period_type"],
            period_end=date.fromisoformat(r["period_end"]) if r["period_end"] else None,
            span=span(doc, int(r["line_start"]), int(r["line_end"]), int(r["char_start"]), int(r["char_end"])))
        data.facts.append(f)
        by_key[f.key] = f

    # aliases, from the documents only: "| IVN | Ivanhoe Mines |" rows and "### IVN — Ivanhoe Mines Ltd" headings
    for label in data.entities:
        data.aliases[label] = {label.lower()}
    for doc in data.docs.values():
        for line in doc.lines:
            m = re.match(r"^\| ([A-Z][A-Z.]+) \| ([A-Za-z][^|]+?) \|", line) or \
                re.match(r"^### ([A-Z][A-Z.]+) — (.+?)(?: — .*)?(?:\s*\{#.*\})?$", line)
            if m and m.group(1) in data.entities:
                data.aliases[m.group(1)].add(m.group(2).strip().lower())

    # chunks: blank-line-separated blocks that are not table rows
    for doc in data.docs.values():
        block: list[int] = []
        for i, line in enumerate(doc.lines + [""], start=1):
            if line.strip() and not line.startswith("|"):
                block.append(i)
                continue
            if block:
                ls, le = block[0], block[-1]
                cs = doc.offset(ls)
                ce = cs + len("\n".join(doc.lines[ls - 1: le]))
                if ce > cs:
                    sp = span(doc, ls, le, cs, ce)
                    data.chunks.append(ChunkRec(uid(version_tag, "chunk", doc.name, ls), sp.exact_text, sp))
                block = []

    findings = json.loads((FIX / "expected_findings.json").read_text(encoding="utf-8"))["findings"]
    for fd in findings:
        keys = [fd["claim_fact"]] + [k for k in fd["evidence_facts"] if k != fd["claim_fact"]]
        facts = [by_key[k] for k in keys]
        data.findings.append(FindingRec(uid(version_tag, "finding", fd["finding_key"]), fd["rule"], fd["severity"],
                                        fd["explanation"], [f.fact_id for f in facts], [f.span for f in facts],
                                        fd["expected"], fd["observed"]))
    return data
