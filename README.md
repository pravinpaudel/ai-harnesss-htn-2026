# FinRet Research Desk

FinRet is an evidence-first financial research workspace. It ingests local files or an MCP data source into immutable dataset versions, then answers questions with source-grounded citations. The browser client is designed for an investment associate: select a source, ask or choose a question, open a citation to read the exact supporting text, and return to saved conversations.

## What runs where

| Component | Purpose | Default address |
| --- | --- | --- |
| PostgreSQL + pgvector | Datasets, evidence, jobs, and answer audit trail | `localhost:5432` |
| API | Research, dataset, ingest, health, and history endpoints | `http://localhost:8000` |
| Worker | Processes queued ingest jobs | no HTTP port |
| Web client | FinRet Research Desk browser UI | `http://localhost:5173` |
| Adminer (optional) | PostgreSQL browser supplied by Docker Compose | `http://localhost:8080` |

The API and worker share PostgreSQL and raw snapshot storage. The worker must be running for ingests submitted through the Sources screen to finish.

## Prerequisites

Install these before starting:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with Docker Compose
- [Node.js](https://nodejs.org/) 20+ and npm, for the web client
- [uv](https://docs.astral.sh/uv/) and Python 3.12+, only for native CLI/API/worker commands and Python tests
- An `OPENAI_API_KEY` for research answers, embeddings, and evaluation commands
- An HTTP(S) MCP endpoint if you want to ingest an MCP corpus

## Quick start: full application with Docker

This is the recommended local setup. It runs PostgreSQL, the API, and the ingest worker in containers; run the Vite web client separately for hot reload.

```bash
git clone https://github.com/pravinpaudel/ai-harnesss-htn-2026.git
cd ai-harnesss-htn-2026

# Create your local configuration. Do not commit .env.
cp .env.example .env
```

Edit `.env` and set at least the following values:

```dotenv
OPENAI_API_KEY=your-api-key
HARNESS_MCP_URL=https://your-provider.example/mcp
# Optional: change the local label shown in FinRet.
HARNESS_MCP_DATASET_NAME=mcp-financial-data
```

Start the backend services, install the browser client dependencies, and open the desk:

```bash
docker compose up -d --build

# Confirm that the API is ready.
curl http://localhost:8000/healthz

# In a second terminal:
cd web
npm ci
npm run dev
```

Open `http://localhost:5173`. On **Sources**, paste an MCP URL (or use the configured one), name the dataset, and select **Add source**. Wait for the ingest to report **Ready**, then switch to **Research** and ask a question.

### Daily startup

After the first setup, use:

```bash
docker compose up -d
cd web && npm run dev
```

Useful service commands:

```bash
# Follow backend logs; use this first when a load does not progress.
docker compose logs -f api worker

# Rebuild API and worker after changing Python code.
docker compose up -d --build api worker

# Stop containers while preserving the database and raw snapshots.
docker compose down

# Destructive: delete all local database and raw-snapshot volumes.
docker compose down -v
```

## Use the desk

### Research

1. Select **Research**.
2. Keep the question library open on the left or collapse it with its close icon.
3. Select a benchmark question or write a question in the composer.
4. Read the answer and select a numbered citation marker to open the supporting source text on the right.
5. Use **Copy** for a clean answer or an answer with a source list.
6. Select **History** to reopen a saved conversation. Conversation labels use a compact version of the first question.
7. Select **New conversation** from either Research or Sources to clear the thread and return to Research.

### Sources

1. Select **Sources**.
2. Choose **MCP data source** and paste an `http://` or `https://` MCP URL, or choose **Local files** and provide a server-visible path.
3. Supply a meaningful dataset name, such as `rbc-q3-research`.
4. Select **Add source** and keep the page open until the report says **Ready**.
5. Use **Use for research** beside a completed dataset to make it the corpus for future questions.

MCP URLs entered in the browser are saved with the queued ingest request, so the worker uses the same endpoint later. URLs with embedded credentials are rejected; use an approved endpoint or deployment-level authentication instead.

## Native development setup

Use this when working on Python code, the CLI, or the API outside Docker. PostgreSQL still runs in Docker.

```bash
cp .env.example .env
uv sync --extra dev
docker compose up -d postgres

# On a new, empty database Docker applies schema.sql and grants automatically.
# For a separately created empty database, initialise it explicitly:
uv run htn db init
```

Run the API and worker in separate terminals:

```bash
# Terminal 1
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2
uv run python -m app.worker.main

# Terminal 3
cd web
npm ci
npm run dev
```

With the default `.env.example` database URL, native processes connect to the Docker database at `localhost:5432`. For a non-default database, update `HARNESS_DATABASE_URL` and, when appropriate, `DATABASE_URL_ENGINE`.

## Configuration

Settings load from environment variables and then `.env`. Copy `.env.example` rather than creating a configuration from scratch.

| Variable | What it controls | Default |
| --- | --- | --- |
| `OPENAI_API_KEY` | OpenAI credentials for answers and embeddings | required for research |
| `HARNESS_DATABASE_URL` | Write-capable API, ingest, and worker PostgreSQL URL | local `harness` database |
| `DATABASE_URL_ENGINE` | Read-only research-engine PostgreSQL URL | `HARNESS_DATABASE_URL` |
| `HARNESS_RAW_STORAGE_PATH` | Immutable raw source snapshot directory | `.data/raw` |
| `HARNESS_MCP_URL` | Default MCP source for `htn refresh` and the Sources form | unset |
| `HARNESS_MCP_FINANCIAL_DATA_TOOL` | MCP tool used to fetch the corpus | `financialDataRetrieval` |
| `HARNESS_MCP_DATASET_NAME` | Default local name for MCP data | `mcp-financial-data` |
| `HARNESS_OPENAI_MODEL` / `HTN_MODEL` | Research model | `gpt-5.6-luna` |
| `HARNESS_OPENAI_EMBEDDING_MODEL` / `HTN_EMBEDDING_MODEL` | Embedding model | `text-embedding-3-small` |
| `HTN_MAX_TOOL_ROUNDS` | Maximum evidence-tool rounds per answer | `6` |
| `HTN_MAX_COST_USD` | Per-answer cost budget in USD | `0.50` |
| `HTN_MAX_LATENCY_MS` | Per-answer latency budget | `60000` |
| `HARNESS_API_MAX_CONCURRENT_QUERIES` | Concurrent API answers before a `503` response | `4` |
| `HARNESS_CORS_ORIGINS` | Comma-separated browser origins allowed by the API | local Vite origins |
| `VITE_API_BASE_URL` | Browser client's API base URL | `http://localhost:8000` |

For the Docker API and worker, `.env` is available through the repository bind mount. Compose explicitly sets their in-container database URLs, while values such as `OPENAI_API_KEY` and `HARNESS_MCP_URL` are read from `.env` by the application.

## CLI reference

Install Python dependencies first with `uv sync --extra dev`. The CLI reads the same `.env` file as the API.

| Task | Command |
| --- | --- |
| List commands and options | `uv run htn --help` |
| Print version | `uv run htn version` |
| Initialise an empty external database | `uv run htn db init` |
| Ingest a local file or directory | `uv run htn ingest /path/to/corpus --dataset-name my-corpus` |
| Snapshot the configured MCP corpus | `uv run htn refresh` |
| Inspect a dataset | `uv run htn dataset show --dataset mcp-financial-data` |
| Inspect a dataset as JSON | `uv run htn dataset show --dataset mcp-financial-data --json` |
| Ask a cited question | `uv run htn ask "Which company has the highest revenue growth?"` |
| Ask a named dataset | `uv run htn ask "What changed?" --dataset my-corpus` |
| Group an answer into a CLI conversation | `uv run htn ask "What changed?" --session demo-session-1` |
| Print raw answer JSON | `uv run htn ask "What changed?" --json` |
| List data-quality findings | `uv run htn conflicts --dataset my-corpus` |
| List findings as JSON | `uv run htn conflicts --dataset my-corpus --format json` |
| Replay an audited run | `uv run htn trace <run-id>` |
| Replay a run as JSON | `uv run htn trace <run-id> --json` |
| Run an evaluation suite | `uv run htn eval --questions evals/rbc_sample.json` |

Local-file ingestion accepts `.md`, `.markdown`, `.csv`, `.json`, and `.txt`. Each changed source produces a new immutable dataset version. `htn refresh` fetches the complete MCP corpus and reuses an already-ready version when the combined content hash and parser version have not changed.

## HTTP API reference

The interactive OpenAPI page is available at `http://localhost:8000/docs` while the API is running.

| Method and route | Purpose |
| --- | --- |
| `GET /healthz` | API liveness and database round trip |
| `GET /v1/config` | Configured default MCP URL, tool, and dataset name |
| `POST /v1/ingests` | Queue a local-file or MCP ingest job |
| `GET /v1/ingests/{job_id}` | Get a completed ingest report |
| `POST /v1/queries?dataset=latest` | Return a complete cited answer |
| `POST /v1/queries/stream?dataset=latest` | Stream answer progress over server-sent events |
| `GET /v1/datasets` | List datasets and their newest ready versions |
| `GET /v1/datasets/{dataset}` | Get a dataset profile |
| `GET /v1/runs?session_id=&limit=` | List a conversation's saved answers |
| `GET /v1/runs/{run_id}` | Get a stored answer and its recorded steps |
| `GET /v1/conversations` | List recent conversation summaries |
| `GET /v1/conflicts` | List validation findings for a dataset |

### API examples

```bash
# Health and available datasets
curl http://localhost:8000/healthz
curl http://localhost:8000/v1/datasets

# Queue an MCP ingest. The worker must be running.
curl -X POST http://localhost:8000/v1/ingests \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "mcp",
    "dataset_name": "mcp-financial-data",
    "mcp_url": "https://your-provider.example/mcp"
  }'

# Check the job after receiving {"job_id":"..."}.
curl http://localhost:8000/v1/ingests/<job-id>

# Queue a local-file ingest. The path must exist inside the API/worker environment.
curl -X POST http://localhost:8000/v1/ingests \
  -H 'Content-Type: application/json' \
  -d '{"source":"file","path":"/workspace/contracts/fixture/raw","dataset_name":"fixture-api"}'

# Ask a question and save it in a conversation.
curl -X POST 'http://localhost:8000/v1/queries?dataset=mcp-financial-data' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which company has the highest revenue growth?","session_id":"demo-session-1"}'

# Stream the same answer. Events arrive as run, step, answer, and done.
curl -N -X POST 'http://localhost:8000/v1/queries/stream?dataset=mcp-financial-data' \
  -H 'Content-Type: application/json' \
  -d '{"question":"Which company has the highest revenue growth?","session_id":"demo-session-1"}'

# Restore saved conversation summaries and their runs.
curl 'http://localhost:8000/v1/conversations?limit=20'
curl 'http://localhost:8000/v1/runs?session_id=demo-session-1&limit=20'
```

Answers can take 10–20 seconds. When the configured concurrency limit is full, the API returns `503` with `Retry-After: 5`; retry after the supplied interval. Optional response fields are omitted instead of returned as `null`.

## Testing and quality checks

```bash
# Python contract and non-database tests
uv run pytest

# PostgreSQL-backed engine tests
docker compose -f tests/engine/compose.test.yml up -d
uv run pytest -m db
docker compose -f tests/engine/compose.test.yml down -v

# Tests that call the real OpenAI API; requires OPENAI_API_KEY and incurs API usage.
uv run pytest -m live tests/engine/test_live.py -s

# Shared contract checks
uv run python -m contracts.check

# Browser client tests and production build
cd web
npm test
npm run build
```

## Troubleshooting

| Symptom | Check or fix |
| --- | --- |
| Sources stays queued or never reaches Ready | Ensure `worker` is running: `docker compose ps` and `docker compose logs -f worker`. |
| Browser says research service is offline | Run `curl http://localhost:8000/healthz`; then inspect `docker compose logs -f api`. |
| API starts but questions fail | Confirm `OPENAI_API_KEY` is set in `.env` and a ready dataset exists at `GET /v1/datasets`. |
| MCP refresh fails | Verify `HARNESS_MCP_URL`, the endpoint is reachable from the worker, and its tool name matches `HARNESS_MCP_FINANCIAL_DATA_TOOL`. |
| Browser cannot reach API on another host | Set `VITE_API_BASE_URL=http://host:8000` before `npm run dev` and add the web origin to `HARNESS_CORS_ORIGINS`. |
| Database schema or privileges are missing | For a clean Docker volume, run `docker compose down -v && docker compose up -d`. For an empty external database, run `uv run htn db init`. |
| Local file ingest cannot find the path in Docker | Use a path visible inside the API and worker containers, for example `/workspace/...`, or add an appropriate volume mount. |

## Repository map

| Path | Contents |
| --- | --- |
| `app/` | FastAPI service, worker, ingestion, retrieval, reasoning engine, and CLI |
| `contracts/` | Pydantic contracts, JSON schemas, PostgreSQL schema, and fixtures |
| `web/` | React/Vite FinRet browser client; see [`web/README.md`](web/README.md) for UI details |
| `tests/` | Unit, API, ingestion, and PostgreSQL-backed integration tests |
| `evals/` | Evaluation suites; see [`evals/README.md`](evals/README.md) |
| `.env.example` | Complete local configuration template |

## Data and safety notes

- `.env`, database volumes, raw source snapshots, and local recovery backups are intentionally not committed.
- Treat every ingest as evidence preservation: raw documents are stored by SHA-256 and versions remain immutable.
- Citations identify the exact retained source span used to support a claim. Open the citation in the desk when traceability matters.
- Keep API keys and source credentials out of the browser, git history, and MCP URLs. The Sources UI intentionally rejects URLs with embedded credentials.
