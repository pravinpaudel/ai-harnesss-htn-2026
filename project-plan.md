# Project Plan — RBC "Signal in the Noise"

**Hack the North 2026 | Waterloo | Code window: Sat Sep 19, 12:00 AM → Sun Sep 20, 8:00 AM EDT (32 hours)**

**Target prizes:** RBC *Best Business Value* **[BV]** and RBC *Top Tech Performance* **[TP]**.
**Team:** 2 developers, 2 isolated lanes, 1 shared contract.

---

## 1. What we are building

A **CLI research harness** (`htn`) over financial research corpora delivered by RBC's `htn` MCP tool. It answers precise questions from a **structured fact store** with verifiable citations, detects contradictions inside the source documents, declines when the corpus does not contain the answer, and leaves an audit trail for every answer.

**Who it's for.** An RBC equity research associate or portfolio manager who receives a sector pack and must answer specific questions from it before a meeting, with sources a compliance reviewer can check.

**The outcome we sell.** *"A 440K-character sector pack goes from hours of reading to seconds per cited answer, and arrives with an error report listing where the document contradicts itself."* Speed is the hook; **risk reduction** is why a bank pays.

**The technical wedge.** Every document follows the same 11-section template, and its summaries, comparison tables and rankings are *derived* from the per-quarter detail. We parse everything into typed facts deterministically, recompute the derived claims, and diff them at ingest — finding errors with no prior knowledge of which exist.

**Assumptions (confirmed with RBC):** all data is Markdown; the judging dataset follows the same template as the sample, for a new industry. So the template parser is the main system, not a fallback. What changes in Phase 2 is the **entities, metric columns and periods** — never hardcode those.

---

## 2. Corpus intelligence (from the Phase 1 sample)

| Property | Observation | Consequence |
|---|---|---|
| Size | ~448 KB, ~5,900 lines, ~110k tokens for 3 reports | One MCP call returns everything as one JSON payload (444K chars — overflowed Claude Code's tool-output limit). Stream to disk; never pass the corpus through an LLM. |
| Template | Identical 11 sections per sector report; `{#ticker-quarterly}` anchors | Parse by section number, not heading text. Anchors are free citation targets. |
| §3 comp table | One row per company: ticker, name, market cap, EPS, revenue, consensus | **Entity registry.** Ticker + canonical name come free. |
| §4 metrics table | Columns vary by sector (CET1/NIM for banks, AISC/gross margin for mining, ARR/Rule of 40 for tech) | Column-agnostic: header text becomes `concept`. Phase 2 will bring new columns. |
| §5 deep dives | `Field \| Value` tables + Thesis/Catalysts/Risks bullets | Key-value facts + prose chunks. |
| §6 quarterly | Financials/tech: pipe tables (Quarter, Revenue, YoY%, EPS Act vs Est, Beat/Miss, Key Driver). **Mining: no table — each quarter is a `<details><summary>` line** ("Q2 2026 — Revenue $5,292M; EPS $0.82 vs $0.83 est (Slight Miss); …") | Two §6 parsers: table rows and summary-line regex. Both must produce identical fact shapes. Plus a "Quick trend read" line per company (arrows, N/8 counts). |
| §8 / §9 | Catalyst and insider tables | Row facts with dates/events. |
| §10 screening | Financials/tech: rank table. Mining: metric × rank matrix (#1–#6 columns) | Rank facts; validator input. |
| Periods | Banks fiscal (Oct YE), mining calendar, tech mixed. DSG "Q2 FY2027" precedes SHOP "Q2 2026" chronologically. | Store fiscal label **and** resolved calendar end date. Sort only on the resolved date. |
| Currency | §3 mining mixes USD and CAD in one column (FM ~C$30B, IVN C$17.1B) | Currency is a required field. Cross-currency comparisons flag or decline. |
| Hedging | "some reports cite 18.1%", "$700M–$1B", "est ~25%", "~$0.26" | `value_low`/`value_high`, `is_estimate`. |

---

## 3. Architecture

```
          LANE 1 — Corpus → Fact Store                      LANE 2 — Question → Cited Answer
 ┌──────────────────────────────────────────┐        ┌──────────────────────────────────────────┐
 │ MCP client (stream to disk, SHA-256)     │        │ CLI shell (ask, batch, conflicts, brief, │
 │ Section splitter (by §number)            │        │   trace, eval, config)                   │
 │ Parsers: §3 entities, §4 metrics,        │        │ Query planner / agent loop (bounded)     │
 │   §5 key-value, §6 table + summary-line, │        │ Tools: sql_query · find_facts ·          │
 │   §8/§9 rows, §10 ranks, prose chunks    │        │   search_text · get_source ·             │
 │ Resolvers: period, unit, currency, range │        │   list_conflicts · decline               │
 │ Entity registry + aliases                │        │ Citation verifier (quote-in-source)      │
 │ Derived-Fact Validator → conflict table  │        │ Answer contract + renderer / --json      │
 │                                          │        │ run_log, cost metering, exports, eval    │
 └───────────────────┬──────────────────────┘        └───────────────────▲──────────────────────┘
                     │  writes                                            │ reads (read-only)
                     ▼                                                    │
             corpus/<name>/store.duckdb  +  corpus/<name>/raw/*.md  ──────┘
                           THE CONTRACT (§4) — the only thing both lanes share
```

**Structured facts are the main path.** Numeric, comparative, ranking, counting and aggregation questions are answered by SQL over `fact`. The prose chunks (`<details>` bodies, thesis/risk bullets) are a secondary path for narrative questions ("why did NA shares fall after Q3 FY2025?"). Every answer, either path, cites a verified source span.

---

## 4. The contract (frozen at minute 30)

Both developers write this together in the first 30 minutes, commit it as `contract/`, and then **do not look at each other's code until the integration checkpoints**. Changes to the contract require both people and a version bump.

`contract/` contains:
1. `schema.sql` — the DuckDB schema below.
2. `fixture.duckdb` + `fixture_raw/` — a hand-built mini-store: the IVN §6 summaries, mining §10 revenue-growth row, SHOP §1 bullet and §6 row, 2 conflicts, ~40 facts, ~10 chunks. Lane 2 develops against this; Lane 1 must be able to regenerate an equivalent from the real files.
3. `golden_facts.csv` — ~60 facts both people read off the sample by hand (entity, concept, period, value, source line). Lane 1's parser test; Lane 2's answer test.
4. `answer.schema.json` — the Answer contract below.

**Store schema (`store.duckdb`)** — Lane 1 writes, Lane 2 reads only.

```sql
document(doc_id, filename, sector, title, as_of_date, snapshot_sha256, line_count)
section(section_id, doc_id, number, heading, anchor, line_start, line_end)
entity(entity_id, ticker, name, sector, doc_id)                 -- from §3
entity_alias(alias, entity_id)                                  -- ticker variants, short names, "Teck", "CGI"
fact(
  fact_id, doc_id, section_id, entity_id,                       -- entity_id null for sector-level facts
  concept, concept_key,                                         -- raw header text; normalized snake_case key
  value_num, value_text, value_low, value_high,
  unit,            -- pct | bps | x | count | money | ratio | text
  currency,        -- CAD | USD | null
  scale,           -- 1 | 1e3 | 1e6 | 1e9
  is_estimate,     -- bool
  period_label,    -- as written: "Q3 FY2026"
  period_end,      -- resolved calendar date, THE sort key
  period_type,     -- fiscal | calendar | ttm | point
  role,            -- actual | estimate | rank | count | trend | target | guidance
  line_start, line_end, exact_text,                             -- verbatim source span
  extractor        -- table | summary_line | kv | trend_line | rank_matrix
)
chunk(chunk_id, doc_id, section_id, entity_id, heading_path, text, line_start, line_end)
conflict(conflict_id, check_type, severity, entity_id, concept_key, description,
         fact_id_a, fact_id_b, expected_value, observed_value)
```

**Answer contract** — Lane 2 produces; `--json` emits it verbatim.

```
run_id, question, status: answered | conflict | declined | partial
answer_text                     every claim footnoted [n]
value                           typed scalar/list for quantitative questions
citations: [ {n, doc, section, anchor, line_start, line_end, quote} ]
conflicts: [ {conflict_id, claim_a, claim_b, citations} ]
decline_reason                  not_in_corpus | false_premise | ambiguous | cross_currency
confidence: high | medium | low + one-line reason
cost: {input_tokens, output_tokens, usd, latency_ms, tool_calls}
corpus_sha256, model, prompt_version
```

**Handoff:** `htn ingest` (Lane 2's CLI) calls exactly one Lane 1 function:
```python
build_store(source: "mcp" | Path, out_dir: Path) -> IngestReport   # docs, facts, chunks, conflicts, seconds
```

---

## 5. Lane 1 — Corpus → Fact Store (Developer A)

**Goal:** 100% of table cells and summary lines in the sample become correctly typed, cited facts; conflicts are found by recomputation; any same-template corpus ingests cold in < 30 s.

| # | Deliverable | Done when |
|---|---|---|
| 1.1 | MCP client: call `financialDataRetrieval`, stream JSON to `raw/*.md`, SHA-256 each | Real corpus on disk in < 10 s; no full payload held in memory twice |
| 1.2 | Section splitter by `## N.` and `### TICKER` | All 11 sections × 3 docs with correct line ranges |
| 1.3 | Generic pipe-table parser → rows with line numbers | Every table in the sample parsed; row count test |
| 1.4 | Value resolver: `$5,292M`, `C$1.93`, `~13.5%`, `+26%`, `36 bps`, `3.0x`, `$700M-$1B`, `est ~25%`, `N/M` | Unit tests on 50 real strings from the sample |
| 1.5 | Period resolver: `Q3 FY2026` + sector fiscal year-end → calendar date; `(Jul 31, 2026)` hints override | DSG/SHOP/bank ordering test passes |
| 1.6 | Entity registry from §3 + aliases (ticker, name, name minus suffix, `.TO`, `.B`) | All 18 entities; "Teck", "CGI", "Scotiabank" resolve |
| 1.7 | Section parsers: §3, §4 (column-agnostic), §5 key-value, §6 table **and** summary-line, trend-read line, §8, §9, §10 (rank table and rank matrix) | `golden_facts.csv` 100% match |
| 1.8 | Prose chunker: `<details>` bodies and §5 bullets with heading path + entity | Every non-table line belongs to exactly one chunk or fact |
| 1.9 | Derived-Fact Validator → `conflict` | Finds the 6 known conflicts **and** reports any others (see below) |
| 1.10 | Held-out test: rewrite one sample report as a fake new industry (rename tickers, change §4 columns) and ingest it | Zero code changes needed |

**Derived-Fact Validator** — four checks, none of which know what is planted:

| # | Check | Method | Catches in sample |
|---|---|---|---|
| 1 | Ordering | Any rank table/matrix: sort by its own values, diff | Mining §10 revenue growth: AEM +35% ranked above IVN +58%, ABX +44% |
| 2 | Count | Any "N/8" / "Beat 7/8" claim: recount from §6 facts | IVN 3/8 in §10 vs 2 beats + 1 met in §6 |
| 3 | Endpoint vs arrow | Any ↑/↓ in a trend line: compare first and last value | CSU FCFE ↑ but $362M → $345M |
| 4 | Cross-section match | Same (entity, concept_key, period) in §1/§3/§4/§10 vs §6 | SHOP Payments 58% vs 62%; OTEX vs GIB.A slowest growth |
| + | Unit hygiene | Mixed currency within one column | Mining §3 market cap USD/CAD |

**Lane 1 does not touch:** LLMs, the CLI, answer formatting. Pure deterministic Python + DuckDB. (One optional exception, last: LLM-assisted `concept_key` normalization, cached.)

---

## 6. Lane 2 — Question → Cited Answer (Developer B)

**Goal:** Every question returns an Answer contract with verified citations, the right status, and cost metering — built entirely against `contract/fixture.duckdb` until integration.

| # | Deliverable | Done when |
|---|---|---|
| 2.1 | CLI skeleton (`typer` + `rich`): all commands stubbed, `--json`, config file | `htn --help` shows the full surface |
| 2.2 | Tools over the store: `find_facts(entity, concept, period_from/to, role)`, `sql_query` (read-only, row-limited), `search_text` (DuckDB FTS over `chunk`), `get_source(doc, line_start, line_end)`, `list_conflicts(entity?, concept?)`, `list_concepts(entity?)`, `resolve_entity(name)` | Each tool unit-tested on the fixture |
| 2.3 | Agent loop: LLM with tools, max 8 steps, token + $ budget; structured facts first, text search second | Answers fixture questions; returns `partial` when out of budget |
| 2.4 | **Citation verifier:** every quote must appear verbatim in `raw/` at the cited lines, else dropped and status downgraded | A deliberately wrong quote is caught in a test |
| 2.5 | Status logic: `conflict` whenever a used fact appears in `conflict`; `declined` when no supporting fact/chunk; `cross_currency` guard | Fixture conflict and decline questions pass |
| 2.6 | `run_log` in a separate `runs.duckdb` (question, tool calls, facts used, spans, tokens, $, ms, corpus SHA, model, prompt version); `htn trace <run_id>` | Any answer replayable |
| 2.7 | `htn eval questions.json` → scorecard (accuracy by type, citation precision, verified-quote rate, conflict recall, false-answer rate, tokens/$/latency p50/p95) | Runs on fixture questions; later on the full set |
| 2.8 | Long-context baseline (same questions, whole corpus in prompt) for the scorecard comparison | Baseline row in scorecard |
| 2.9 | Exports: `htn conflicts --export md|html|json`, `htn batch questions.txt --out answers.md|html` | Files a PM could open |
| 2.10 | `htn brief --sector <s>`: cited one-pager from §1/§3/§10 facts | Every line footnoted and verified |
| 2.11 | `htn config`: provider/model, `--local-only` (Ollama), `--budget-usd` | Switching provider needs no code change |
| 2.12 | Packaging: `uv` project, `pipx install .`, works on a clean laptop | Fresh-machine install in < 2 min |

**Lane 2 does not touch:** Markdown parsing. If an answer needs a fact the store doesn't have, it's logged as a store gap for Lane 1 at the next checkpoint — never patched with an ad-hoc regex over raw text.

**Business-value features and why each exists [BV]:**

| Feature | Why an executive cares |
|---|---|
| Audit trail + `trace` | Compliance can reproduce any answer; model-risk guidance (e.g. OSFI E-23) expects it |
| Corpus hash + model + prompt version on every run | Answers survive review weeks later |
| Verified citations | Zero fabricated sources — the main blocker to LLM use in research |
| Honest status + confidence | Tells the user when not to trust it |
| Conflicts export | Errors caught before they reach a PM or client |
| Batch → memo | Matches the real workflow: a list of questions before a meeting |
| Sector brief | The "time saved" moment |
| Cost metering + budget cap | Unit economics: $/cited answer; no runaway spend |
| `--local-only` + provider-agnostic | Bank data-residency and approved-model constraints |
| One-command install | "Deployment-ready" shown, not claimed |

---

## 7. Evaluation

Question set (`question-set.md`, 47 questions) converted to JSON by Lane 2:

```json
{"id":"D4","q":"How many quarters did Ivanhoe beat EPS estimates?",
 "type":"conflict",
 "expect":{"conflict":true,"values":["3/8","2 beats"],
           "anchors":["mining#10","mining#ivn-quarterly"]},
 "tolerance":0.0}
```

Types: `scalar` | `entity` | `ordered_list` | `conflict` | `decline` | `false_premise` | `narrative`.

**Two-sided testing, so the lanes stay honest without seeing each other:**
- Lane 1 is graded on `golden_facts.csv` (fact recall/precision) and on finding the known conflicts.
- Lane 2 is graded on the question set against the fixture, then against the real store after integration.
- Each developer writes 15 extra questions *independently* before the first integration; the other lane's system has never seen them.

**Question set gaps to close** (target 70–80):
- False premises ("Why did Barrick's CEO resign in Q2 2026?").
- Fan-out aggregation ("total PCL across the six banks", "median gross margin across all 18 companies").
- Entity-resolution stress (TECK.B / Teck / "Anglo Teck"; GIB.A / CGI; "Scotiabank").
- Plausible-but-absent metrics (AISC for a bank) and out-of-coverage companies (BCE, Cenovus).
- Period traps (fiscal vs calendar Q2 across sectors).

---

## 8. Timeline and integration checkpoints

Lanes work in parallel; they meet **only** at the checkpoints below. Sleep is staggered so one developer is always awake.

| Time | Lane 1 (Dev A) | Lane 2 (Dev B) |
|---|---|---|
| **12:00–12:30 AM** | **Together:** pull corpus via MCP, write `contract/` (schema, fixture, golden facts, answer schema). Commit. Split. | ← |
| 12:30–3:00 AM | 1.1–1.5: MCP client, splitter, table parser, value + period resolvers | 2.1–2.3: CLI skeleton, tools, agent loop on fixture |
| **3:00 AM — CP1** | **Swap fixture for real store.** Goal: `htn ask` answers one A-tier question with a verified citation from real data. Exchange store-gap list. | ← |
| 3:00–7:00 AM | 1.6–1.7: entity registry, all section parsers, `golden_facts` → 100% | 2.4–2.6: citation verifier, status logic, run_log + trace |
| 7:00–11:00 AM | Dev A sleeps | 2.7–2.8: eval + baseline; convert question set to JSON |
| 11:00 AM–3:00 PM | 1.8–1.9: chunker, validator (4 checks) | Dev B sleeps |
| **3:00 PM — CP2** | **Full eval on real store.** Scorecard v1. Exchange failure list. RBC Power Hour (Room 4C) — show scorecard + conflicts. | ← |
| 3:00–9:00 PM | 1.10: fake-industry held-out test; fix every break; ingest < 30 s | 2.9–2.11: exports, brief, config, local-only |
| **9:00 PM — CP3** | **Phase 2 rehearsal:** cold `htn ingest` on the fake-industry corpus, run 15 unseen questions. | ← |
| 9:00 PM–2:00 AM | Fix CP3 failures on the store side | Fix CP3 failures on the answer side; 2.12 packaging |
| 2:00–5:00 AM | **Feature freeze.** Both: clean-machine install, eval re-run, demo script. | ← |
| 5:00–7:30 AM | Four full demo run-throughs. Submit Devpost by 7:30. | ← |
| **8:00 AM Sun** | Hard code freeze. | ← |

Devpost draft and badge IDs in by **1:30 PM Saturday**. Book RBC judging slots the instant links drop Sunday ~8:30 AM.

**Cut lines at each checkpoint:**
- CP1 missed → Lane 2 falls back to the long-context baseline behind the same Answer contract; Lane 1 keeps going.
- CP2 missed → drop prose chunks and `brief`; structured facts + conflicts only.
- CP3 breaks → the fix list decides the rest of the night; `--local-only` and HTML export are cut first.

---

## 9. Prize strategy and demo

**Primary: both RBC prizes.** One build, two pitches.

| RBC prize | What we show |
|---|---|
| **Top Tech Performance** | Cold Phase 2 ingest in < 30 s; scorecard vs long-context baseline (accuracy, verified-citation rate, tokens and $ per answer); validator finding conflicts it wasn't told about; `trace` |
| **Best Business Value** | Analyst persona and before/after; conflicts report as a risk-reduction deliverable; `batch` → exportable memo; $/answer; audit trail + local-only mode as the answer to "can a bank deploy this?" |

**Secondary (zero extra cost only):** Rox *Best AI Agent* with the agent loop as built. **Declined:** Elastic, GPTZero, Sentry, OpenAI, Baseten — each costs a judging slot during the RBC window.

**Demo (3 minutes, business first):**
1. **Problem (20s).** "An analyst gets this pack at 7 AM and a PM meeting at 9."
2. **Cold ingest (30s).** `htn ingest` on the Phase 2 corpus live → documents, facts, **N conflicts found**.
3. **Cited answer (30s).** `htn ask` → answer with footnotes, quotes, cost and latency.
4. **Contradiction (30s).** A question hitting a conflict → both claims, both sources.
5. **Honest no (20s).** False premise or absent metric → declines with a reason.
6. **Deliverable (30s).** `htn conflicts --export html` and `htn batch` output.
7. **Deployable (20s).** Scorecard, `trace`, `--local-only`.

Live demo only. Video-only and slideshow-only submissions are not accepted.

---

## 10. Risks and cut lines

| Risk | Mitigation | Cut line |
|---|---|---|
| Phase 2 industry has new §4 columns / new §6 format variant | Column-agnostic parsers; both §6 variants supported; fake-industry test (1.10) | Unparsed tables become chunks so they're still searchable and citable |
| Contract drift between isolated lanes | Frozen schema + fixture + golden facts; changes need both devs + version bump | Adapter in Lane 2's tool layer, never in Lane 1's schema |
| Integration surprises at CP1 | Fixture built from real sample rows, not invented | Long-context baseline behind the same Answer contract |
| Period/currency mistakes produce wrong comparisons | Resolved `period_end`; `cross_currency` decline | Refuse cross-currency ranking rather than guess |
| Agent loop slow or unreliable | Bounded steps; `find_facts` handles most questions in one call | Deterministic router: quantitative → SQL, narrative → FTS |
| LLM rate limits mid-demo | Budget cap; second provider in config | Cached answers for the rehearsed path |
| One developer blocked or asleep | Lanes independent by design; each has a fixture/golden set to test alone | — |

**Non-negotiables:** no graph database; no LLM in Lane 1's critical path; no citation reaches the user without the quote-in-source check; no web UI until CP3 passes.

---

## 11. After the CLI (only if CP3 is green)

1. HTML report polish (conflicts + batch).
2. Thin web UI reading `--json`: question box, clickable citations opening the source span, conflicts tab.
3. MCP server wrapper around `htn` so analysts can use it from their own AI assistant.

---

## 12. Questions for the RBC team

1. **How will judging-day answers be scored?** Live questions from judges, or a fixed set run through our system? What counts as a correct citation: document + section, a quoted span, or a line reference? Will questions include no-answer, false-premise or planted-contradiction cases?
2. **How big is the Phase 2 dataset, and how is it delivered?** Same `financialDataRetrieval` tool and endpoint? One industry report or several, and roughly how large compared with the ~440K-character sample?
3. **For Best Business Value, who is the target user and what are the constraints?** Analyst, PM or compliance — and which matters most: speed, accuracy or auditability? Any limits on sending data to external LLM APIs, or required/approved models?

---

## 13. Reference

- Wi-Fi: `Hack the North` / `hackthenorth2026`
- Slack: hackthenorth.slack.com — RBC channel
- Mentorship Café: PSE 1st floor (C&D). Software mentors Sat 10 AM–12 PM
- RBC booth: Sponsor Bay, PSE Floor 2 · RBC Power Hour Sat 3–5 PM, Room 4C
- Judging: waiting room 10 min early, team name + all badge IDs at check-in
- Help Desk: PSE entrance, all weekend. Chatbot in `#questions`
