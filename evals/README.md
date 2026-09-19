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

Every case is scored on naming the right company (the ticker, matched as a whole word). 22 of the 30 also require at least one citation inside that company's quarter block in §6, where the answer key pins a single quarter with high confidence. That stops an answer from passing on the right name from the wrong evidence. Medium-confidence and multi-quarter cases check the company only.

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
