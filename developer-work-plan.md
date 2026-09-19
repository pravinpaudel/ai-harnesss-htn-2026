# Finance Research Harness — Two-Developer Work Plan

This plan divides the backend/CLI harness into two independent lanes. The UI is
explicitly out of scope. The developers collaborate on contracts first, then
integrate through stable interfaces rather than importing each other's internal
modules.

## Shared decisions

- Runtime: Python 3.12, FastAPI, Typer, PostgreSQL 16 with pgvector, and Docker
  Compose.
- No Redis: background jobs are claimed by a Python worker from PostgreSQL.
- LLM: OpenAI Responses API with bounded, typed custom tools.
- Source of truth: raw corpus snapshots and verified source spans; model output
  and memory are never source evidence.
- Every answer is scoped to one immutable `dataset_version`.

## Work split

| Area | Developer A — Evidence Platform | Developer B — Research Engine |
|---|---|---|
| Owns | Ingestion, document model, extraction, facts, embeddings, validations | API/CLI query surface, retrieval, tool orchestration, answer policy, audit/evaluation |
| Reads | Source adapters, parser output, fact/chunk/finding tables | Published repositories/interfaces from Developer A |
| Must not own | LLM prompt/agent decisions, answer composition, query HTTP routes | Source parsing, raw-document mutation, extraction heuristics |
| Main deliverable | A ready, versioned, validated evidence store | A cited, reproducible answer over a ready evidence store |

## Shared contract — complete before parallel work

Create and version `contracts/` together before implementation. Changes require
agreement from both developers and a migration/version bump.

### 1. Database and repository contract

Developer A authors the migrations; Developer B reviews the query requirements.
The required stable records are:

```text
dataset, dataset_version, document, source_span,
chunk, table, table_cell, entity, entity_alias, fact,
validation_finding, job, research_memory, answer_run, tool_event
```

The contract must define:

- IDs, foreign keys, lifecycle states, indexes, and dataset-version isolation
- `fact` fields: entity, metric, value/range, original value, unit, currency,
  scale, basis, role, period label/end, estimate flag, source span
- `source_span` convention: UTF-8 character offsets plus one-based line ranges
  and exact text; document hash is required
- status enums for datasets, jobs, validation findings, and answers
- read-only repository interfaces available to Developer B

### 2. Python service contract

Define Pydantic models in `contracts/models.py`:

- `IngestRequest`, `IngestReport`, `DatasetProfile`
- `FactFilter`, `EvidenceHit`, `CalculationRequest`, `CalculationResult`
- `ValidationFinding`, `AnswerRequest`, `AnswerResponse`, `Citation`
- `EvaluationCase`, `EvaluationReport`

The answer response must include:

```text
run_id, status, answer, values, citations, calculation_trace,
conflicts, limitations, evidence_status, dataset_version, provenance
```

### 3. Fixture contract

Build one small fixture corpus from the supplied reports. It must include:

- At least two entities, factual table cells, narrative chunks, a calculation,
  one conflicting claim, mixed currencies, and an unsupported question
- Raw documents with stable line references
- Expected typed facts and validation findings
- Ten evaluation questions covering lookup, comparison, calculation, narrative,
  conflict, currency ambiguity, and decline behavior

The fixture is the only shared development dependency until the first
integration checkpoint.

## Developer A — Evidence Platform

### A1. Platform and source ingestion

1. Create Docker Compose services for PostgreSQL/pgvector, API, and worker.
2. Configure SQLAlchemy, Alembic, typed settings, source-storage abstraction,
   and database health checks.
3. Implement the PostgreSQL job repository with idempotency keys, state
   transitions, retries for transient failures, and `FOR UPDATE SKIP LOCKED`
   job claiming.
4. Implement `SourceAdapter` and source snapshots:
   - MCP capability discovery and retrieval adapter
   - local Markdown/CSV/JSON/XLSX/PDF file adapter
   - immutable raw payload storage and SHA-256 hashing
5. Produce `IngestReport` with documents, warnings, timings, hash, and errors.

### A2. Document and evidence extraction

1. Canonicalize input into line-preserving text while retaining original files.
2. Parse headings, prose blocks, lists, details/summary blocks, generic tables,
   header hierarchies, rows, cells, and source spans.
3. Persist all raw evidence, including uncertain tables/cells. Failure to type a
   value may reduce structured-query coverage but must not discard citable text.
4. Extract conservative table/key-value facts with original strings intact.
5. Normalize numeric values, ranges, signs, scales, units, currencies,
   qualifiers, estimate markers, roles, and fiscal/calendar periods.
6. Create dataset-scoped entities and aliases from document content only.
7. Generate chunk embeddings and PostgreSQL full-text indexes after the raw
   store has committed successfully.

### A3. Validation and quality reporting

Implement versioned deterministic validation rules:

- incompatible units/currencies within a comparison set
- arithmetic and percentage inconsistencies when required operands exist
- rank order inconsistent with its displayed values
- count claims inconsistent with extracted component records
- stated trend direction inconsistent with endpoints
- duplicate claims with matching entity/metric/period/basis but different values
- impossible or ambiguous period ordering

Persist findings with links to facts/spans, severity, rule version, and a
human-readable explanation. Expose `DatasetProfile` with extraction coverage,
untyped cells, entities, metrics, periods, currencies, and findings.

### A4. Developer A tests

- Unit tests for parser blocks/tables/spans and value/period resolvers
- Integration tests from fixture raw file through a ready dataset version
- Hash/version immutability and idempotent-ingest tests
- Known-corpus extraction regression tests for critical tables and source spans
- Validator tests for each documented inconsistency and for false positives
- MCP failure, invalid payload, and partial-document tests

### Developer A definition of done

`htn ingest` can create a queryable, versioned fixture dataset; every persisted
fact/chunk/finding points to a verifiable source span; and Developer B can use
only the published repositories to retrieve facts and evidence.

## Developer B — Research Engine

### B1. Query, retrieval, and calculator layer

1. Implement read-only repositories over the shared database contract.
2. Implement dataset-scoped entity resolution and metric/period discovery.
3. Implement hybrid retrieval:
   - structured fact lookup with metadata filters
   - PostgreSQL lexical search over chunks/table cells
   - pgvector semantic search
   - deterministic candidate merge and reranking
4. Implement deterministic calculator/comparator functions. They must reject
   incompatible currency, unit, basis, and period combinations unless an
   explicit source-backed conversion is supplied.
5. Return formulas, operands, rounding policy, and source citations with every
   calculated value.

### B2. OpenAI tool orchestration and answer policy

1. Implement OpenAI Responses API client configuration using environment
   settings for model and embedding model.
2. Define strict Pydantic/JSON schemas for `inspect_dataset`, `resolve_entity`,
   `find_facts`, `search_evidence`, `get_source_span`, `calculate`,
   `compare_values`, `list_validation_findings`, and `check_coverage`.
3. Add deterministic routing: quantitative questions use facts/calculator;
   narrative questions use hybrid evidence retrieval.
4. Build a bounded agent loop with maximum six tool rounds and configurable
   token, cost, and latency limits.
5. Implement answer policy and citation verifier:
   - verify every displayed quote against exact source text and offsets
   - require cited operands for calculations
   - use `answered`, `conflict`, `declined`, and `partial` statuses correctly
   - replace model confidence with evidence status

### B3. API, CLI, memory, and audit

1. Implement FastAPI routes for dataset lookup, query submission, run trace,
   conflicts, and evaluation. Developer B owns query-facing routes; Developer
   A supplies the ingest worker/service endpoint.
2. Implement Typer commands: `ask`, `dataset show`, `conflicts`, `trace`, and
   `eval`. `ingest` is wired to A's published ingest service.
3. Implement scoped research memory:
   - session summaries and user preferences
   - dataset-scoped saved notes/aliases with evidence links
   - no cross-dataset factual memory by default
4. Persist `answer_run` and `tool_event` records with dataset hash, prompt and
   parser version, tool inputs/outputs, retrieved spans, model, cost, and
   timing.
5. Implement an evaluation runner and report for correctness, citation validity,
   conflict/decline behavior, cost, and latency.

### B4. Developer B tests

- Tool schema and repository tests against the fixture store
- Hybrid retrieval tests for exact, synonym, and narrative wording
- Calculator tests for percentages, rankings, units, basis, and currency guards
- Citation-verifier test that rejects an altered quote/span
- Status-policy tests for conflict, unsupported, future-data, and false-premise
  questions
- Mocked OpenAI tests for tool sequence and budget exhaustion
- API/CLI contract tests and audit-trace replay tests

### Developer B definition of done

Given a ready fixture dataset, `htn ask --json` and `POST /v1/queries` return
the contractually valid cited answer or an honest conflict/decline, with an
auditable run trace and no write access to source evidence.

## Integration checkpoints

### Checkpoint 0 — contracts and fixture

Both developers complete migrations/models, repository method signatures,
answer JSON schema, and fixture before splitting. Run contract tests in CI.

### Checkpoint 1 — first vertical slice

Developer A supplies one ready fixture dataset. Developer B answers:

1. one direct fact lookup;
2. one calculation with operand citations;
3. one conflict question; and
4. one unsupported question.

Do not expand extraction formats or agent behavior until this path passes.

### Checkpoint 2 — known-corpus evaluation

Ingest all three reports and execute the 47-question benchmark. Triage failures
into one of: extraction gap, normalization issue, retrieval miss, calculation
issue, policy/citation failure, or benchmark ambiguity. Fix the owning layer;
do not add ad-hoc question-specific logic.

### Checkpoint 3 — Phase 2 rehearsal

Developer A creates structural variants of the corpus: reordered headings,
changed table columns, changed labels, alternate periods, and missing tables.
Developer B runs the unchanged API/CLI and evaluation suite. No code or prompt
changes are allowed between ingest and test execution.

### Checkpoint 4 — release candidate

Run a clean Docker Compose install, MCP ingestion smoke test, known-corpus
evaluation, cold-start rehearsal, API/CLI smoke tests, and trace replay. Freeze
schema/parser/prompt versions and export the scorecard.

## Integration rules

- Developer A owns writes to raw evidence, extraction tables, and validation
  findings. Developer B treats them as read-only.
- Developer B owns answer runs, tool events, query routes, and answer policy.
- Both developers may add migrations only after reviewing the shared contract.
- Never fix an answer by adding a question-specific parser rule or prompt
  exception. Record the failure class and make a general solution.
- Every feature must preserve dataset-version filtering and source-span
  provenance.
- A citation cannot be displayed until Developer B's verifier confirms it is
  verbatim in Developer A's immutable source snapshot.
- The worker/API boundary is a service interface; no direct import from the
  research engine into parser internals.

## Suggested implementation order

| Window | Developer A | Developer B |
|---|---|---|
| Start | Contracts, schema, fixture, Compose | Contracts, answer schema, fixture questions |
| First slice | File ingest, spans, chunks, facts | Repositories, fact lookup, calculator, CLI ask |
| Second slice | MCP adapter, normalization, embeddings, validators | Hybrid retrieval, OpenAI tools, policy/citation gate |
| Phase 1 finish | Full known-corpus ingest and extraction tests | Evaluation, trace, API, memory |
| Phase 2 finish | Variant-corpus ingest hardening | Cold-start retrieval/policy hardening and scorecard |
