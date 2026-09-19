"""Shared service contract — Pydantic v2 models (contract version 1.2).

Both developers import these models; neither redefines them. Developer A produces
IngestReport, DatasetProfile, ValidationFinding and the evidence records behind
EvidenceHit. Developer B produces AnswerResponse, CalculationResult and
EvaluationReport. Changing a field needs both developers and a CONTRACT_VERSION bump.

Conventions (see contracts/README.md for the full text):
  * Source spans use 1-based inclusive line ranges AND 0-based, end-exclusive
    character offsets into the document's canonical UTF-8 text. `exact_text` must
    equal text[char_start:char_end]. The document hash is always carried with a span.
  * Every evidence object is scoped to exactly one dataset_version_id.
  * Money/number values are fully expanded (5,292M -> 5292000000); percentages are
    stored as percent points (58% -> 58). `original_value` keeps the source string.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.2"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Enums (mirrored as PostgreSQL enums in schema.sql)
# ---------------------------------------------------------------------------

class DatasetStatus(str, Enum):
    pending = "pending"
    ingesting = "ingesting"
    validating = "validating"
    ready = "ready"
    failed = "failed"


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    retrying = "retrying"


class SourceType(str, Enum):
    mcp = "mcp"
    file = "file"


class Role(str, Enum):
    actual = "actual"
    estimate = "estimate"
    guidance = "guidance"
    target = "target"
    rank = "rank"
    count = "count"
    trend = "trend"
    attribute = "attribute"
    event = "event"


class Basis(str, Enum):
    gaap = "gaap"
    adjusted = "adjusted"
    non_gaap = "non_gaap"


class Unit(str, Enum):
    money = "money"
    pct = "pct"
    bps = "bps"
    multiple = "x"
    count = "count"
    rank = "rank"
    quantity = "quantity"
    ratio = "ratio"
    score = "score"
    text = "text"
    unknown = "unknown"


class PeriodType(str, Enum):
    fiscal = "fiscal"
    calendar = "calendar"
    trailing = "trailing"
    point = "point"
    range = "range"
    unspecified = "unspecified"


class Severity(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class FindingRule(str, Enum):
    unit_currency_mix = "unit_currency_mix"          # incompatible units/currencies in one comparison set
    arithmetic = "arithmetic"                        # arithmetic / percentage inconsistency
    rank_order = "rank_order"                        # rank order inconsistent with displayed values
    count_claim = "count_claim"                      # N/M claim inconsistent with component records
    trend_direction = "trend_direction"              # arrow/word inconsistent with endpoints
    duplicate_claim = "duplicate_claim"              # same entity/metric/period/basis, different values
    period_order = "period_order"                    # impossible or ambiguous period ordering


class FindingStatus(str, Enum):
    open = "open"
    dismissed = "dismissed"


class AnswerStatus(str, Enum):
    answered = "answered"
    conflict = "conflict"
    declined = "declined"
    partial = "partial"


class EvidenceStatus(str, Enum):
    fully_supported = "fully_supported"
    conflicting = "conflicting"
    partial_support = "partial_support"
    unsupported = "unsupported"


class DeclineReason(str, Enum):
    insufficient_evidence = "insufficient_evidence"
    false_premise = "false_premise"
    incompatible_currency = "incompatible_currency"
    ambiguous_period_or_basis = "ambiguous_period_or_basis"
    out_of_corpus_entity = "out_of_corpus_entity"
    future_data = "future_data"
    budget_exhausted = "budget_exhausted"


class EvidenceKind(str, Enum):
    chunk = "chunk"
    table_cell = "table_cell"
    fact = "fact"


class QuestionCategory(str, Enum):
    lookup = "lookup"
    comparison = "comparison"
    calculation = "calculation"
    narrative = "narrative"
    cross_report = "cross_report"
    conflict = "conflict"
    currency_ambiguity = "currency_ambiguity"
    decline = "decline"
    false_premise = "false_premise"
    identification = "identification"   # "which company..." questions answered from several clues (v1.1)


# ---------------------------------------------------------------------------
# Provenance primitives
# ---------------------------------------------------------------------------

class SourceSpan(_Model):
    span_id: Optional[UUID] = None           # null only in fixtures/expectations
    document_id: Optional[UUID] = None
    document_name: str                       # original file name or MCP document name
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    exact_text: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> "SourceSpan":
        if self.line_end < self.line_start or self.char_end <= self.char_start:
            raise ValueError("span end must follow span start")
        if self.char_end - self.char_start != len(self.exact_text):
            raise ValueError("exact_text length must equal char_end - char_start")
        return self


class FactValue(_Model):
    value: Optional[float] = None            # null for text values and pure ranges
    value_low: Optional[float] = None
    value_high: Optional[float] = None
    value_text: Optional[str] = None
    original_value: str                      # verbatim source string, always kept
    unit: Unit
    unit_label: Optional[str] = None         # "t Cu", "/oz", "GEO" ...
    currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")  # null = not stated
    scale: float = 1.0                       # as written: 1, 1e3, 1e6, 1e9
    is_estimate: bool = False
    qualifier: Optional[str] = None          # "adj", "(Est)", "incl CP" ...


class Period(_Model):
    label: Optional[str] = None              # as written: "Q3 FY2026 (Jul 31, 2026)"
    end: Optional[date] = None               # resolved only when source-backed
    type: PeriodType = PeriodType.unspecified


class Fact(_Model):
    fact_id: UUID
    dataset_version_id: UUID
    entity_id: Optional[UUID]
    entity_label: Optional[str]              # canonical label as found in the dataset
    metric: str                              # dataset-scoped normalized metric key
    metric_label: Optional[str]              # label as written, if any
    value: FactValue
    basis: Optional[Basis] = None
    role: Role
    period: Period
    span: SourceSpan
    extraction_confidence: float = Field(ge=0, le=1)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class IngestRequest(_Model):
    source: SourceType
    path: Optional[str] = None               # required when source=file
    mcp_tool: Optional[str] = None           # null = discover
    dataset_name: str
    idempotency_key: Optional[str] = None

    @model_validator(mode="after")
    def _path_for_files(self) -> "IngestRequest":
        if self.source == SourceType.file and not self.path:
            raise ValueError("path is required for file ingestion")
        return self


class DocumentReport(_Model):
    document_id: UUID
    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lines: int
    chunks: int
    tables: int
    table_cells: int
    facts: int
    untyped_cells: int
    warnings: list[str] = Field(default_factory=list)


class IngestReport(_Model):
    job_id: UUID
    dataset_id: UUID
    dataset_version_id: Optional[UUID]       # null if the job failed before a version existed
    status: DatasetStatus
    source: SourceType
    source_hash: Optional[str] = None        # sha256 over sorted document hashes
    reused: bool = False                      # true when an identical ready version was reused
    parser_version: str
    mcp_capabilities: Optional[dict] = None  # tools, argument schema, payload type as discovered
    documents: list[DocumentReport] = Field(default_factory=list)
    findings: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)   # snapshot, parse, embed, validate, total


class ProfileEntry(_Model):
    label: str
    count: int


class DatasetProfile(_Model):
    dataset_id: UUID
    dataset_version_id: UUID
    status: DatasetStatus
    source_hash: str
    parser_version: str
    documents: list[DocumentReport]
    entities: list[ProfileEntry]             # label, fact count
    metrics: list[ProfileEntry]
    periods: list[ProfileEntry]
    currencies: list[ProfileEntry]
    units: list[ProfileEntry]
    table_coverage: float = Field(ge=0, le=1)   # typed cells / all cells
    extraction_warnings: list[str]
    findings_by_rule: dict[FindingRule, int]


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

class FactFilter(_Model):
    dataset_version_id: UUID
    entity_ids: list[UUID] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    metric_text: Optional[str] = None        # fuzzy match on metric / metric_label
    roles: list[Role] = Field(default_factory=list)
    bases: list[Basis] = Field(default_factory=list)
    period_labels: list[str] = Field(default_factory=list)
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    currencies: list[str] = Field(default_factory=list)
    units: list[Unit] = Field(default_factory=list)
    include_estimates: bool = True
    limit: int = Field(default=50, ge=1, le=500)


class EvidenceHit(_Model):
    kind: EvidenceKind
    evidence_id: UUID
    dataset_version_id: UUID
    text: str
    score: float
    score_components: dict[str, float] = Field(default_factory=dict)  # lexical, semantic, metadata, specificity
    span: SourceSpan
    fact: Optional[Fact] = None


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

class Operation(str, Enum):
    difference = "difference"
    percent_change = "percent_change"
    ratio = "ratio"
    sum = "sum"
    average = "average"
    median = "median"
    rank = "rank"
    compare = "compare"


class Operand(_Model):
    name: str
    fact_id: Optional[UUID] = None           # preferred
    value: float
    unit: Unit
    currency: Optional[str] = None
    basis: Optional[Basis] = None
    period: Period
    span: SourceSpan                         # every operand is cited


class CalculationRequest(_Model):
    operation: Operation
    operands: list[Operand] = Field(min_length=1)
    rounding: int = Field(default=2, ge=0, le=10)
    allow_mixed_currency: bool = False       # only with a source-backed conversion operand


class CalculationResult(_Model):
    operation: Operation
    formula: str                             # e.g. "(152.6M - 165.5M) / 165.5M * 100"
    operands: list[Operand]
    result: Optional[float]                  # null when a guard rejected the request
    result_items: list[str] = Field(default_factory=list)   # for rank/compare
    unit: Unit
    currency: Optional[str] = None
    rounding: int
    rejected: bool = False
    rejection_reason: Optional[str] = None   # currency/unit/basis/period mismatch


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationFinding(_Model):
    finding_id: UUID
    dataset_version_id: UUID
    rule: FindingRule
    rule_version: str
    severity: Severity
    status: FindingStatus = FindingStatus.open
    explanation: str                         # one human-readable sentence
    fact_ids: list[UUID]
    spans: list[SourceSpan] = Field(min_length=1)
    expected: Optional[str] = None           # recomputed value
    observed: Optional[str] = None           # value asserted by the document


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

class Budget(_Model):
    max_tool_rounds: int = Field(default=6, ge=1, le=6)
    max_tokens: int = 60_000
    max_cost_usd: float = 0.50
    max_latency_ms: int = 60_000


class AnswerRequest(_Model):
    question: str = Field(min_length=1)
    dataset_version: UUID | Literal["latest"] = "latest"
    session_id: Optional[str] = None
    budget: Budget = Budget()


class Citation(_Model):
    citation_id: int = Field(ge=1)           # [n] marker used in `answer`
    span: SourceSpan
    evidence_kind: EvidenceKind
    evidence_id: Optional[UUID] = None
    verified: Literal[True]                  # unverified citations are never emitted


class AnswerValue(_Model):
    label: str
    value: Optional[float] = None
    value_low: Optional[float] = None
    value_high: Optional[float] = None
    value_text: Optional[str] = None
    unit: Unit
    currency: Optional[str] = None
    period: Optional[Period] = None
    citation_ids: list[int] = Field(min_length=1)


class ConflictClaim(_Model):
    text: str
    citation_ids: list[int] = Field(min_length=1)


class ConflictRef(_Model):
    finding_id: Optional[UUID]               # null if detected at query time only
    rule: FindingRule
    explanation: str
    claims: list[ConflictClaim] = Field(min_length=2)


class Usage(_Model):
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    tool_rounds: int = 0


class Provenance(_Model):
    contract_version: str = CONTRACT_VERSION
    dataset_id: UUID
    source_hash: str
    parser_version: str
    prompt_version: str
    model: str
    embedding_model: Optional[str] = None
    usage: Usage


class AnswerResponse(_Model):
    run_id: UUID
    status: AnswerStatus
    answer: str                              # every material claim carries [n] markers
    values: list[AnswerValue] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    calculation_trace: list[CalculationResult] = Field(default_factory=list)
    conflicts: list[ConflictRef] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    decline_reason: Optional[DeclineReason] = None
    evidence_status: EvidenceStatus
    dataset_version: UUID
    provenance: Provenance

    @model_validator(mode="after")
    def _policy(self) -> "AnswerResponse":
        ids = {c.citation_id for c in self.citations}
        if self.status == AnswerStatus.answered and not self.citations:
            raise ValueError("answered requires at least one citation")
        if self.status == AnswerStatus.conflict and not self.conflicts:
            raise ValueError("conflict requires at least one ConflictRef")
        if self.status == AnswerStatus.declined and self.decline_reason is None:
            raise ValueError("declined requires decline_reason")
        if self.status == AnswerStatus.partial and not self.limitations:
            raise ValueError("partial requires limitations describing the unsupported part")
        for v in self.values:
            if not set(v.citation_ids) <= ids:
                raise ValueError(f"value {v.label!r} cites unknown citation ids")
        for c in self.conflicts:
            for claim in c.claims:
                if not set(claim.citation_ids) <= ids:
                    raise ValueError("conflict claim cites unknown citation ids")
        for calc in self.calculation_trace:
            if not calc.rejected and calc.result is None:
                raise ValueError("non-rejected calculation must have a result")
        return self


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class SpanAnchor(_Model):
    """A citation the answer must include: any citation overlapping these lines of this document."""
    document_name: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)


class ExpectedValue(_Model):
    label: str
    value: Optional[float] = None
    value_text: Optional[str] = None
    currency: Optional[str] = None
    tolerance: float = 0.0                   # absolute, in the value's own unit


class Expectation(_Model):
    acceptable_statuses: list[AnswerStatus] = Field(min_length=1)
    decline_reasons: list[DeclineReason] = Field(default_factory=list)
    values: list[ExpectedValue] = Field(default_factory=list)
    ordered_items: list[str] = Field(default_factory=list)
    must_cite: list[SpanAnchor] = Field(default_factory=list)
    must_cite_any: list[SpanAnchor] = Field(default_factory=list)   # v1.2: at least one of these must be cited
    must_mention: list[str] = Field(default_factory=list)       # case-insensitive substrings of `answer`
    finding_rules: list[FindingRule] = Field(default_factory=list)
    requires_calculation: bool = False


class EvaluationCase(_Model):
    case_id: str
    question: str
    category: QuestionCategory
    expect: Expectation
    notes: Optional[str] = None


class CaseResult(_Model):
    case_id: str
    category: QuestionCategory
    run_id: Optional[UUID]
    passed: bool
    status_ok: bool
    values_ok: bool
    citations_valid: bool                    # every displayed quote verified verbatim
    anchors_hit: bool
    failure_class: Optional[Literal[
        "extraction_gap", "normalization_issue", "retrieval_miss",
        "calculation_issue", "policy_or_citation_failure", "benchmark_ambiguity",
    ]] = None
    usage: Optional[Usage] = None
    detail: Optional[str] = None


class EvaluationReport(_Model):
    report_id: UUID
    dataset_version_id: UUID
    suite: str
    started_at: datetime
    finished_at: datetime
    cases: list[CaseResult]
    accuracy_by_category: dict[QuestionCategory, float]
    citation_validity: float = Field(ge=0, le=1)
    citation_precision: Optional[float] = Field(default=None, ge=0, le=1)
    conflict_recall: float = Field(ge=0, le=1)
    unsupported_answer_rate: float = Field(ge=0, le=1)
    mean_latency_ms: float
    p95_latency_ms: float
    mean_tool_rounds: float
    mean_cost_usd: float
    baseline: Optional[dict[str, float]] = None   # whole-corpus prompt baseline metrics
