# Frontend handover

You are building the web UI for the Finance Research Harness. The backend is finished and tested;
this document is the contract between it and you.

## What this product is

A research assistant that answers questions about financial research reports **and proves every
claim**. Each answer carries `[n]` markers, and each `[n]` is a citation with the exact source text,
its document, and its line range — verified character-for-character against an immutable snapshot
before it is ever shown. An answer that cannot be supported is declined rather than guessed.

That is the product. The interface's job is to make the evidence as prominent as the prose: a user
should be able to check any sentence without leaving the page. Treat a declined or partial answer as
a first-class result to present well, not an error state to bury.

## What is yours, and what is not

**Yours:** `web/` — everything about the interface. There is a small React + Vite + TypeScript app
there already (chat view, `web/src/App.tsx`, `web/src/api.ts`, vitest tests). Replace it or build on
it as your design requires.

**Not yours:** `app/`, `contracts/`, `evals/`, `tests/`. That is the ingest pipeline, research engine,
evidence store and evaluation suites. Do not change them to make the UI easier. If you need something
the API does not expose, write down what you need and raise it — a backend change has to keep the
citation guarantees and the evaluation scores intact, so it is decided on the backend side.

Keep `uv run pytest -m "not live"` green (152 tests) and `cd web && npm test` green.

## Running it

```bash
docker compose up -d postgres            # Postgres 16 + pgvector, schema and roles applied on first start
uv run htn refresh                       # pull the corpus from the MCP server into a new dataset version
HARNESS_DATABASE_URL=postgresql+psycopg://harness:harness@localhost:5432/harness \
DATABASE_URL_ENGINE=postgresql+psycopg://htn_engine:htn_engine@localhost:5432/harness \
  uv run uvicorn app.api.main:app --port 8000
cd web && npm install && npm run dev     # http://localhost:5173
```

`VITE_API_BASE_URL` points the client at the API (default `http://localhost:8000`). The API allows
the two local Vite origins by default; `HARNESS_CORS_ORIGINS` is a comma-separated override.

## The API

`README.md` has the full route table. The four that matter to you:

| Route | Use |
|---|---|
| `POST /v1/queries` | Ask a question, get the whole answer when it is ready (10–20s) |
| `POST /v1/queries/stream` | The same answer as server-sent events: every step as it happens, then the answer |
| `GET /v1/datasets` | Datasets with their newest ready version: `ready_at`, `documents`, `facts`, `findings` |
| `GET /v1/runs?session_id=&limit=` | Past answers for one conversation; `GET /v1/runs/{run_id}` returns one run's stored answer and steps |

Both query routes take `?dataset=` (default `latest`) and a body of
`{"question": str, "session_id": str|null, "dataset_version": uuid|"latest", "budget": {...}}`.

### Streaming

```
event: run     {"run_id": "…", "dataset_version": "…"}
event: step    {"seq": 1, "kind": "policy", "name": "route", "detail": "routed as identification", "latency_ms": 28}
event: step    {"seq": 3, "kind": "tool_call", "name": "find_candidates", "detail": "precious metals streaming…"}
event: answer  {…the same body POST /v1/queries returns…}
event: done    {"run_id": "…"}
```

`kind` is `policy`, `tool_call`, `llm` or `verify`. `detail` is a short display line and may be
absent. A failure arrives as `event: error {"detail": "…"}`, not a dropped socket. A `: keep-alive`
comment arrives after 15 quiet seconds — ignore it. A real run is about 10 steps over 12 seconds, so
this is what turns a dead spinner into something worth watching.

### Three things that will bite you

1. **Null fields are omitted, not `null`.** The API uses `response_model_exclude_none`. Type every
   optional field as optional (`decline_reason?: DeclineReason`), not `T | null`.
2. **Concurrency is bounded.** Four answers at a time; beyond that the API returns `503` with
   `Retry-After: 5`. Handle it as "busy, try again", not as a failure.
3. **Answers take 10–20 seconds.** Design for that: streaming steps, an optimistic user bubble, and
   no layout jump when the answer lands.

## The answer shape

From `contracts/models.py` (`AnswerResponse`), the fields worth designing around:

| Field | Why it matters |
|---|---|
| `status` | `answered`, `partial`, `conflict`, `declined`. Four genuinely different outcomes; the current UI only badges three of them and treats `partial` almost like success |
| `evidence_status` | `fully_supported`, `partial_support`, `conflicting`. Nothing in the UI shows this today, and it is the honest summary of how good the answer is |
| `answer` | Markdown with `[n]` markers. The markers should be clickable and tie to the citation list |
| `citations[]` | `citation_id` (the `[n]`), `evidence_kind`, `verified` (always `true`), and `span`: `document_name`, `line_start`, `line_end`, `exact_text`, `heading_path` |
| `values[]` | The numbers the answer asserts, each with `label`, `value`, `unit`, `period_label` and the `citation_ids` backing it — a natural "key figures" element |
| `conflicts[]` | For `status: conflict`: `rule`, `explanation`, and at least two `claims` that disagree. Showing both sides is the point |
| `limitations[]` | Why an answer is partial — always present when `status` is `partial` |
| `decline_reason` | Always present when `status` is `declined`: `insufficient_evidence`, `false_premise`, `incompatible_currency`, `ambiguous_period_or_basis`, `out_of_corpus_entity`, `future_data`, `budget_exhausted` |
| `run_id`, `dataset_version` | Provenance: which run and which corpus version answered |
| `provenance` | `model`, `prompt_version`, `parser_version`, `source_hash`, and `usage` (tokens, cost, latency, tool rounds) |

`span.exact_text` is verified verbatim source text. Render it as a quotation and do not edit,
truncate mid-word, or reflow it in a way that changes the characters; if you must shorten it
visually, make the full text available. Never present anything else as if it were sourced text.

## What the existing UI does not do yet

Useful as a gap list, whatever you build: no `evidence_status`, no `values`, no `conflicts`, no
provenance or `run_id`, `[n]` markers are not linked to sources, sources sit behind a collapsed
`<details>`, and history disappears on reload even though `session_id` is sent with every question.

## Definition of done

- The four statuses each read correctly and honestly at a glance.
- Citations are reachable from the claim that uses them, with the verbatim quote and its document
  and line range.
- Streaming progress while an answer is being built, degrading to a sensible wait if the stream
  fails.
- Conversation history restored after reload from `session_id`.
- Which dataset answered, and how fresh it is.
- `503` and `event: error` handled as ordinary, explained states.
- `npm test` green, `npm run build` clean, and the Python suite untouched and still passing.
- Works at a narrow window width and on a phone; keyboard and screen-reader usable.
