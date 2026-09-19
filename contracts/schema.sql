-- Contract v1.0 — reference PostgreSQL 16 + pgvector schema.
-- Developer A turns this into Alembic migration 0001; Developer B reviews query needs.
-- Any change: both developers agree, bump CONTRACT_VERSION in models.py, add a migration.
--
-- Isolation rule: every evidence row carries dataset_version_id, and every query
-- filters on it. Evidence rows are immutable once their dataset_version is 'ready'.
-- Ownership: Developer A writes everything except answer_run, tool_event and
-- research_memory, which Developer B writes. Each side reads the other's tables only.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE TYPE dataset_status   AS ENUM ('pending', 'ingesting', 'validating', 'ready', 'failed');
CREATE TYPE job_state        AS ENUM ('queued', 'running', 'succeeded', 'failed', 'retrying');
CREATE TYPE source_type      AS ENUM ('mcp', 'file');
CREATE TYPE fact_role        AS ENUM ('actual', 'estimate', 'guidance', 'target', 'rank', 'count',
                                      'trend', 'attribute', 'event');
CREATE TYPE fact_basis       AS ENUM ('gaap', 'adjusted', 'non_gaap');
CREATE TYPE value_unit       AS ENUM ('money', 'pct', 'bps', 'x', 'count', 'rank', 'quantity',
                                      'ratio', 'score', 'text', 'unknown');
CREATE TYPE period_type      AS ENUM ('fiscal', 'calendar', 'trailing', 'point', 'range', 'unspecified');
CREATE TYPE finding_rule     AS ENUM ('unit_currency_mix', 'arithmetic', 'rank_order', 'count_claim',
                                      'trend_direction', 'duplicate_claim', 'period_order');
CREATE TYPE finding_severity AS ENUM ('high', 'medium', 'low');
CREATE TYPE finding_status   AS ENUM ('open', 'dismissed');
CREATE TYPE answer_status    AS ENUM ('answered', 'conflict', 'declined', 'partial');
CREATE TYPE evidence_status  AS ENUM ('fully_supported', 'conflicting', 'partial_support', 'unsupported');

-- ---------------------------------------------------------------- datasets --

CREATE TABLE dataset (
    dataset_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL UNIQUE,
    source_type   source_type NOT NULL,
    source_uri    text NOT NULL,                 -- MCP endpoint + tool, or file path
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dataset_version (
    dataset_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id         uuid NOT NULL REFERENCES dataset,
    version_no         integer NOT NULL,
    status             dataset_status NOT NULL DEFAULT 'pending',
    source_hash        text,                     -- sha256 over sorted document hashes
    parser_version     text NOT NULL,
    mcp_capabilities   jsonb,
    quality_report     jsonb,                    -- DatasetProfile snapshot
    created_at         timestamptz NOT NULL DEFAULT now(),
    ready_at           timestamptz,
    UNIQUE (dataset_id, version_no)
);

-- --------------------------------------------------------------- documents --

CREATE TABLE document (
    document_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    name               text NOT NULL,
    title              text,
    media_type         text NOT NULL,
    sha256             char(64) NOT NULL,
    raw_object_key     text NOT NULL,            -- immutable raw bytes in object storage
    canonical_text     text NOT NULL,            -- line-preserving text; spans index into this
    line_count         integer NOT NULL,
    metadata           jsonb NOT NULL DEFAULT '{}',
    UNIQUE (dataset_version_id, name)
);

CREATE TABLE source_span (
    span_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    document_id        uuid NOT NULL REFERENCES document ON DELETE CASCADE,
    line_start         integer NOT NULL CHECK (line_start >= 1),
    line_end           integer NOT NULL CHECK (line_end >= line_start),
    char_start         integer NOT NULL CHECK (char_start >= 0),
    char_end           integer NOT NULL CHECK (char_end > char_start),
    exact_text         text NOT NULL,            -- = substring(canonical_text, char_start+1, char_end-char_start)
    heading_path       text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX source_span_doc_lines ON source_span (document_id, line_start, line_end);

CREATE TABLE chunk (
    chunk_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    span_id            uuid NOT NULL REFERENCES source_span,
    text               text NOT NULL,
    token_count        integer NOT NULL,
    embedding          vector(1536),             -- null until embedded; dimension = embedding model
    search_tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);
CREATE INDEX chunk_version ON chunk (dataset_version_id);
CREATE INDEX chunk_tsv ON chunk USING gin (search_tsv);
CREATE INDEX chunk_embedding ON chunk USING hnsw (embedding vector_cosine_ops);

-- ------------------------------------------------------------ tables/cells --

CREATE TABLE source_table (                      -- "table" in the plan; renamed to avoid the keyword
    table_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    span_id            uuid NOT NULL REFERENCES source_span,
    caption            text,
    header_rows        jsonb NOT NULL,           -- list of header rows (header hierarchy preserved)
    n_rows             integer NOT NULL,
    n_cols             integer NOT NULL
);

CREATE TABLE table_cell (
    cell_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    table_id           uuid NOT NULL REFERENCES source_table ON DELETE CASCADE,
    span_id            uuid NOT NULL REFERENCES source_span,
    row_idx            integer NOT NULL,
    col_idx            integer NOT NULL,
    row_label          text,
    header_path        text[] NOT NULL,
    raw_text           text NOT NULL,
    typed              boolean NOT NULL DEFAULT false,   -- a fact was extracted from it
    search_tsv         tsvector GENERATED ALWAYS AS
                         (to_tsvector('simple', coalesce(row_label, '') || ' ' || raw_text)) STORED,
    UNIQUE (table_id, row_idx, col_idx)
);
CREATE INDEX table_cell_version ON table_cell (dataset_version_id);
CREATE INDEX table_cell_tsv ON table_cell USING gin (search_tsv);

-- -------------------------------------------------------- entities & facts --

CREATE TABLE entity (
    entity_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    label              text NOT NULL,            -- canonical label as found in the dataset
    UNIQUE (dataset_version_id, label)
);

CREATE TABLE entity_alias (
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    entity_id          uuid NOT NULL REFERENCES entity ON DELETE CASCADE,
    alias              text NOT NULL,            -- stored lowercased
    origin             text NOT NULL,            -- 'document' | 'derived' | 'memory' (approved)
    span_id            uuid REFERENCES source_span,
    PRIMARY KEY (dataset_version_id, alias, entity_id)
);

CREATE TABLE fact (
    fact_id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id    uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    entity_id             uuid REFERENCES entity,          -- null = dataset/sector-level fact
    cell_id               uuid REFERENCES table_cell,      -- null for prose/summary-line facts
    span_id               uuid NOT NULL REFERENCES source_span,
    metric                text NOT NULL,                   -- dataset-scoped normalized key
    metric_label          text,                            -- as written
    value                 double precision,
    value_low             double precision,
    value_high            double precision,
    value_text            text,
    original_value        text NOT NULL,
    unit                  value_unit NOT NULL,
    unit_label            text,
    currency              char(3),                         -- null = not stated
    scale                 double precision NOT NULL DEFAULT 1,
    is_estimate           boolean NOT NULL DEFAULT false,
    qualifier             text,
    basis                 fact_basis,
    role                  fact_role NOT NULL,
    period_label          text,
    period_end            date,                            -- only when source-backed
    period_type           period_type NOT NULL DEFAULT 'unspecified',
    extraction_confidence real NOT NULL CHECK (extraction_confidence BETWEEN 0 AND 1),
    search_tsv            tsvector GENERATED ALWAYS AS
                            (to_tsvector('simple', metric || ' ' || coalesce(metric_label, '') || ' ' ||
                                                   original_value)) STORED,
    CHECK (value IS NOT NULL OR value_low IS NOT NULL OR value_text IS NOT NULL)
);
CREATE INDEX fact_lookup ON fact (dataset_version_id, entity_id, metric, role, period_end);
CREATE INDEX fact_tsv ON fact USING gin (search_tsv);

-- -------------------------------------------------------------- validation --

CREATE TABLE validation_finding (
    finding_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version ON DELETE CASCADE,
    rule               finding_rule NOT NULL,
    rule_version       text NOT NULL,
    severity           finding_severity NOT NULL,
    status             finding_status NOT NULL DEFAULT 'open',
    explanation        text NOT NULL,
    fact_ids           uuid[] NOT NULL,
    span_ids           uuid[] NOT NULL,
    expected           text,
    observed           text,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX validation_finding_version ON validation_finding (dataset_version_id, rule);
CREATE INDEX validation_finding_facts ON validation_finding USING gin (fact_ids);

-- ------------------------------------------------------------ memory (B) --

CREATE TABLE research_memory (
    memory_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope              text NOT NULL CHECK (scope IN ('session', 'dataset')),
    session_id         text,
    dataset_version_id uuid REFERENCES dataset_version,   -- required for scope='dataset'
    kind               text NOT NULL,                     -- summary | preference | alias | note
    content            text NOT NULL,
    evidence_span_ids  uuid[] NOT NULL DEFAULT '{}',
    version            integer NOT NULL DEFAULT 1,
    revoked            boolean NOT NULL DEFAULT false,
    expires_at         timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (scope <> 'dataset' OR dataset_version_id IS NOT NULL)
);

-- --------------------------------------------------------------- jobs (A) --

CREATE TABLE job (
    job_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type               text NOT NULL,           -- ingest | embed | validate | evaluate
    idempotency_key    text UNIQUE,
    payload            jsonb NOT NULL,
    state              job_state NOT NULL DEFAULT 'queued',
    attempts           integer NOT NULL DEFAULT 0,
    max_attempts       integer NOT NULL DEFAULT 3,
    last_error         text,
    result             jsonb,                   -- IngestReport / EvaluationReport
    run_after          timestamptz NOT NULL DEFAULT now(),
    claimed_by         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
-- Workers claim with: SELECT ... WHERE state IN ('queued','retrying') AND run_after <= now()
--                     ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
CREATE INDEX job_claim ON job (state, run_after, created_at);

-- -------------------------------------------------------------- audit (B) --

CREATE TABLE answer_run (
    run_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL REFERENCES dataset_version,
    session_id         text,
    question           text NOT NULL,
    -- inserted when the run starts (so tool_event rows can reference it); the four
    -- columns below are filled when it finishes. A null status means the run crashed.
    status             answer_status,
    evidence_status    evidence_status,
    response           jsonb,                   -- full AnswerResponse
    completed_at       timestamptz,
    source_hash        text NOT NULL,
    parser_version     text NOT NULL,
    prompt_version     text NOT NULL,
    model              text NOT NULL,
    input_tokens       integer NOT NULL DEFAULT 0,
    output_tokens      integer NOT NULL DEFAULT 0,
    cost_usd           numeric(10, 6) NOT NULL DEFAULT 0,
    latency_ms         integer,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tool_event (
    event_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             uuid NOT NULL REFERENCES answer_run ON DELETE CASCADE,
    seq                integer NOT NULL,
    kind               text NOT NULL,           -- tool_call | llm | verify | policy
    name               text NOT NULL,
    input              jsonb,
    output             jsonb,
    span_ids           uuid[] NOT NULL DEFAULT '{}',
    latency_ms         integer NOT NULL,
    tokens             integer,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);
