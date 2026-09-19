# Checkpoint 3 — Phase 2 rehearsal

Judging day runs the harness on documents we have never seen. Everything measured until now came
from three reports we had been reading while building the parser, so the scores said how well the
harness handles *these* documents, not documents of this *kind*.

The rehearsal removes that doubt: it rewrites the known corpus with generic structural changes,
keeps every fact, remaps the evaluation's citation anchors and the expected facts to the new line
numbers, and runs the unchanged CLI over each variant. Nothing is changed between ingest and eval,
and no fix is allowed to name a question, a company or a section number.

## How to run it

See `evals/README.md`. `evals/variants.py` writes the variant corpora, `evals/rehearsal.py` ingests
them into a throwaway database and scores each one.

| Variant | What changes |
|---|---|
| `reorder` | sections, company subsections and quarter blocks in a different order |
| `relabel` | section names without numbers, renamed table headers and block labels, "vs $X est" → "against a $X consensus" |
| `heading_style` | `### ABX — Barrick Mining Corporation` → `### Barrick Mining Corporation (ABX)` |
| `columns` | every table's columns permuted (the identifier column is no longer first) |
| `periods` | `Q3 2024` → `3Q24`, `Q3 FY2026` → `FQ3 2026`, `FY2025` → `fiscal 2025` |
| `drop_tables` | the cross-company tables (snapshot, metrics, rankings, news) removed |
| `flatten` | `<details><summary>…</summary>` blocks written as `####` headings |
| `combined` | all of the above at once, plus different file names |

## What the first run found

The engine still named the right company almost everywhere (30/30 on every variant except
`combined`, at 28/30), because that path leans on text search and the model. The evidence store was
the brittle part.

| Variant | Facts with a period | Expected facts | RBC suite |
|---|---|---|---|
| `baseline` | 1103 | 73/73 | 28/30 |
| `heading_style` | 1103 | **41/73** | **18/30** |
| `columns` | 972 | 63/73 | 27/30 |
| `periods` | **57** | 63/73 | 28/30 |
| `flatten` | 899 | 64/73 | 29/30 |
| `relabel` | 1114 | 69/73 | 29/30 |
| `combined` | **0** | **0/33** | 27/30 |

Six failure classes, each an assumption baked into the parser:

1. **Period grammar.** Only `Q3 2024`, `Q3 FY2026` and `FY2026` were understood. `3Q24`, `FQ3 2026`,
   `fiscal 2026` and `Q3'24` were invisible, so facts lost their period and the duplicate-claim and
   count validators lost their grouping.
2. **Heading style.** An entity was only recognised in `LABEL — Name` headings, so
   `Name (LABEL)` headings left every quarter fact without an owner.
3. **Period headings as entities.** With `<details>` blocks written as `#### 3Q24 — …` headings, the
   repeated period headings were themselves catalogued as entities.
4. **First column assumed to be the label.** Tables were read as "column 0 names the row", so
   permuted columns lost the identifier, the row label and the row's period.
5. **Estimate wording.** Only `X vs Y est` was read as actual vs estimate; `X against a Y consensus`
   and similar wordings produced no estimate, and with it no beat/miss verdict.
6. **Dated tables identified by header names.** News and catalyst tables were only recognised when
   their headers were spelled `Date` and `Event`.

A seventh problem showed up in the run itself: one OpenAI timeout ended a whole 30-question suite.

## The fixes (all general)

- `app/periods.py` — one period grammar shared by ingest and the engine, covering the spellings
  above, with the stated end date when the source gives one. Two-digit years need an attached marker
  so "Q3 24 stores" is not a period.
- `app/markdown.py` — `heading_subject()` reads both heading styles; `heading_period()` gives the
  period of a heading, so a report without `<details>` still has periods per span.
- `app/ingest/entities.py` — subjects come from either heading style; a heading that starts with a
  period is never an entity.
- `app/ingest/service.py` — `label_column()` finds the column that names each row (entity labels,
  then periods, then short distinct text, with a generic header as a tie-break); `date_column()` and
  `event_columns()` recognise dated tables by their content rather than by header wording.
- `app/ingest/extract.py` — the actual-vs-estimate pattern accepts "vs / versus / against /
  compared with" and "consensus / estimate / street / expected", including the parenthesised form.
  Numbers inside a period label are never read as values.
- `app/reasoning/engine.py` — a transient model error (timeout, connection, rate limit, 5xx) is
  retried once; if the model is still unavailable the run returns an audited, well-formed answer
  instead of raising. `app/eval/runner.py` records a failed case and carries on with the suite.

`tests/unit/test_structure_variants.py` pins each of these, and two scripted engine tests cover the
outage path.

## After the fixes

Every variant reaches full expected-fact recall and the same validation findings as the baseline,
and the RBC suite scores within one case of it. `MIN02` fails everywhere: it is the known baseline
miss, where the answer is right but cites a neighbouring quarter. The other one-off failures move
between runs (a suite varies by about one case), and all of them are citation-anchor misses on
questions whose company is still named correctly — 30/30 on every variant but the baseline run,
where one answer named the wrong company.

| Variant | Facts with a period | Expected facts | Findings | RBC suite |
|---|---|---|---|---|
| `baseline` | 1158 | 73/73 | 10 | 28/30 |
| `reorder` | 1158 | 73/73 | 10 | 29/30 |
| `relabel` | 1167 | 73/73 | 10 | 29/30 |
| `heading_style` | 1158 | 73/73 | 10 | 29/30 |
| `columns` | 1150 | 73/73 | 10 | 28/30 |
| `periods` | 1161 | 73/73 | 10 | 27/30 |
| `drop_tables` | 753 | 33/33 | 4 | 29/30 |
| `flatten` | 1158 | 73/73 | 10 | 29/30 |
| `combined` | 749 | 33/33 | 4 | 29/30 |

(`drop_tables` and `combined` have fewer expected facts because the tables holding them are gone;
the harness remaps the expectation to what the variant still contains.)

## What this does not cover

- Non-Markdown sources. Ingest accepts `.md`, `.csv`, `.json` and `.txt`; a PDF or HTML corpus would
  need a new adapter.
- A different domain. The transforms keep the sector reports' shape, so a wholly different document
  type (transcripts, filings) is still unproven.
- Questions unlike the RBC set. The suite is 30 identification questions plus the 10 contract cases.
