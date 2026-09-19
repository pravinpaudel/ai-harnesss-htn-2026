# Web App Requirements — `htn` Research Harness

**Owner:** Developer C (web app lane)
**Status:** v0.1, ready to start
**Read first:** `project-plan.md` (§1 what we're building, §7 this lane, §10 demo), `contract/README.md`, `contract/answer.schema.json`

---

## 1. Purpose

The CLI (`htn`) is the product engine. The web app is how a **judge, an executive or an analyst** sees it. It has to make the Best Business Value pitch visible in 3 minutes on a projector. That means a cited answer you can click through to the source, the error report as a deliverable, and cost and audit information shown on the page rather than just claimed.

It is a **thin client over the harness**. It never parses Markdown, never calls an LLM and never computes an answer itself. Everything it shows comes from the harness's JSON output or from the read-only store.

**Primary user:** an RBC equity research associate preparing for a PM meeting.
**Secondary users:** a compliance reviewer checking an answer; a judge watching the demo.

---

## 2. Lane isolation (important)

The project runs in isolated lanes (see `project-plan.md` §4). The web app is a third lane and follows the same rule: **it depends only on published contracts, never on another lane's code.**

| The web app MAY use | The web app MUST NOT |
|---|---|
| `htn <command> --json` output (subprocess) | Import Lane 1 or Lane 2 Python modules |
| `store.duckdb`, **read-only** (sections, conflicts, documents) | Write to `store.duckdb` or `runs.duckdb` |
| `runs.duckdb`, **read-only** (for trace) | Parse the raw Markdown into facts |
| `raw/*.md` to display source lines | Call an LLM or the MCP server directly |
| `contract/answer.schema.json` for types | Compute or rewrite answers, citations or conflicts |

Until the CLI exists, build against **mock data** (§8). Swapping mocks for the real CLI must be a config change, not a code change.

---

## 3. Architecture

```
Browser (React SPA)
   │  HTTP/JSON  (API contract in §6)
   ▼
Web API (FastAPI, owned by the web app lane)
   ├── MockBackend  ── reads webapp/mocks/*.json          (default until CP1)
   └── CliBackend   ── runs `htn ... --json` as subprocess (after CP1)
         + read-only DuckDB queries against corpus/<name>/store.duckdb and runs.duckdb
         + reads corpus/<name>/raw/*.md for the source viewer
```

**Recommended stack** (change only with a reason):

| Layer | Choice | Why |
|---|---|---|
| Frontend | Vite + React + TypeScript | Fast setup, no SSR needed |
| Styling | Tailwind CSS | Speed; consistent spacing on a projector |
| Types | Generate TS types from `contract/answer.schema.json` (`json-schema-to-typescript`) | One source of truth for the Answer shape |
| Data fetching | TanStack Query | Loading, error and retry states for free |
| Backend | FastAPI (Python 3.10+) + `duckdb` | Same language as the harness; reads DuckDB natively |
| Packaging | `uv` for Python, `pnpm` for the frontend; one `make dev` starts both | Runs on a clean laptop |

---

## 4. Screens

Priorities: **P0** = needed for the demo, **P1** = strong business-value add, **P2** = only if time allows.

### 4.1 Ask (home) — P0

The first screen a judge sees.

- A large question box, with 3–5 suggested questions as chips (loaded from config, so we can change them for the Phase 2 corpus).
- **Answer card:**
  - **Status badge**, colour and text both: `Answered` (green), `Conflict` (amber), `Declined` (grey), `Partial` (blue). Never rely on colour alone.
  - `answer_text` with `[n]` footnotes rendered as clickable superscripts.
  - The typed `value` shown large when present (e.g. `$152.6M`, `Q2 2026`), with currency and period.
  - **Confidence** level and its one-line reason.
  - For `declined`: `decline_reason` in plain English plus `decline_detail`. For example: "Not in the documents. We looked for AISC for Ivanhoe; the report gives C1 cash cost instead."
  - **Cost strip:** tokens · $ · latency · tool calls, in small text under the answer.
  - **Provenance footer:** corpus hash (first 8 chars), model, contract version, run ID (links to Trace).
- **Citations list** under the answer. Each row shows the number, document, section heading, line range and quote.
- **Source viewer** (side panel, or a drawer on narrow screens). Clicking a footnote or citation opens the raw document at the cited lines:
  - The cited lines are highlighted and the quote is marked inside them.
  - It shows ±10 lines of context, with a "show more" control.
  - It shows the document name, section breadcrumb and line numbers.
  - It must render the raw Markdown **as text with line numbers**, not as rendered HTML. The point is to show exactly what the source says on those lines.
- **Conflict answers** show both claims side by side ("Claim A" and "Claim B"), each with its citation. Clicking either opens it in the source viewer.
- A **history** of this session's questions in a sidebar. Clicking one re-shows its stored answer (no re-run).

### 4.2 Conflicts (error report) — P0

The risk-reduction deliverable.

- A header count, e.g. "**6 contradictions found** in 3 documents".
- A table with severity, check type (human label: "Ranking out of order", "Count doesn't match", "Trend arrow wrong", "Sections disagree", "Mixed currencies"), company, description, expected vs observed.
- Filters by document, severity and check type.
- A row expands to show both claims with their source snippets. Each opens in the source viewer.
- **Export:** "Download report" as HTML and Markdown. This calls `htn conflicts --export`, so the file matches the CLI output. P1: a print stylesheet so it prints cleanly as a PDF.

### 4.3 Corpus (ingest) — P0

- Current corpus name, documents (name, sector, as-of date, line count, SHA-256 short), and totals for facts, chunks and conflicts.
- An **"Ingest from RBC"** button that calls `POST /api/ingest`. It shows progress, then the ingest report: documents, facts, chunks, conflicts, seconds and warnings. This is demo step 2, so the result must be large and legible, e.g. "3 documents · 2,140 facts · 6 contradictions · 18.2 s".
- A corpus switcher when more than one corpus exists (e.g. `sample` vs `phase2`).

### 4.4 Batch (meeting prep) — P1

- Paste questions (one per line) or upload a `.txt`/`.json` file.
- Run them, with per-question progress and status badges filling in as they complete.
- A results table: question, status, short answer, citations count, cost.
- **Export memo:** HTML or Markdown from `htn batch --out`, with all answers and footnotes. This is what the analyst sends the PM.
- Totals: questions, answered/conflict/declined counts, total $, total time.

### 4.5 Brief (sector one-pager) — P1

- Pick a sector and generate a brief. It renders a cited one-pager using the same footnote and source-viewer behaviour as Ask.
- Print/export.

### 4.6 Trace (audit trail) — P1

- Opened from any answer's run ID, or by pasting a run ID.
- A timeline of the run: the question, then each tool call (name, arguments, row/result count, ms), the facts and chunks used, and the final answer.
- A header with the corpus SHA, model, prompt version, contract version, total tokens and $.
- A "Replay" note saying the same corpus + model + prompt version should give the same evidence.
- Read-only. This is the compliance screen, so keep it plain and precise.

### 4.7 Scorecard — P1

- The latest `htn eval` results, one card per axis: Accuracy (by question type), Grounding (citation precision, verified-quote rate), Honesty (conflict recall, false-answer rate on unanswerables), Efficiency (tokens/answer, $/answer, p50/p95 latency, cold-ingest time).
- A **baseline comparison** row: our harness vs the long-context baseline on accuracy and tokens. This is the Top Tech Performance headline.
- The date and question count of the eval run.

### 4.8 Settings — P2

- Read-only display of the model/provider, local-only mode, budget cap and current corpus. Editing can stay in the CLI.

---

## 5. Cross-cutting requirements

| Area | Requirement |
|---|---|
| **Honesty** | Never show an answer without its status badge. Never show a citation the API didn't return. Never hide `declined` or `partial` behind a friendlier message. |
| **Projector-ready** | Readable at 1280×720 from the back of a room: body ≥ 16px, answer text ≥ 18px, key numbers ≥ 32px. High contrast. Test on a projector-sized window. |
| **Responsive** | Works from 1280px down to 390px wide. The source viewer becomes a full-screen drawer on narrow screens. |
| **Light and dark** | Both themes, following the system setting by default, with a toggle. |
| **Accessibility** | WCAG 2.1 AA contrast. Keyboard: `/` focuses the question box, `Enter` submits, `Esc` closes the source viewer. Footnotes are real buttons with labels. |
| **Latency** | Show a skeleton answer immediately. If a request exceeds 3 s, show "Searching N facts…" and the tool-call count so far if the API provides it. Timeout at 60 s with a clear error. |
| **Errors** | CLI failures show the error message and a retry button, never a blank screen. |
| **Offline demo safety** | A `DEMO_MODE=cached` switch serves stored answers for the rehearsed demo questions if the live harness or LLM is down. The UI shows a small "cached" tag, so we never pass cached answers off as live. |
| **Security** | Binds to `localhost` by default. No auth needed for the hackathon. Subprocess calls pass arguments as a list (no shell), and the question is passed as one argument. |
| **No data leaves** | The web app makes no external network calls. Fonts and assets are bundled locally. |

---

## 6. API contract (v0 — proposed)

The web app lane owns and implements this API. Shapes marked **(contract)** already exist in `contract/`. Shapes marked **(proposed)** are new. Agree them with Lane 1/Lane 2 at CP1 and add them to `contract/` with a version bump, because the CLI's `--json` for these commands must emit the same shape.

| Method + path | Backed by | Response |
|---|---|---|
| `POST /api/ask` `{question, corpus?}` | `htn ask "<q>" --json --corpus <c>` | `Answer` **(contract)** |
| `POST /api/batch` `{questions[], corpus?}` | `htn batch <tmpfile> --json` | `{answers: Answer[], totals}` |
| `GET /api/conflicts?corpus=` | read-only `store.duckdb` (`conflict` + `fact` + `section`) | `ConflictView[]` **(proposed)** |
| `GET /api/conflicts/export?format=html\|md&corpus=` | `htn conflicts --export <fmt>` | file download |
| `GET /api/source?corpus=&doc_id=&line_start=&line_end=&context=10` | read `raw/<filename>` + `section` table | `SourceSpan` **(proposed)** |
| `GET /api/corpus` | read-only `store.duckdb` (`document`, counts) | `CorpusInfo` **(proposed)** |
| `POST /api/ingest` `{source: "mcp", name}` | `htn ingest --name <name> --json` | `IngestReport` **(contract README; JSON form proposed)** |
| `GET /api/brief?sector=&corpus=` | `htn brief --sector <s> --json` | `Brief` **(proposed)** |
| `GET /api/trace/{run_id}` | `htn trace <run_id> --json` | `Trace` **(proposed)** |
| `GET /api/eval/latest` | `htn eval --latest --json` | `Scorecard` **(proposed)** |

**Proposed shapes** (TypeScript notation):

```ts
type SourceSpan = {
  doc_id: string; filename: string; section_id: string; section_path: string[];
  line_start: number; line_end: number;          // the cited span
  context_start: number; context_end: number;    // what is returned
  lines: { n: number; text: string; cited: boolean }[];
  quote?: string;                                // to highlight inside cited lines
};

type ConflictView = {
  conflict_id: string; check_type: "ordering"|"count"|"trend_direction"|"cross_section"|"unit_mix";
  severity: "high"|"medium"|"low"; doc_id: string; entity_id: string|null; concept_key: string|null;
  description: string; expected_value: string|null; observed_value: string|null;
  claim_a: { text: string; doc_id: string; section_id: string; line_start: number; line_end: number; quote: string };
  claim_b: { text: string; doc_id: string; section_id: string; line_start: number; line_end: number; quote: string } | null;
  evidence_count: number;
};

type CorpusInfo = {
  name: string; corpus_sha256: string; contract_version: string;
  documents: { doc_id: string; filename: string; sector: string; title: string; as_of_date: string;
               line_count: number; snapshot_sha256: string }[];
  totals: { facts: number; chunks: number; conflicts: number; entities: number };
};

type IngestReport = { documents: number; facts: number; chunks: number; conflicts: number;
                      seconds: number; warnings: string[] };

type Brief = { sector: string; answer: Answer };   // brief is an Answer with a long answer_text

type Trace = {
  run_id: string; question: string; started_at: string; provenance: Answer["provenance"];
  steps: { i: number; kind: "tool_call"|"llm"|"verify"; name: string; args?: unknown;
           result_summary: string; ms: number; tokens?: number }[];
  facts_used: string[]; chunks_used: string[]; answer: Answer;
};

type Scorecard = {
  run_at: string; questions: number;
  accuracy: Record<string, number>;               // by question type
  grounding: { citation_precision: number; verified_quote_rate: number };
  honesty: { conflict_recall: number; false_abstention_rate: number; false_answer_rate: number };
  efficiency: { tokens_per_answer: number; usd_per_answer: number; p50_ms: number; p95_ms: number;
                cold_ingest_s: number };
  baseline?: { accuracy: number; tokens_per_answer: number; usd_per_answer: number };
};
```

**Claim text for `ConflictView`:** build it from the fact rows only by joining `value_text`/`value_num`/`value_low → value_high` with `exact_text`. Do not generate prose.

---

## 7. Configuration

`webapp/.env` (commit a `.env.example`):

```
HTN_BACKEND=mock            # mock | cli
HTN_BIN=htn                 # path to the CLI when HTN_BACKEND=cli
HTN_CORPUS_ROOT=../corpus   # contains <name>/{raw,store.duckdb,runs.duckdb}
HTN_DEFAULT_CORPUS=sample
DEMO_MODE=live              # live | cached
SUGGESTED_QUESTIONS=webapp/config/suggested.json
PORT=8787
```

For mock-mode development, point the corpus root at the fixture: `HTN_CORPUS_ROOT=../contract`, `HTN_DEFAULT_CORPUS=fixture`. Then `/api/source`, `/api/conflicts` and `/api/corpus` work against real data from day one.

---

## 8. Mock data (start here)

1. Build the fixture: `uv run --with duckdb python contract/build_fixture.py`.
2. Write `webapp/mocks/answers/F01.json … F15.json`, one `Answer` per question in `contract/fixture/questions.json`. Use the expected values from that file and **real citations**: doc, section and line numbers from the fixture store, with quotes copied verbatim from `contract/fixture/raw/`. Cover every status: `answered` (F01, F02, F04, F09), `conflict` (F06, F07, F08), `declined` (F12, F13) and one `partial` (make one up).
3. Validate every mock against `contract/answer.schema.json` in a test (`ajv` or Python `jsonschema`). If a mock fails, fix the mock, not the schema.
4. `MockBackend.ask(q)` matches the question text to a mock by exact match, then fuzzy match, and otherwise returns a `declined` answer with `decline_detail: "mock backend has no answer for this question"`.
5. Conflicts, source, and corpus info come from the fixture store (read-only), not from mocks.
6. Mock trace, brief, scorecard and ingest report as one JSON file each.

---

## 9. Milestones

Aligned to `project-plan.md` §9 checkpoints.

| By | Deliverable | Done when |
|---|---|---|
| Hour 3 (≈3 AM, CP1) | Repo scaffold, `make dev`, mock backend, Ask screen with status badges, footnotes and source viewer on the fixture | A judge could click a footnote and see the highlighted line |
| Hour 9 | Conflicts screen + export (mock export file), Corpus screen, generated TS types, schema-validation test for mocks | All P0 screens work on mocks |
| CP2 (3 PM Sat) | `CliBackend` wired to the real `htn` for `ask`, `conflicts`, `corpus`, `source` | Same screens, real data, no code changes beyond config |
| Hour 21 (9 PM, CP3) | Batch, Trace, Scorecard, Brief (P1); `DEMO_MODE=cached`; ingest button live | Full demo script (plan §10) runs end to end in the browser |
| 2 AM Sun (freeze) | Projector pass, dark mode, keyboard shortcuts, error states, clean-laptop install | Four demo run-throughs without touching the terminal |

If you fall behind: Ask + source viewer + Conflicts + Corpus (all P0) are the whole demo. Cut P1 screens in reverse order: Settings, Brief, Scorecard, Trace, Batch.

---

## 10. Acceptance checklist

- [ ] Every answer shows a status badge; `declined` and `partial` are visible and explained.
- [ ] Every footnote opens the source viewer at the exact cited lines, with the quote highlighted.
- [ ] A conflict answer shows both claims side by side, each linked to its source.
- [ ] The Conflicts screen lists every row of the `conflict` table and exports HTML/Markdown identical to `htn conflicts --export`.
- [ ] The Ingest screen shows documents, facts, conflicts and seconds after a live MCP ingest.
- [ ] Cost (tokens, $, ms) and provenance (corpus hash, model, run ID) appear on every answer.
- [ ] Trace shows every tool call for a run and the facts it used.
- [ ] Switching `HTN_BACKEND=mock` → `cli` needs no code change.
- [ ] All mocks validate against `contract/answer.schema.json`.
- [ ] No imports from Lane 1/Lane 2 code; no writes to any `.duckdb`; no external network calls.
- [ ] Legible at 1280×720; usable at 390px; light and dark; keyboard shortcuts work.
- [ ] `make dev` works on a clean laptop in under 5 minutes.

---

## 11. Out of scope

- User accounts, auth, multi-tenant anything.
- Editing facts, answers or conflicts in the UI.
- Chat/multi-turn conversation (each question is independent).
- Any LLM call, prompt or retrieval logic in the web app.
- Hosting; the demo runs on localhost.

---

## 12. Open questions for the team

1. Will Lane 2 add `--json` to `conflicts`, `brief`, `trace`, `eval` and `ingest` with the §6 shapes? Decide at CP1 and add the shapes to `contract/`.
2. Does `htn ask` stream progress (tool-call count) we can show during long answers, or only return at the end?
3. Which 5 suggested questions go on the Ask screen for the Phase 2 demo? Pick them at the CP3 rehearsal.
