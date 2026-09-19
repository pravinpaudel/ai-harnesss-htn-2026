"""Service and repository interfaces (contract version 1.0).

Developer A implements SourceAdapter, IngestService and EvidenceRepository.
Developer B depends only on these Protocols, never on A's modules. All
EvidenceRepository methods are read-only and every method is scoped to one
dataset_version_id; there is no cross-version query.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from .models import (
    DatasetProfile,
    EvidenceHit,
    Fact,
    FactFilter,
    FindingRule,
    IngestReport,
    IngestRequest,
    SourceSpan,
    ValidationFinding,
)


# ---------------------------------------------------------------------------
# Developer A — ingestion side
# ---------------------------------------------------------------------------

class RawDocument(Protocol):
    name: str
    content: bytes          # exact bytes as received; never modified
    media_type: str         # text/markdown, text/csv, application/json, ...
    metadata: dict


class SourceCapabilities(Protocol):
    source_type: str
    tools: list[dict]       # for MCP: discovered tool names + argument schemas
    notes: list[str]


@runtime_checkable
class SourceAdapter(Protocol):
    def discover(self) -> SourceCapabilities: ...
    def snapshot(self, request: IngestRequest) -> list[RawDocument]: ...


@runtime_checkable
class IngestService(Protocol):
    """Queues and runs ingestion. The CLI/API (Developer B) calls only this."""

    def submit(self, request: IngestRequest) -> UUID:
        """Create an ingest job and return its job_id. Idempotent on request.idempotency_key."""

    def report(self, job_id: UUID) -> IngestReport:
        """Current state of a job: progress, warnings, retries, failure details."""

    def run_sync(self, request: IngestRequest) -> IngestReport:
        """Submit and wait (used by `htn ingest` and tests)."""


# ---------------------------------------------------------------------------
# Developer A implements, Developer B reads
# ---------------------------------------------------------------------------

class Entity(Protocol):
    entity_id: UUID
    label: str
    aliases: list[str]


@runtime_checkable
class EvidenceRepository(Protocol):
    # datasets
    def resolve_version(self, dataset: str | UUID, version: UUID | str = "latest") -> UUID:
        """Return a READY dataset_version_id; raise if none is ready."""

    def profile(self, dataset_version_id: UUID) -> DatasetProfile: ...

    # entities and vocabulary (all discovered from the dataset itself)
    def list_entities(self, dataset_version_id: UUID) -> list[Entity]: ...
    def resolve_entity(self, dataset_version_id: UUID, name: str) -> list[Entity]:
        """Case-insensitive alias match; returns all candidates, best first. Empty if unknown."""
    def list_metrics(self, dataset_version_id: UUID, entity_id: Optional[UUID] = None) -> list[str]: ...
    def list_periods(self, dataset_version_id: UUID, entity_id: Optional[UUID] = None) -> list[str]: ...

    # structured evidence
    def find_facts(self, flt: FactFilter) -> list[Fact]: ...
    def get_fact(self, dataset_version_id: UUID, fact_id: UUID) -> Fact: ...

    # text evidence
    def search_lexical(self, dataset_version_id: UUID, query: str, k: int = 20) -> list[EvidenceHit]: ...
    def search_semantic(self, dataset_version_id: UUID, embedding: list[float], k: int = 20) -> list[EvidenceHit]: ...

    # sources
    def get_span(self, dataset_version_id: UUID, span_id: UUID) -> SourceSpan: ...
    def read_lines(self, dataset_version_id: UUID, document_name: str,
                   line_start: int, line_end: int) -> SourceSpan:
        """Exact text for a line range from the immutable snapshot (used by the citation verifier)."""

    # validation
    def list_findings(self, dataset_version_id: UUID, *, entity_id: Optional[UUID] = None,
                      fact_ids: Optional[list[UUID]] = None,
                      rules: Optional[list[FindingRule]] = None) -> list[ValidationFinding]: ...
