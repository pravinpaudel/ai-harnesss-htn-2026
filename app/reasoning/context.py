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

    def fact_by_handle(self, handle: str) -> Optional[Fact]:
        ev = self.evidence.get(handle)
        return ev.fact if ev else None
