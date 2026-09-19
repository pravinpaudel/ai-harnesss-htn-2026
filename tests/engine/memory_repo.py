"""In-memory EvidenceRepository over the contract fixture (test-only).

Mirrors PgEvidenceRepository's behaviour closely enough for engine unit tests that do not
need PostgreSQL. The PostgreSQL repository itself is tested in test_repository_pg.py.
"""

from __future__ import annotations

import re
from typing import Optional
from uuid import UUID

from contracts.models import (
    Basis, DatasetProfile, DatasetStatus, DocumentReport, EvidenceHit, EvidenceKind, Fact, FactFilter, FactValue,
    FindingRule, Period, PeriodType, ProfileEntry, Role, SourceSpan, Unit, ValidationFinding,
)
from app.retrieval.repository import EntityRecord, VersionInfo, span_from_lines
from tests.engine.fixture_data import FactRec, FixtureData, SpanRec, load


def _span(s: SpanRec) -> SourceSpan:
    return SourceSpan(span_id=s.span_id, document_id=s.doc.document_id, document_name=s.doc.name,
                      document_hash=s.doc.sha256, line_start=s.line_start, line_end=s.line_end,
                      char_start=s.char_start, char_end=s.char_end, exact_text=s.exact_text,
                      heading_path=s.doc.heading_path(s.line_start))


class MemoryEvidenceRepository:
    def __init__(self, data: Optional[FixtureData] = None, tamper: Optional[dict[str, str]] = None):
        self.data = data or load()
        self.tamper = tamper or {}   # document name -> replacement text (simulates a changed snapshot)

    # datasets
    def version_info(self, dataset="latest", version="latest") -> VersionInfo:
        d = self.data
        if version not in ("latest", d.dataset_version_id, str(d.dataset_version_id)):
            raise LookupError(f"no ready dataset version {version}")
        return VersionInfo(d.dataset_id, d.dataset_version_id, "fixture", d.source_hash, "fixture-seed")

    def resolve_version(self, dataset="latest", version="latest") -> UUID:
        return self.version_info(dataset, version).dataset_version_id

    def _check(self, v: UUID) -> None:
        if v != self.data.dataset_version_id:
            raise LookupError(f"unknown dataset version {v}")

    def profile(self, dataset_version_id: UUID) -> DatasetProfile:
        self._check(dataset_version_id)
        d = self.data

        def count(key) -> list[ProfileEntry]:
            c: dict[str, int] = {}
            for f in d.facts:
                k = key(f)
                if k:
                    c[k] = c.get(k, 0) + 1
            return [ProfileEntry(label=k, count=v) for k, v in sorted(c.items(), key=lambda x: (-x[1], x[0]))]

        rules: dict[FindingRule, int] = {}
        for f in d.findings:
            rules[FindingRule(f.rule)] = rules.get(FindingRule(f.rule), 0) + 1
        return DatasetProfile(
            dataset_id=d.dataset_id, dataset_version_id=d.dataset_version_id, status=DatasetStatus.ready,
            source_hash=d.source_hash, parser_version="fixture-seed",
            documents=[DocumentReport(document_id=doc.document_id, name=doc.name, sha256=doc.sha256,
                                      lines=len(doc.lines), chunks=sum(c.span.doc is doc for c in d.chunks),
                                      tables=0, table_cells=0, facts=sum(f.span.doc is doc for f in d.facts),
                                      untyped_cells=0) for doc in d.docs.values()],
            entities=count(lambda f: f.entity_label), metrics=count(lambda f: f.metric),
            periods=count(lambda f: f.period_label), currencies=count(lambda f: f.currency),
            units=count(lambda f: f.unit), table_coverage=1.0, extraction_warnings=[], findings_by_rule=rules)

    # entities
    def list_entities(self, dataset_version_id: UUID) -> list[EntityRecord]:
        self._check(dataset_version_id)
        return [EntityRecord(eid, label, sorted(self.data.aliases.get(label, {label.lower()})))
                for label, eid in sorted(self.data.entities.items())]

    def resolve_entity(self, dataset_version_id: UUID, name: str) -> list[EntityRecord]:
        q = name.strip().lower()
        ents = self.list_entities(dataset_version_id)
        exact = [e for e in ents if e.label.lower() == q or q in e.aliases]
        part = [e for e in ents if e not in exact and q and any(q in a for a in e.aliases)]
        return exact + part

    def list_metrics(self, dataset_version_id, entity_id=None) -> list[str]:
        return sorted({f.metric for f in self._facts(entity_id)})

    def list_periods(self, dataset_version_id, entity_id=None) -> list[str]:
        return sorted({f.period_label for f in self._facts(entity_id) if f.period_label})

    def _facts(self, entity_id=None) -> list[FactRec]:
        return [f for f in self.data.facts
                if entity_id is None or self.data.entities.get(f.entity_label or "") == entity_id]

    # facts
    def _fact(self, f: FactRec) -> Fact:
        return Fact(
            fact_id=f.fact_id, dataset_version_id=self.data.dataset_version_id,
            entity_id=self.data.entities.get(f.entity_label or ""), entity_label=f.entity_label, metric=f.metric,
            metric_label=f.metric_label,
            value=FactValue(value=f.value, value_low=f.value_low, value_high=f.value_high, value_text=f.value_text,
                            original_value=f.original_value, unit=Unit(f.unit), currency=f.currency, scale=f.scale,
                            is_estimate=f.is_estimate),
            basis=Basis(f.basis) if f.basis else None, role=Role(f.role),
            period=Period(label=f.period_label, end=f.period_end, type=PeriodType(f.period_type)),
            span=_span(f.span), extraction_confidence=1.0)

    def find_facts(self, flt: FactFilter) -> list[Fact]:
        self._check(flt.dataset_version_id)
        out = []
        for f in self.data.facts:
            eid = self.data.entities.get(f.entity_label or "")
            if flt.entity_ids and eid not in flt.entity_ids:
                continue
            if flt.metrics and f.metric not in flt.metrics:
                continue
            if flt.metric_text:
                pat = re.compile(".*".join(map(re.escape, flt.metric_text.lower().split())))
                if not (pat.search(f.metric.lower()) or pat.search((f.metric_label or "").lower())):
                    continue
            if flt.roles and Role(f.role) not in flt.roles:
                continue
            if flt.bases and (f.basis is None or Basis(f.basis) not in flt.bases):
                continue
            if flt.period_labels and not any((f.period_label or "").lower().startswith(p.lower())
                                             for p in flt.period_labels):
                continue
            if flt.currencies and f.currency not in flt.currencies:
                continue
            if flt.units and Unit(f.unit) not in flt.units:
                continue
            if not flt.include_estimates and f.is_estimate:
                continue
            out.append(self._fact(f))
        return out[: flt.limit]

    def get_fact(self, dataset_version_id: UUID, fact_id: UUID) -> Fact:
        self._check(dataset_version_id)
        return next(self._fact(f) for f in self.data.facts if f.fact_id == fact_id)

    # text
    def search_lexical(self, dataset_version_id: UUID, query: str, k: int = 20) -> list[EvidenceHit]:
        self._check(dataset_version_id)
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2]
        scored = []
        for c in self.data.chunks:
            body = c.text.lower()
            score = sum(body.count(t[:6]) for t in terms)
            if score:
                scored.append((score, c))
        scored.sort(key=lambda x: (-x[0], x[1].span.doc.name, x[1].span.line_start))
        return [EvidenceHit(kind=EvidenceKind.chunk, evidence_id=c.chunk_id, dataset_version_id=dataset_version_id,
                            text=c.text, score=float(s), score_components={"lexical": float(s)}, span=_span(c.span))
                for s, c in scored[:k]]

    def search_semantic(self, dataset_version_id, embedding, k=20) -> list[EvidenceHit]:
        return []

    # sources
    def get_span(self, dataset_version_id: UUID, span_id: UUID) -> SourceSpan:
        for f in self.data.facts:
            if f.span.span_id == span_id:
                return _span(f.span)
        raise LookupError(span_id)

    def read_lines(self, dataset_version_id: UUID, document_name: str, line_start: int, line_end: int) -> SourceSpan:
        self._check(dataset_version_id)
        doc = self.data.docs[document_name]
        text = self.tamper.get(document_name, doc.text)
        return span_from_lines(document_name, doc.sha256, text, line_start, line_end)

    # findings
    def list_findings(self, dataset_version_id: UUID, *, entity_id=None, fact_ids=None,
                      rules=None) -> list[ValidationFinding]:
        self._check(dataset_version_id)
        out = []
        ent_facts = {f.fact_id for f in self._facts(entity_id)} if entity_id else None
        for f in self.data.findings:
            if fact_ids and not set(fact_ids) & set(f.fact_ids):
                continue
            if rules and FindingRule(f.rule) not in rules:
                continue
            if ent_facts is not None and not ent_facts & set(f.fact_ids):
                continue
            out.append(ValidationFinding(
                finding_id=f.finding_id, dataset_version_id=dataset_version_id, rule=FindingRule(f.rule),
                rule_version="fixture", severity=f.severity, explanation=f.explanation, fact_ids=f.fact_ids,
                spans=[_span(s) for s in f.spans], expected=f.expected, observed=f.observed))
        return out
