import type { AnswerResponse, Citation, DatasetProfile, IngestReport } from "../api/types";

export function citation(overrides: Partial<Citation> & { citation_id: number }): Citation {
  return {
    evidence_kind: "chunk",
    verified: true,
    ...overrides,
    span: {
      document_name: "canadian-financials-research.md",
      document_hash: "a".repeat(64),
      line_start: 1631,
      line_end: 1631,
      char_start: 0,
      char_end: 64,
      exact_text: "NA.TO shares fell 5.83% on the release date, despite the beat.",
      heading_path: ["Company deep dives", "NA"],
      ...overrides.span,
    },
  };
}

export function answer(overrides: Partial<AnswerResponse> = {}): AnswerResponse {
  return {
    run_id: "11111111-1111-1111-1111-111111111111",
    status: "answered",
    answer: "National Bank fell 5.83% on the release date [1].",
    values: [],
    citations: [citation({ citation_id: 1 })],
    calculation_trace: [],
    conflicts: [],
    limitations: [],
    evidence_status: "fully_supported",
    dataset_version: "22222222-2222-2222-2222-222222222222",
    provenance: {
      contract_version: "1.0.0",
      dataset_id: "33333333-3333-3333-3333-333333333333",
      source_hash: "b".repeat(64),
      parser_version: "0.1.0",
      prompt_version: "answer_v1",
      model: "gpt-5.6-luna",
      usage: {
        input_tokens: 9000, output_tokens: 700, embedding_tokens: 0,
        cost_usd: 0.0132, latency_ms: 12400, tool_rounds: 3,
      },
    },
    ...overrides,
  };
}

export function profile(overrides: Partial<DatasetProfile> = {}): DatasetProfile {
  return {
    dataset_id: "33333333-3333-3333-3333-333333333333",
    dataset_version_id: "22222222-2222-2222-2222-222222222222",
    status: "ready",
    source_hash: "b".repeat(64),
    parser_version: "0.1.0",
    documents: [
      {
        document_id: "d1", name: "canadian-financials-research.md", sha256: "c".repeat(64),
        lines: 1943, chunks: 612, tables: 44, table_cells: 1810, facts: 902, untyped_cells: 210, warnings: [],
      },
      {
        document_id: "d2", name: "canadian-mining-research.md", sha256: "d".repeat(64),
        lines: 1930, chunks: 588, tables: 41, table_cells: 1702, facts: 845, untyped_cells: 190, warnings: ["one row skipped"],
      },
    ],
    entities: [{ label: "RY", count: 180 }, { label: "TD", count: 165 }],
    metrics: [{ label: "revenue", count: 220 }],
    periods: [{ label: "Q3 FY2026", count: 310 }],
    currencies: [{ label: "CAD", count: 400 }],
    units: [{ label: "money", count: 500 }],
    table_coverage: 0.87,
    extraction_warnings: [],
    findings_by_rule: { duplicate_claim: 4, rank_order: 1 },
    ...overrides,
  };
}

export function ingestReport(overrides: Partial<IngestReport> = {}): IngestReport {
  return {
    job_id: "job-1",
    dataset_id: "33333333-3333-3333-3333-333333333333",
    dataset_version_id: "22222222-2222-2222-2222-222222222222",
    status: "ready",
    source: "mcp",
    source_hash: "b".repeat(64),
    reused: false,
    parser_version: "0.1.0",
    mcp_capabilities: { tools: ["financialDataRetrieval"] },
    documents: profile().documents,
    findings: 5,
    warnings: [],
    errors: [],
    timings_ms: { parse: 4200, embed: 9100, total: 13400 },
    ...overrides,
  };
}
