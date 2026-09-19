# Checkpoint 1 — gaps in the evidence store (for Developer A)

**Run:** `htn ingest contracts/fixture/raw --dataset-name fixture` on the merged `main` (commit 754ea14), Postgres from the root `docker-compose.yml`, schema from `app.db.bootstrap.apply_contract_v1`. Then `htn eval --dataset fixture` as the read-only `htn_engine` role.

**Result:** ingest succeeded: a ready version in 0.2 s, document hashes matching `contracts/fixture/sources.json`. The store holds **56 facts, 32 of which match the 143 in `contracts/fixture/expected_facts.csv`, plus 0 of the 5 findings in `expected_findings.json`**. That stops three of the four Checkpoint 1 cases from passing for the right reason.

How to reproduce the comparison: match facts on document + line + value against `contracts/fixture/expected_facts.csv`. The contract README ("How each side tests against the contract") describes this as A's acceptance test.

## Gaps, in priority order

| # | Gap | Expected (contract) | Found | Cases blocked |
|---|---|---|---|---|
| 1 | **No validation findings** | 5 findings: `count_claim` (IVN 3/8 vs 2 beats), `rank_order` (mining revenue-growth ranking), `duplicate_claim` (SHOP Payments 58% vs 62%), 2 × `unit_currency_mix` (mining market caps) | 0 | FX06, FX07, FX08. Without findings the engine answered "3 of 8" as fact. |
| 2 | **No period labels on facts** | `period_label` as written, e.g. `Q2 2026 (Jun 30)`, `Q3 FY2026`; `period_end` only when the source states a date | `period_label` null on all 56 facts | FX01, FX02, FX04, FX10. The engine now works around this by matching the period in the source row, but typed periods are the contract. |
| 3 | **Summary-line facts missing** | Mining §6 has no table; each quarter is a `<summary>` line (`Q2 2026 — Revenue $152.6M; EPS $0.03 vs $0.06 est (Miss); …`) that must produce revenue, EPS actual, EPS estimate and beat/miss facts | 0 (32 expected for IVN alone) | FX01, FX04 (Q1 → Q2 2026 revenue calculation) |
| 4 | **Compound table cells not split** | `$0.42 vs $0.39` → actual + estimate; `$0.48 vs $0.51 (adj); $0.57 vs $0.43 (GAAP)` → 4 facts with basis; `Beat (+7.7%)` → attribute | Left as untyped cells (86 of 114 tech cells untyped) | FX02, FX10 |
| 5 | **Rank-matrix cells not typed** | `IVN (+58%)` → a `rank` fact plus the embedded value; `IVN (3/8)` → `rank` + `count` | 0 rank/count facts; all 56 facts have role `actual` | FX06, FX08; the `rank_order` and `count_claim` validators need these |
| 6 | **Trend lines not extracted** | `Payments penetration ↑ (62% → 68%)` → `trend` fact (low 62, high 68); `Beat/Met 6/8` → `count` | none | FX07; the `trend_direction` validator |
| 7 | **No entity aliases** | Aliases from the documents: the company column (`IVN` → `Ivanhoe Mines`) and `### IVN — Ivanhoe Mines Ltd` headings | 0 aliases, so "Ivanhoe" and "Shopify" don't resolve | Every question that names a company rather than a ticker. All 30 RBC questions do. |
| 8 | **30 entities for 12 companies** | One entity per company | 30. The first column of every table becomes an entity: quarter rows (`Q2 2026 (Jun 30)`, `Q1 2025 (Mar 31)`, …), `Field \| Value` labels (`Market Cap`, `Short interest`, `Next earnings date`, …) and screening rows (`**Market Cap**`, `**EPS Beat Rate (8Q)**`) | Pollutes `inspect_dataset` and entity resolution; the SHOP quarterly facts are attached to quarter "entities" instead of SHOP |
| 9 | **Currency on cells that override the header** | `C$17.1B` under `Mkt Cap (USD)` is CAD (the cell wins); IVN's market-cap cell | The IVN comparison-table market cap wasn't found as a fact | FX08 |
| 10 | **Chunk metadata and embeddings** | Chunks carry `heading_path` with section, company and quarter (e.g. `6. Company Performance > IVN — Ivanhoe Mines Ltd > Q2 2026 > 1. Headline Results`), and `chunk.embedding` is populated | 65 chunks, but **`heading_path` is empty on all of them** and **0 have embeddings** | Every RBC identification question (see below) |

## Why gap 10 matters most for judging day

RBC's sample questions (`question-set.md`) are all **"which company…"** questions whose clues live in the quarterly narrative. Examples: "stock fell 5.83% on the release date", "first anodes", "GAAP loss optics". The engine finds a candidate by searching chunks for each clue. It can only name the company and quarter if the chunk knows which company and quarter it belongs to. Please make sure:

- each `<details>` block becomes chunks whose `heading_path` includes the company heading and the quarter from its `<summary>`;
- each numbered or bolded sub-section (`**1. Headline Results**`, `**Market reaction:**`) is its own chunk, so the evidence is specific;
- chunk embeddings are populated (`chunk.embedding`), because the questions paraphrase the source ("crushed consensus by 13%" vs "Beat (+13%)").

## Already good

- Snapshots, hashes and line-preserving canonical text match the fixture exactly. Every span the engine cited verified word for word.
- Comparison-table cells come through with correct values, scale, and header currency (`Mkt Cap (USD)` → USD).
- The dataset/version model, the ready status and version isolation work with the engine as-is.

## Still to agree (from Checkpoint 0)

- Add the `htn_engine` role grants (`tests/engine/grants.sql`) to the first migration and the root compose setup. Today the engine can connect as `harness`, which has full write access.
- `contracts/repositories.py` says A implements `EvidenceRepository`, but B implemented `PgEvidenceRepository` (per the work plan's B1.1). Update the docstring and bump the contract to 1.1.
- FX08 in `contracts/fixture/questions.json` no longer requires specific citation lines. The currency mix appears in two places.
