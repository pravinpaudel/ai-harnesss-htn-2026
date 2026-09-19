"""Per-run state: the evidence handles the model may cite, and usage accounting.

Tools register every fact, chunk, span, finding and calculation they return under a short
handle (E1, E2, ..., C1, F1). The model only ever cites handles; the answer policy turns
handles back into repository spans, so quote text is never authored by the model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from uuid import UUID, uuid4

from contracts.models import CalculationResult, EvidenceKind, Fact, SourceSpan, ValidationFinding


class EvidenceSource(Protocol):
    """What the engine needs from a repository: the contract Protocol plus version metadata."""

    def version_info(self, dataset: Any = "latest", version: Any = "latest") -> Any: ...
    def profile(self, dataset_version_id: UUID) -> Any: ...
    def list_entities(self, dataset_version_id: UUID) -> list: ...
    def resolve_entity(self, dataset_version_id: UUID, name: str) -> list: ...
    def list_metrics(self, dataset_version_id: UUID, entity_id: Optional[UUID] = None) -> list[str]: ...
    def list_periods(self, dataset_version_id: UUID, entity_id: Optional[UUID] = None) -> list[str]: ...
    def find_facts(self, flt: Any) -> list[Fact]: ...
    def get_fact(self, dataset_version_id: UUID, fact_id: UUID) -> Fact: ...
    def search_lexical(self, dataset_version_id: UUID, query: str, k: int = 20) -> list: ...
    def read_lines(self, dataset_version_id: UUID, document_name: str, line_start: int, line_end: int) -> SourceSpan: ...
    def document_text(self, dataset_version_id: UUID, document_name: str) -> tuple[str, str]: ...
    def list_findings(self, dataset_version_id: UUID, *, entity_id: Optional[UUID] = None,
                      fact_ids: Optional[list[UUID]] = None, rules: Optional[list] = None) -> list[ValidationFinding]: ...


@dataclass
class Evidence:
    handle: str
    kind: EvidenceKind | str          # fact | chunk | table_cell | span
    evidence_id: Optional[UUID]
    span: SourceSpan
    fact: Optional[Fact] = None
    text: str = ""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_rounds: int = 0


@dataclass
class RunContext:
    repo: EvidenceSource
    dataset_id: UUID
    dataset_version_id: UUID
    source_hash: str
    parser_version: str
    question: str
    run_id: UUID = field(default_factory=uuid4)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    calculations: dict[str, CalculationResult] = field(default_factory=dict)
    findings: dict[str, ValidationFinding] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    started: float = field(default_factory=time.monotonic)
    _by_key: dict[tuple, str] = field(default_factory=dict)
    row_cache: dict[tuple, str] = field(default_factory=dict)
    outlines: dict[str, Any] = field(default_factory=dict)
    _labels: Optional[set[str]] = None
    _subjects: Optional[set[str]] = None
    _matcher: Any = None
    embedder: Any = None                 # QueryEmbedder; enables hybrid search when the dataset has embeddings
    _semantic: Optional[bool] = None
    search_mode: str = "lexical"

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def add_evidence(self, kind: EvidenceKind | str, evidence_id: Optional[UUID], span: SourceSpan,
                     fact: Optional[Fact] = None, text: str = "") -> str:
        key = (str(kind), evidence_id or (span.document_name, span.char_start, span.char_end))
        if key in self._by_key:
            return self._by_key[key]
        handle = f"E{len(self.evidence) + 1}"
        self.evidence[handle] = Evidence(handle, kind, evidence_id, span, fact, text)
        self._by_key[key] = handle
        return handle

    def add_calculation(self, calc: CalculationResult) -> str:
        handle = f"C{len(self.calculations) + 1}"
        self.calculations[handle] = calc
        return handle

    def add_finding(self, finding: ValidationFinding) -> str:
        for h, f in self.findings.items():
            if f.finding_id == finding.finding_id:
                return h
        handle = f"F{len(self.findings) + 1}"
        self.findings[handle] = finding
        return handle

    def search(self, query: str, k: int = 20) -> list:
        """Hybrid retrieval: lexical hits, fused with semantic hits (reciprocal-rank fusion) when the
        dataset has chunk embeddings and an embedder is configured. Falls back to lexical on any error."""
        lexical = self.repo.search_lexical(self.dataset_version_id, query, k=k)
        if self.embedder is None:
            return lexical
        if self._semantic is None:
            has = getattr(self.repo, "has_embeddings", None)
            self._semantic = bool(has and has(self.dataset_version_id))
        if not self._semantic:
            return lexical
        try:
            semantic = self.repo.search_semantic(self.dataset_version_id, self.embedder.embed_query(query), k=k)
        except Exception:  # noqa: BLE001 - embedding/API failure must not break answering
            self.search_mode = "lexical (semantic failed)"
            return lexical
        self.search_mode = "hybrid"
        fused: dict[tuple, list] = {}
        for source, hits in (("lexical", lexical), ("semantic", semantic)):
            for rank, h in enumerate(hits):
                key = (h.kind, h.evidence_id)
                entry = fused.setdefault(key, [h, 0.0, {}])
                entry[1] += 1.0 / (60 + rank)
                entry[2][source] = h.score
        ranked = sorted(fused.values(), key=lambda x: -x[1])[:k]
        return [h.model_copy(update={"score": round(score, 6), "score_components": comps}) for h, score, comps in ranked]

    def entity_labels(self) -> set[str]:
        if self._labels is None:
            self._labels = {e.label for e in self.repo.list_entities(self.dataset_version_id)}
        return self._labels

    def subjects(self) -> set[str]:
        """Entities that have their own section in some document (the dataset's real subjects).
        Falls back to every entity when no document has per-entity sections."""
        if self._subjects is None:
            from app.retrieval.context import Outline, entity_from_path

            labels, found = self.entity_labels(), set()
            for doc in self.repo.profile(self.dataset_version_id).documents:
                if doc.name not in self.outlines:
                    body, _ = self.repo.document_text(self.dataset_version_id, doc.name)
                    self.outlines[doc.name] = Outline.parse(body)
                for _, _, heading in self.outlines[doc.name].headings:
                    ent = entity_from_path([heading], labels)
                    if ent:
                        found.add(ent)
            self._subjects = found or labels
        return self._subjects

    def entities_in(self, text: str) -> list[str]:
        """Subjects a text mentions (by label or alias), in order of first appearance."""
        if self._matcher is None:
            from app.retrieval.context import EntityMatcher

            subjects = self.subjects()
            ents = self.repo.list_entities(self.dataset_version_id)
            self._matcher = (EntityMatcher({e.label: list(e.aliases) for e in ents if e.label in subjects}),
                             EntityMatcher({e.label: list(e.aliases) for e in ents if e.label not in subjects}))
        primary, fallback = self._matcher
        return primary.find(text) or fallback.find(text)

    def span_context(self, span: SourceSpan):
        """Heading path, enclosing <details> summary and entity for a span (derived when not stored)."""
        from app.retrieval.context import Outline, SpanContext, entity_from_path

        if span.document_name not in self.outlines:
            body, _ = self.repo.document_text(self.dataset_version_id, span.document_name)
            self.outlines[span.document_name] = Outline.parse(body)
        o = self.outlines[span.document_name]
        path = list(span.heading_path) or o.path(span.line_start)
        block = o.block(span.line_start)
        row_period = None
        if block is None and span.exact_text.lstrip().startswith("|"):
            # a quarterly-table row: "| Q3 FY2026 (Jul 31, 2026) | ..." belongs to that period
            first = span.exact_text.strip().strip("|").split("|", 1)[0].strip()
            row_period = _match_period(first, o.periods())
        return SpanContext(path, block, entity_from_path(path, self.entity_labels()), row_period)

    def fact_by_handle(self, handle: str) -> Optional[Fact]:
        ev = self.evidence.get(handle)
        return ev.fact if ev else None


def _match_period(cell: str, periods: list[str]) -> Optional[str]:
    """The block period a table row's first cell names ('Q3 FY2026 (Jul 31/26)' -> 'Q3 FY2026 (Jul 31, 2026)'),
    matched on the leading label before any parenthesis."""
    head = cell.split("(")[0].strip().lower()
    if not head:
        return None
    for p in periods:
        if p.split("(")[0].strip().lower() == head:
            return p
    return None
