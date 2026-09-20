/**
 * Types for the harness API, mirroring `contracts/models.py`.
 *
 * The API serialises with `response_model_exclude_none`, so anything optional in the contract is
 * ABSENT from the payload rather than `null`. Every such field is typed `?:` here, never `| null`.
 */

export type AnswerStatus = "answered" | "partial" | "conflict" | "declined";

export type EvidenceStatus = "fully_supported" | "partial_support" | "conflicting" | "unsupported";

export type DeclineReason =
  | "insufficient_evidence"
  | "false_premise"
  | "incompatible_currency"
  | "ambiguous_period_or_basis"
  | "out_of_corpus_entity"
  | "future_data"
  | "budget_exhausted";

export type EvidenceKind = "chunk" | "table_cell" | "fact";

export type Unit =
  | "money" | "pct" | "bps" | "x" | "count" | "rank" | "quantity" | "ratio" | "score" | "text" | "unknown";

export type FindingRule =
  | "unit_currency_mix" | "arithmetic" | "rank_order" | "count_claim"
  | "trend_direction" | "duplicate_claim" | "period_order";

export interface Period {
  label?: string;
  end?: string;
  type?: string;
}

export interface SourceSpan {
  span_id?: string;
  document_id?: string;
  document_name: string;
  document_hash: string;
  line_start: number;
  line_end: number;
  char_start: number;
  char_end: number;
  /** Verbatim source text, verified character-for-character. Never edit or reflow it. */
  exact_text: string;
  heading_path: string[];
}

export interface Citation {
  citation_id: number;
  span: SourceSpan;
  evidence_kind: EvidenceKind;
  evidence_id?: string;
  verified: true;
}

export interface AnswerValue {
  label: string;
  value?: number;
  value_low?: number;
  value_high?: number;
  value_text?: string;
  unit: Unit;
  currency?: string;
  period?: Period;
  citation_ids: number[];
}

export interface ConflictClaim {
  text: string;
  citation_ids: number[];
}

export interface ConflictRef {
  finding_id?: string;
  rule: FindingRule;
  explanation: string;
  claims: ConflictClaim[];
}

export interface Operand {
  name: string;
  value: number;
  unit: Unit;
  currency?: string;
  period: Period;
  span: SourceSpan;
}

export interface CalculationResult {
  operation: string;
  formula: string;
  operands: Operand[];
  result?: number;
  result_items: string[];
  unit: Unit;
  currency?: string;
  rounding: number;
  rejected: boolean;
  rejection_reason?: string;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  embedding_tokens: number;
  cost_usd: number;
  latency_ms: number;
  tool_rounds: number;
}

export interface Provenance {
  contract_version: string;
  dataset_id: string;
  source_hash: string;
  parser_version: string;
  prompt_version: string;
  model: string;
  embedding_model?: string;
  usage: Usage;
}

export interface AnswerResponse {
  run_id: string;
  status: AnswerStatus;
  /** Markdown; every material claim carries a `[n]` marker matching a citation_id. */
  answer: string;
  values: AnswerValue[];
  citations: Citation[];
  calculation_trace: CalculationResult[];
  conflicts: ConflictRef[];
  limitations: string[];
  decline_reason?: DeclineReason;
  evidence_status: EvidenceStatus;
  dataset_version: string;
  provenance: Provenance;
}

export interface DatasetSummary {
  dataset_id: string;
  dataset_version_id: string;
  name: string;
  version_no: number;
  status: string;
  source_type: string;
  source_hash: string;
  parser_version: string;
  created_at: string;
  ready_at?: string;
  documents: number;
  facts: number;
  findings: number;
}

export interface RunSummary {
  run_id: string;
  dataset_version_id: string;
  question: string;
  status?: AnswerStatus;
  evidence_status?: EvidenceStatus;
  session_id?: string;
  created_at: string;
  completed_at?: string;
  latency_ms?: number;
}

export interface ConversationSummary {
  session_id: string;
  title: string;
  run_count: number;
  updated_at: string;
}

/** One step the engine took, as streamed by `POST /v1/queries/stream`. */
export interface Step {
  seq: number;
  kind: "policy" | "tool_call" | "llm" | "verify";
  name: string;
  detail?: string;
  latency_ms?: number;
}

export type SourceType = "mcp" | "file";

export type DatasetStatus = "pending" | "ingesting" | "validating" | "ready" | "failed";

export interface ProfileEntry {
  label: string;
  count: number;
}

export interface DocumentReport {
  document_id: string;
  name: string;
  sha256: string;
  lines: number;
  chunks: number;
  tables: number;
  table_cells: number;
  facts: number;
  untyped_cells: number;
  warnings: string[];
}

export interface DatasetProfile {
  dataset_id: string;
  dataset_version_id: string;
  status: DatasetStatus;
  source_hash: string;
  parser_version: string;
  documents: DocumentReport[];
  entities: ProfileEntry[];
  metrics: ProfileEntry[];
  periods: ProfileEntry[];
  currencies: ProfileEntry[];
  units: ProfileEntry[];
  /** Typed cells over all cells, 0–1. */
  table_coverage: number;
  extraction_warnings: string[];
  findings_by_rule: Partial<Record<FindingRule, number>>;
}

export interface IngestRequest {
  source: SourceType;
  path?: string;
  /** Optional MCP endpoint; omitted uses the API's configured default. */
  mcp_url?: string;
  mcp_tool?: string;
  dataset_name: string;
  idempotency_key?: string;
}

/** Defaults the API exposes for the load form. */
export interface AppConfig {
  mcp_url: string | null;
  mcp_tool: string | null;
  dataset_name: string | null;
}

export interface IngestReport {
  job_id: string;
  dataset_id: string;
  dataset_version_id?: string;
  status: DatasetStatus;
  source: SourceType;
  source_hash?: string;
  /** True when the corpus was unchanged and an existing ready version was reused. */
  reused: boolean;
  parser_version: string;
  mcp_capabilities?: Record<string, unknown>;
  documents: DocumentReport[];
  findings: number;
  warnings: string[];
  errors: string[];
  /** Whatever the run recorded, e.g. parse, embed, total. */
  timings_ms: Record<string, number>;
}
