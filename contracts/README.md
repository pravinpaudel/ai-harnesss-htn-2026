# Shared contract — v1.0

This folder is the "Shared contract — complete before parallel work" from `developer-work-plan.md`. It's the only thing Developer A (Evidence Platform) and Developer B (Research Engine) share until Checkpoint 1. A change needs both developers, a bump of `CONTRACT_VERSION` in `models.py`, and a migration if the schema changes.

Check everything with one command from the repo root:

```bash
uv run python -m contracts.check
```

## Contents

| Path | Contract item (work plan) | Owner | Used by |
|---|---|---|---|
| `schema.sql` | 1. Database and repository contract | A writes migrations from it | B reviews query needs |
| `repositories.py` | 1. Read-only repository interfaces, plus `SourceAdapter` and `IngestService` | A implements | B depends only on these Protocols |
| `models.py` | 2. Python service contract (all Pydantic models) | Both | Both; neither redefines them |
| `json-schema/` | JSON Schemas generated from `models.py` by `check.py` | Generated | CLI `--json` / API response tests |
| `fixture/` | 3. Fixture contract | Both | A's integration tests, B's engine tests |
| `examples/` | Known-good `AnswerResponse` objects (calculation, conflict, false-premise decline) | B | CLI/API contract tests |
| `known-corpus/expected_facts.csv` | 73 hand-checked facts from the three full reports | A | A4 known-corpus extraction regression tests |
| `check.py` | Verifies all of the above | — | CI, every checkpoint |

## Fixture

`fixture/raw/` holds two small documents cut verbatim from the supplied reports: `mining-excerpt.md` (132 lines) and `technology-excerpt.md` (84 lines). Line ranges are copied unchanged, with a blank line between non-adjacent ranges. `fixture/sources.json` maps every excerpt line back to its original file and line, and records each excerpt's SHA-256.

It covers everything the work plan asks for:

| Requirement | Where |
|---|---|
| At least two entities | 12 tickers across two sectors; IVN and SHOP in depth |
| Factual table cells | Mining and tech comparable-company tables, SHOP 8-quarter table, screening matrix, `Field \| Value` tables |
| Narrative chunks | IVN Q2 2026 and SHOP Q2 2026 full narratives, thesis/catalyst/risk bullets |
| A calculation | FX04: IVN revenue change Q1 → Q2 2026 (−7.79%), both operands cited |
| Conflicting claims | IVN beat count (3/8 vs 2 beats); SHOP Payments penetration (58% vs 62%); revenue-growth ranking out of order |
| Mixed currencies | Mining market caps: "Mkt Cap (USD)" column holding C$ values; USD/CAD ranked together |
| An unsupported question | FX09 (AISC for IVN, not in the corpus); FX10 (false premise) |
| Stable line references | The raw files are committed and hash-checked |
| Expected typed facts | `fixture/expected_facts.csv`: 143 facts with line ranges and character offsets |
| Expected validation findings | `fixture/expected_findings.json`: 5 findings (count_claim, rank_order, duplicate_claim, 2 × unit_currency_mix) |
| Ten evaluation questions | `fixture/questions.json`: lookup, comparison, calculation, narrative, 2 × conflict, currency ambiguity, decline, false premise |

Regenerate with `python -m contracts.fixture.build` and then `python -m contracts.examples.generate` (same `uv run python -m` prefix). Fact values are typed by hand in `fixture/build.py`; the script only computes positions and fails if any value's text isn't exactly where it claims to be. **Application code must never import `contracts/fixture` or read `expected_*` files.** They exist only for testing.

## Conventions

**Source spans.** `line_start`/`line_end` are 1-based and inclusive. `char_start`/`char_end` are 0-based, end-exclusive Python string (Unicode code point) offsets into the document's canonical text, which is the file decoded as UTF-8 with `\n` line endings. `exact_text == text[char_start:char_end]` always. Every span carries its document's SHA-256. The citation verifier checks both the text and the hash.

**Values.**
- `value` is fully expanded: `$5,292M` → `5292000000`, `58%` → `58`, `36 bps` → `36`.
- `scale` records what the source used (`1e6` for M).
- `original_value` is always the verbatim string.
- Ranges set `value_low`/`value_high` and leave `value` null.
- Trends (`62% → 68%`) put the start in `value_low` and the end in `value_high`, with the direction word or arrow in `value_text`.
- Counts (`3/8`) put N in `value` and the verbatim string in `value_text`.
- Rank cells (`IVN (+58%)`) produce two facts: a `rank` fact and the embedded value.

**Currency.** Currency comes from the cell (`C$` → CAD, `US$`, trailing `USD`/`CAD`) or from the column header (`Mkt Cap (USD)`); the cell wins. A bare `$` with no header currency is `null`, meaning not stated. Comparisons across different known currencies are rejected unless a source-backed conversion is cited. The fixture has one: IVN's `C$17.1B (~$12.4B USD)`.

**Periods.** `period_label` is as written. `period_end` is set only when the source states the date (`Q2 2026 (Jun 30)`) or for point-in-time values as of the document date. Expected facts leave it blank otherwise, and tests don't require implementations to infer it. Fiscal year-ends are never assumed.

**Entities and metrics.**
- Entities and aliases come from the dataset itself; nothing is pre-seeded.
- Expected facts carry `entity_label` (as written) and a `metric_hint`. The hint is for humans only: implementations choose their own metric keys, and tests match facts on document, line, entity, role and value, not on metric name.
- `metric_label` is the header or label as written, when there is one.

**Statuses.** `answered` needs at least one citation. `conflict` needs at least one `ConflictRef` with two or more claims. `declined` needs a `decline_reason`. `partial` needs `limitations`. `AnswerResponse` enforces all of these in its validator, and a `Citation` can only exist with `verified: true`.

## How each side tests against the contract

- **Developer A:** ingest `fixture/raw/` → a ready dataset version. Every row in `fixture/expected_facts.csv` must be found (same document, line, entity, role and value, within float tolerance). Every finding in `fixture/expected_findings.json` must be produced (same rule, and a fact on the claim line). Then run the same checks against `known-corpus/expected_facts.csv` after ingesting the three full reports.
- **Developer B:** until Checkpoint 1, build a fake `EvidenceRepository` backed by `fixture/expected_facts.csv` and `fixture/expected_findings.json`. Pass all 10 cases in `fixture/questions.json`. Every `AnswerResponse` must validate and match the shape of `examples/`.
- **Checkpoint 1:** swap the fake repository for A's real one. The same 10 cases must still pass.
