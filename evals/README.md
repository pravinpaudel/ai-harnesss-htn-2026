# Evaluation suites

| Suite | Cases | Corpus | What it tests |
|---|---|---|---|
| `contracts/fixture/questions.json` | 10 | `contracts/fixture/raw/` (excerpts) | Contract behaviour: lookup, calculation, conflict, currency, decline, false premise |
| `evals/rbc_sample.json` | 30 | the three full reports | RBC's sample "which company…" questions; judging-day questions will be similar |

## RBC sample suite

Built from the answer key in `question-set.md`:

```bash
uv run python -m evals.build_rbc_sample
```

Every case is scored on naming the right company (the ticker, matched as a whole word) and on citing
the evidence the answer key itself points at. All 30 cases carry citation anchors, and any one of
them satisfies the case:

- each line the key's **Evidence** column lists, widened to the narrative unit that holds it — the
  quarter's `<details>` block, or the paragraph when the line sits outside one, because a report
  states the same fact in several subsections of a quarter and the key lists only one of them;
- the block of the quarter the key names, whether it marks that quarter certain or not;
- for a claim the key spans over several quarters ("Trailing 8Q", a year, a range), the company
  profile's own summary paragraph, which is where the report restates what its sector summary says.

Anchors stay small — a median of 2.1% of a report and at most 4.9% — so an answer that names the
right company from unrelated evidence still fails. `tests/unit/test_rbc_sample_anchors.py` holds
that line. The limit of the check: questions that genuinely share evidence (MIN02 and MIN09 both
describe the Antamina deal; MIN06 and MIN10 both describe TECK.B's trailing eight quarters) accept
each other's citations — 7 of the 28 same-company case pairs do. The suite measures each answer
against its own key, not against the other questions.

## Running it

```bash
docker compose up -d postgres
uv run htn db init            # schema (if empty) + read-only htn_engine role; compose does this on a fresh volume

mkdir -p /tmp/rbc && cp canadian-*-research.md /tmp/rbc/
uv run htn ingest /tmp/rbc --dataset-name rbc-sample

DATABASE_URL_ENGINE=postgresql+psycopg://htn_engine:htn_engine@localhost:5432/harness \
  uv run htn eval --dataset rbc-sample --questions evals/rbc_sample.json
```

Each question takes about 10–20 seconds. Every run is recorded in `answer_run`/`tool_event`; use `htn trace <run-id>` to see the clues, candidates and searches behind an answer.

## Phase 2 rehearsal (Checkpoint 3)

Judging day uses documents we have not seen. The rehearsal rewrites the three known reports with
generic structural changes, keeps every fact, remaps the RBC suite's citation anchors and the
known-corpus expected facts to the new line numbers, then runs the **unchanged** CLI on each variant.
No code or prompt changes happen between ingest and eval.

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

Run it against a throwaway database so the rehearsal never touches your working data:

```bash
docker run -d --name htn-phase2 -p 55498:5432 -e POSTGRES_USER=harness -e POSTGRES_PASSWORD=harness \
  -e POSTGRES_DB=harness -v $PWD/contracts/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro \
  -v $PWD/app/db/grants.sql:/docker-entrypoint-initdb.d/02-grants.sql:ro pgvector/pgvector:pg16

uv run python -m evals.variants /tmp/phase2          # variant docs + remapped suite + expected facts
uv run python -m evals.rehearsal /tmp/phase2 --db postgresql+psycopg://harness:harness@localhost:55498/harness --workers 9
docker rm -f htn-phase2
```

`docs/phase2-rehearsal.md` records what the first run found and how each gap was closed.
`/tmp/phase2/summary.json` has, per variant, the structure scores (entities, facts with a period,
spans without a heading path, expected-fact recall, findings) and the RBC score with each failure's class.

## Release check (Checkpoint 4)

One command takes a release candidate from a clean install to a signed scorecard:

```bash
uv run python -m evals.release_check            # every stage (~30 min; calls the model)
uv run python -m evals.release_check --quick    # everything but the two suites and the cold start (~2 min)
uv run python -m evals.release_check --stages compose,api,scorecard
```

It brings up Docker Compose under its own project name (`htn-release`) with `docker-compose.release.yml`,
so it gets its own volumes and ports (55496, 8010, 8081) and never touches a working stack. The stack
is torn down afterwards unless `--keep` is passed. Each stage has a bar it must clear:

| Stage | What it proves |
|---|---|
| `compose` | A fresh volume gets the contract schema and the read-only `htn_engine` role: 15 tables, engine reads evidence, cannot write it, can write its own audit rows |
| `db_init` | `htn db init` is safe to re-run on a prepared database |
| `mcp` | The configured MCP server ingests, records the tools it offered and the exact call, and a second refresh reuses the version instead of duplicating it |
| `corpus` | The three reports ingest and every hand-checked expected fact is in the store (73/73) |
| `suites` | The contract fixture and RBC suites, with every displayed citation verified verbatim |
| `coldstart` | The Phase 2 `combined` variant — a restructured corpus the parser has never seen — ingested and answered with no code change |
| `api` | `/healthz`, a query answered through the read-only role with verified citations, and the run, conflicts and dataset routes |
| `trace` | Any answer replays from the audit trail by run id, in both renderings |
| `scorecard` | Freezes contract, parser, prompt, schema, model and commit, and writes `docs/scorecard.md` and `release-scorecard.json` |

A stage that misses its bar stops the run and no scorecard is written.

### Reading a scorecard

- **A suite varies by about one case between runs.** The same build has scored 9/10 and 10/10 on the
  fixture suite and 27–30/30 on the RBC suite. Treat a one-case move as noise and look at which case
  moved; a repeated failure is a finding.
- **The cold-start line is the same 30 questions over a corpus restructured seven ways at once**
  (`evals/variants.py`), with the anchors remapped to the new line numbers. It runs a few cases below
  the known corpus, which matches the per-variant range in `docs/phase2-rehearsal.md`.
- **Citations are pass/fail, not a score.** Every displayed quote must verify verbatim against the
  stored snapshot; a suite with any unverified citation fails the release check outright.
