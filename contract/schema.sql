-- Contract v1 — store.duckdb schema
-- Lane 1 (corpus -> fact store) WRITES this database. Lane 2 (question -> answer) only READS it.
-- Changing anything here needs both developers and a bump of contract_version below.
-- Conventions for every column are in contract/README.md.

CREATE TABLE meta (
    key   VARCHAR PRIMARY KEY,   -- 'contract_version', 'built_at', 'source', 'builder'
    value VARCHAR NOT NULL
);

CREATE TABLE document (
    doc_id          VARCHAR PRIMARY KEY,   -- sector slug from filename: canadian-mining-research.md -> 'mining'
    filename        VARCHAR NOT NULL,      -- file name inside raw/
    sector          VARCHAR NOT NULL,      -- 'Mining'
    title           VARCHAR NOT NULL,      -- first '# ' heading, verbatim
    as_of_date      DATE,                  -- from the title / '**As-of:**' line
    snapshot_sha256 VARCHAR NOT NULL,      -- sha256 of raw/<filename> bytes
    line_count      INTEGER NOT NULL
);

CREATE TABLE section (
    section_id        VARCHAR PRIMARY KEY, -- 'mining#6' or 'mining#6/ivn-quarterly' (see README)
    doc_id            VARCHAR NOT NULL REFERENCES document(doc_id),
    parent_section_id VARCHAR,             -- null for '##' sections
    level             INTEGER NOT NULL,    -- 2 for '##', 3 for '###'
    number            VARCHAR NOT NULL,    -- template section number as text: '6'
    heading           VARCHAR NOT NULL,    -- heading text without '#' and without '{#anchor}'
    anchor            VARCHAR,             -- explicit '{#...}' anchor if present, else null
    entity_id         VARCHAR,             -- for '### TICKER — ...' sections
    line_start        INTEGER NOT NULL,    -- 1-based, the heading line
    line_end          INTEGER NOT NULL     -- inclusive
);

CREATE TABLE entity (
    entity_id VARCHAR PRIMARY KEY,         -- ticker exactly as in §3: 'TECK.B', 'GIB.A'
    ticker    VARCHAR NOT NULL,
    name      VARCHAR NOT NULL,            -- §3 'Company' column
    sector    VARCHAR NOT NULL,
    doc_id    VARCHAR NOT NULL REFERENCES document(doc_id)
);

CREATE TABLE entity_alias (
    alias     VARCHAR NOT NULL,            -- lowercased; match case-insensitively
    entity_id VARCHAR NOT NULL REFERENCES entity(entity_id),
    PRIMARY KEY (alias, entity_id)
);

CREATE TABLE fact (
    fact_id      VARCHAR PRIMARY KEY,      -- '<doc_id>:<line_start>:<entity_id|_>:<concept_key>:<role>[:<n>]'
    doc_id       VARCHAR NOT NULL REFERENCES document(doc_id),
    section_id   VARCHAR NOT NULL REFERENCES section(section_id),
    entity_id    VARCHAR REFERENCES entity(entity_id),   -- null = sector-level fact

    concept      VARCHAR NOT NULL,         -- raw column header / label, verbatim: 'EPS (Act vs Est)'
    concept_key  VARCHAR NOT NULL,         -- normalized snake_case: 'eps'
    basis        VARCHAR,                  -- gaap | adjusted | non_gaap | null

    value_num    DOUBLE,                   -- fully expanded number: $5,292M -> 5292000000; 58% -> 58
    value_text   VARCHAR,                  -- non-numeric value or verbatim label: 'Miss', 'N/M', '3/8'
    value_low    DOUBLE,                   -- ranges ('$700M-$1B') and trend start
    value_high   DOUBLE,                   -- ranges and trend end
    unit         VARCHAR,                  -- pct | bps | x | money | count | rank | quantity | ratio | text
    unit_label   VARCHAR,                  -- free text qualifier for 'quantity'/'money': 't Cu', '/oz', '/lb', 'GEO'
    currency     VARCHAR,                  -- CAD | USD | null (null = not stated in cell or header)
    scale        DOUBLE,                   -- scale as written in source: 1, 1e3, 1e6, 1e9
    is_estimate  BOOLEAN NOT NULL,         -- true if '~', 'est', 'approximately', '(Est)' etc.

    period_label VARCHAR,                  -- as written: 'Q3 FY2026 (Jul 31, 2026)', 'Q2 2026'
    period_end   DATE,                     -- resolved calendar end date — THE sort key
    period_type  VARCHAR,                  -- fiscal | calendar | ttm | point | range | unspecified
    role         VARCHAR NOT NULL,         -- actual | estimate | rank | count | trend | target | guidance | event | attribute

    line_start   INTEGER NOT NULL,         -- 1-based line in raw/<filename>
    line_end     INTEGER NOT NULL,
    exact_text   VARCHAR NOT NULL,         -- verbatim source cell or line; MUST be a substring of those lines
    extractor    VARCHAR NOT NULL          -- table | summary_line | kv | trend_line | rank_matrix | bullet
);

CREATE TABLE chunk (
    chunk_id     VARCHAR PRIMARY KEY,      -- '<doc_id>:<line_start>-<line_end>'
    doc_id       VARCHAR NOT NULL REFERENCES document(doc_id),
    section_id   VARCHAR NOT NULL REFERENCES section(section_id),
    entity_id    VARCHAR REFERENCES entity(entity_id),
    heading_path VARCHAR NOT NULL,         -- '6. Company Performance > IVN > Q2 2026 > 1. Headline Results'
    period_label VARCHAR,                  -- quarter the chunk belongs to, if any
    text         VARCHAR NOT NULL,         -- verbatim lines joined with '\n'
    line_start   INTEGER NOT NULL,
    line_end     INTEGER NOT NULL
);

CREATE TABLE conflict (
    conflict_id       VARCHAR PRIMARY KEY, -- '<check_type>:<doc_id>:<entity_id|_>:<concept_key>'
    check_type        VARCHAR NOT NULL,    -- ordering | count | trend_direction | cross_section | unit_mix
    severity          VARCHAR NOT NULL,    -- high | medium | low
    doc_id            VARCHAR NOT NULL REFERENCES document(doc_id),
    entity_id         VARCHAR REFERENCES entity(entity_id),
    concept_key       VARCHAR,
    description       VARCHAR NOT NULL,    -- one human-readable sentence
    fact_id_a         VARCHAR NOT NULL REFERENCES fact(fact_id),  -- the claim being checked
    fact_id_b         VARCHAR REFERENCES fact(fact_id),           -- the contradicting claim, if a single fact
    evidence_fact_ids VARCHAR[],           -- all facts used in the recomputation
    expected_value    VARCHAR,             -- recomputed from base facts
    observed_value    VARCHAR              -- as asserted in fact_id_a
);
