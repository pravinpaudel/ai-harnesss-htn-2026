# Contract v1.0

The only thing Lane 1 (corpus → fact store) and Lane 2 (question → cited answer) share. Neither lane reads the other's code. If something here is wrong or missing, both developers agree the change and bump `CONTRACT_VERSION` in `build_fixture.py` and the `meta` table.

## Files

| File | What it is | Who uses it |
|---|---|---|
| `schema.sql` | DuckDB schema for `store.duckdb` | Lane 1 writes it; Lane 2 reads it |
| `answer.schema.json` | JSON Schema for the Answer object (`htn ask --json`) | Lane 2 produces it; eval grades it |
| `golden_facts.csv` | 73 facts read by hand from all three reports, with the source line and a verbatim snippet | Lane 1's parser test (must reproduce every row) |
| `build_fixture.py` | Builds the fixture store from hand-typed facts and checks every value against its source line | Both; run after any contract change |
| `fixture/raw/` | Copies of the mining and technology reports (same line numbers as the originals) | Citation verification during Lane 2 development |
| `fixture/store.duckdb` | Hand-built store: 2 documents, 46 sections, 12 entities, 61 aliases, 87 facts, 12 chunks, 3 conflicts | Lane 2 develops against it until CP1 |
| `fixture/questions.json` | 15 questions answerable from the fixture, in eval format | Lane 2's tests before CP1 |

`fixture/store.duckdb` is not committed (DuckDB files are ~8 MB even when tiny). Build it:

```bash
uv run --with duckdb python contract/build_fixture.py
```

It prints table counts, checks all 73 golden rows against the raw files, and exits non-zero on any mismatch.

## Corpus layout

Every ingested corpus, fixture or real, has the same layout:

```
corpus/<name>/
  raw/<filename>.md     verbatim documents from the MCP tool, never modified
  store.duckdb          written by Lane 1
  runs.duckdb           written by Lane 2 (run_log, trace); Lane 1 never touches it
```

The fixture uses `contract/fixture/` as its `<name>` directory.

## Handoff

Lane 2's `htn ingest` calls exactly one Lane 1 function:

```python
def build_store(source: Literal["mcp"] | Path, out_dir: Path) -> IngestReport: ...

@dataclass
class IngestReport:
    documents: int
    facts: int
    chunks: int
    conflicts: int
    seconds: float
    warnings: list[str]      # tables or lines that could not be parsed, with doc_id:line
```

`source="mcp"` calls `financialDataRetrieval`; a `Path` reads `*.md` from that directory. Either way, the raw files end up in `out_dir/raw/`.

## Identifiers

| Thing | Format | Example |
|---|---|---|
| `doc_id` | sector slug from the filename `canadian-<slug>-research.md`; fall back to the full stem | `mining` |
| `section_id` (level 2) | `<doc_id>#<number>` | `mining#10` |
| `section_id` (level 3) | `<parent>/<anchor or lowercased ticker or slug>` | `technology#6/shop-quarterly`, `mining#6/ivn`, `mining#5/ivn` |
| `entity_id` | ticker exactly as in §3 | `TECK.B`, `GIB.A` |
| `fact_id` | `<doc_id>:<line>:<entity_id or _>:<concept_key>:<role>[:<basis>][:<n>]` | `mining:1899:IVN:eps_beat_count:count` |
| `chunk_id` | `<doc_id>:<line_start>-<line_end>` | `mining:1793-1794` |
| `conflict_id` | `<check_type>:<doc_id>:<entity_id or _>:<concept_key>` | `count:mining:IVN:eps_beat_count` |

Lines are 1-based. Line numbers always refer to `raw/<filename>`.

## Fact conventions

**`exact_text`** is a verbatim substring of the cited line(s). If Lane 2 can't find it there, the fact is treated as broken.

**`concept`** is the raw header or label, exactly as written: `Rev YoY%`, `EPS (Act vs Est)`, `Revenue Growth (YoY, Latest Q)`.

**`concept_key`** is normalized as follows:
1. Lowercase. Remove period tokens (`Q3`, `Latest Q`, `Last Q`, `(8Q)`), currency tokens (`(CAD)`, `(USD)`) and unit tokens (`(bps)`).
2. Move basis tokens into `basis`: `Adj`/`adj` → `adjusted`, `GAAP` → `gaap`, `non-GAAP` → `non_gaap`.
3. Apply the seed synonyms below.
4. Anything else becomes snake_case of what's left (`Net Debt/EBITDA` → `net_debt_to_ebitda`, `Rule of 40` → `rule_of_40`). Phase 2 metrics fall through to this rule, so it must be deterministic.

| Raw forms | `concept_key` |
|---|---|
| Rev, Revenue, Rev (Q3), Last Q Rev | `revenue` |
| Rev YoY%, YoY% (in a revenue table), Revenue Growth (YoY, Latest Q) | `revenue_yoy_pct` |
| EPS, Q3 Adj EPS, Latest Q EPS, EPS (Adj), EPS (Act vs Est) | `eps` |
| EPS YoY% | `eps_yoy_pct` |
| Mkt Cap, Mkt Cap (CAD), Mkt Cap (USD), Market Cap | `market_cap` |
| CET1, CET1 Ratio | `cet1` |
| ROE, ROE (Adj) | `roe` |
| PCL (bps) | `pcl` |
| Div/Qtr | `dividend_per_quarter` |
| Beat/Miss | `beat_miss` |
| EPS Beat Rate (8Q), `Beat N/8` in a trend line | `eps_beat_count` |
| `Beat/Met N/8` in a trend line | `eps_beat_or_met_count` |
| Screening `Rank` column | `screening_rank` |
| Catalyst calendar rows | `catalyst` |

**Values:**
- `value_num` is fully expanded: `$5,292M` → `5292000000`, `58%` → `58`, `36 bps` → `36`, `3.0x` → `3.0`. `scale` records what the source used (`1e6` for `M`, `1e9` for `B`).
- `value_text` holds non-numeric values (`Miss (-5%)`, `N/M`, `Copper/Zinc`), and for rank facts the verbatim cell (`IVN (+58%)`).
- Ranges set `value_low`/`value_high` and leave `value_num` null: `$2.89-$3.10 est` → 2.89, 3.10. `$700M-$1B` → 7e8, 1e9.
- `is_estimate` is true for `~`, `est`, `approximately`, `(Est)`, `est ~`, `mid-30s` and similar hedges.

**Units:** `pct` | `bps` | `x` | `money` | `count` | `rank` | `quantity` | `ratio` | `score` | `text`. `unit_label` qualifies `quantity` and per-unit money (`t Cu`, `/oz`, `/lb`, `GEO`).

**Currency:** taken from the cell (`C$` → CAD, `US$` → USD, a trailing `CAD`/`USD`) and otherwise from the column header (`Mkt Cap (CAD)`). The cell wins over the header: mining §3 IVN `C$17.1B` under `Mkt Cap (USD)` is CAD. A bare `$` with no header currency is `null`, meaning not stated. Lane 2 declines `cross_currency` only when both currencies are known and differ. When one is null, it answers with a caveat.

**Periods:**
- `period_label` is as written, including any date hint: `Q3 FY2026 (Jul 31, 2026)`.
- `period_end` resolves to a calendar date. A date hint in the label wins. Otherwise use calendar quarter ends, or the entity's fiscal year-end for `FY` labels (banks: Oct 31; GIB.A: Sep 30; OTEX: Jun 30; DSG: Jan 31). Snapshot tables (§3/§4) take the period from the table caption or cell (`(Q2 2026)`); point-in-time values (market cap, consensus, fiscal year-end) use the document's as-of date with `period_type='point'`.
- `period_type` is one of `fiscal` | `calendar` | `ttm` | `point` | `range` | `unspecified`.

**Roles:**

| `role` | Used for | Encoding |
|---|---|---|
| `actual` | reported values | `value_num` |
| `estimate` | consensus estimates (`vs $0.39 est`) | `value_num`, or `value_low`/`value_high` for a range |
| `attribute` | categorical values (beat/miss, consensus, commodity) | `value_text` |
| `rank` | position in a screening/ranking table | `value_num` = rank (1 = top), `value_text` = verbatim cell |
| `count` | `N/8`-style claims | `value_num` = N, `value_text` = `N/8` |
| `trend` | arrows and `A → B` claims | `value_low` = start, `value_high` = end, `value_text` = `↑` / `↓` / verbatim word; `period_label='trailing 8Q'`, `period_type='range'`, `period_end` = entity's latest quarter end; `unspecified` period if the source gives none |
| `target` / `guidance` | management targets and guidance | as `actual` |
| `event` | catalyst calendar rows | `value_text` = event, period = event date |

A rank-matrix cell such as `IVN (+58%)` produces **two** facts: a `rank` fact and an `actual` fact with the embedded value (`extractor='rank_matrix'`). Validator check 1 needs both.

**Extractors:** `table` | `summary_line` (mining §6 `<summary>` lines) | `kv` (§5 `Field | Value` tables) | `trend_line` (`**Quick trend read:**`) | `rank_matrix` | `bullet` (§1 bullets, §5 thesis).

## Chunks

Each chunk is a verbatim block: a bold-labelled paragraph (`**Thesis:**`, `**1. Headline Results**`, `**Headline results:**`), a `- **Top risks/avoid:**` bullet, or a bullet list under a label. `heading_path` joins the section headings, the quarter (if inside a `<details>` block) and the block label with ` > `. In a real store, every non-table, non-blank line belongs to exactly one chunk or fact. The fixture only has 12 sample chunks.

## Conflicts

`check_type` is `ordering` | `count` | `trend_direction` | `cross_section` | `unit_mix`.
- `fact_id_a` is the derived claim being checked.
- `fact_id_b` is the single contradicting fact, if there is one.
- `evidence_fact_ids` lists every fact used in the recomputation.
- `expected_value` is the recomputed value; `observed_value` is what the document asserts.

The fixture's three conflicts show the expected shape for count, ordering and cross-section checks.

## What each lane may assume

**Lane 2 may assume:**
- every `exact_text` is verbatim on its lines
- every fact has a `section_id`, and every entity has at least its lowercased ticker and name as aliases
- `period_end` is safe to sort on when it isn't null

**Lane 1 may assume** that Lane 2 never writes to `store.duckdb` and never parses Markdown.
