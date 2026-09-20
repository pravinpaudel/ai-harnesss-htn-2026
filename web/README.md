# Research Desk — web UI

The browser client for the Finance Research Harness. It asks the API a question, streams the
engine's steps while the answer is built, and puts the evidence for every claim one click away.

## Running it

```bash
npm install
npm run dev          # http://localhost:5173
```

`VITE_API_BASE_URL` points the client at the API (default `http://localhost:8000`). The API allows
the two local Vite origins by default; `HARNESS_CORS_ORIGINS` overrides that list. See
`docs/frontend-handover.md` for bringing up Postgres, the corpus and the API.

```bash
npm test             # vitest
npm run build        # tsc -b && vite build
```

## The two views

**Workspace** is the desk: the question library down the left, the conversation in the middle, and
the source viewer on the right when a citation is opened. The history menu returns to any saved
conversation; each answer's copy menu provides a clean answer or one with its supporting sources. Picking a
question loads it into the composer so it can be edited before it is asked; questions already asked
in this conversation are ticked.

**Sources** loads a dataset: paste an MCP URL or choose a file ingest, watch the job, then read what
landed — per-document lines, chunks, tables, facts and untyped cells, stage timings, validation
findings and the MCP capabilities as discovered. The dataset list below it picks which corpus
answers; that name is passed as `?dataset=` on every question.

## How it is put together

| Path | What it holds |
|---|---|
| `src/api/types.ts` | The contract as TypeScript. Every optional field is `?:` — the API omits nulls rather than sending them |
| `src/api/sse.ts` | Frame decoding for `POST /v1/queries/stream`, including split chunks and `: keep-alive` comments |
| `src/api/client.ts` | API routes, `BusyError` for a bounded-concurrency 503, and the fall back to `POST /v1/queries` when a stream cannot start |
| `src/session.ts` | The conversation id, kept in `localStorage` so history survives a reload |
| `src/format.ts` | Status, decline-reason and finding-rule copy; unit-aware number formatting |
| `src/components/` | `AnswerProse` (markers as buttons), `SourceList`, `EvidencePanel`, `AnswerCard`, `CopyMenu`, `ConversationHistory`, `StepList`, `DatasetBar`, `Composer`, `QuestionLibrary`, `ConnectView` |
| `src/data/questions.json` | The 30 supplied questions, keyed to a dataset name; generated from `question-set.md` |

Decisions worth knowing:

- **Markers are controls.** `[n]` in the answer markdown renders as a button that opens that source.
  Markers with no matching citation stay as plain text.
- **`span.exact_text` is never altered.** Quotes are clamped visually with CSS (which changes no
  characters) and shown in full in the panel. Nothing else in the UI is styled as source text.
- **Four statuses, four treatments.** `declined` leads with its reason, `partial` lists its
  limitations, `conflict` shows every claim side by side. `evidence_status` sits next to the status
  badge as the honest summary.
- **Waiting is shown, not hidden.** Steps arrive over SSE and render as they land; if the stream
  fails the client waits on the plain route instead, and a 503 becomes "at capacity, ask again".
- **History restores** from `GET /v1/runs?session_id=`, then each run's stored answer body.
- **Nothing about a sector is hard-coded.** The question
  library is keyed to a dataset name — a cold Phase-2 corpus gets generic prompts and a note, not
  questions about banks it has never seen.

## Raised with the backend

Nothing here blocks the UI; each is a small read-only addition that would make it better.

1. **Source context around a span.** Only `exact_text` is available, so "open the source at line
   1631" can show the cited line but not the lines around it. A read-only
   `GET /v1/documents/{document_name}/lines?start=&end=` (the engine role already has
   `repo.read_lines`) would let a reader see a quote in place.
2. **`GET /v1/runs/{run_id}` is untyped.** It returns a raw row dict whose `response` holds the
   stored answer; the client parses it defensively (object or JSON string). A `RunDetail` contract
   model would remove the guesswork.
3. **Cancelling a run.** Aborting the fetch drops the client, but the engine keeps working and holds
   its concurrency slot until it finishes. A cancel route, or dropping the slot when the SSE client
   disconnects, would free capacity during a demo.
4. **Ingest job state.** `GET /v1/ingests/{job_id}` answers 409 for a job that is queued, running or
   failed alike, so the connect screen cannot tell "still parsing" from "the worker died"; it shows
   elapsed time and a hint instead. A job-state field (state, attempts, last error) would fix it.
5. **Doc nit.** `docs/frontend-handover.md` lists `values[].period_label`; the contract has
   `period: {label, end, type}`. The client reads `period.label`.
