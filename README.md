# Finance Research Harness

An evidence-first finance research service. It ingests a local or MCP-provided
corpus into immutable dataset versions, retains source spans for citations, and
uses a bounded OpenAI tool loop to produce auditable, source-grounded answers.

## Architecture

```mermaid
flowchart LR
    researcher[Researcher]

    subgraph entrypoints[Entry points]
        cli[htn CLI]
        api[FastAPI API]
    end
    researcher --> cli
    researcher --> api

    subgraph sources[Evidence sources]
        local[Local files\nMD, Markdown, CSV, JSON, TXT]
        mcp[MCP financial-data endpoint]
    end

    subgraph ingest[Ingestion]
        ingest_service[FileIngestService\nsnapshot, canonicalize, parse]
        worker[PostgreSQL job worker]
        raw[Immutable raw storage]
    end
    local --> ingest_service
    mcp -->|htn refresh: fetch complete corpus| ingest_service
    api -->|queue ingest job| worker
    worker --> ingest_service
    cli -->|synchronous ingest or refresh| ingest_service
    ingest_service --> raw

    subgraph postgres[PostgreSQL + pgvector]
        versions[Datasets and immutable versions]
        evidence[Documents, source spans, chunks,\ntables, cells, facts, findings]
        jobs[Jobs]
        audit[Answer runs and tool events]
    end
    ingest_service -->|changed corpus: create version| versions
    ingest_service --> evidence
    ingest_service -->|unchanged hash: reuse ready version| versions
    api --> jobs
    worker --> jobs

    subgraph research[Research engine]
        repo[Read-only evidence repository]
        router[Router and policy gate]
        tools[Evidence tools and\ndeterministic calculator]
        llm[OpenAI Responses API]
    end
    cli -->|ask, eval, trace| router
    router --> repo
    repo --> evidence
    router <--> llm
    llm <--> tools
    tools --> repo
    router --> audit
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker Compose, for PostgreSQL with pgvector
- `OPENAI_API_KEY` to run `htn ask` or `htn eval`

## Quick start

Install the application and development dependencies:

```bash
uv sync --extra dev
```

Start the database, then apply the contract schema. Schema bootstrap is an
explicit, **empty-database-only** operation.

```bash
docker compose up -d postgres
uv run python -c "from app.db import create_engine; from app.db.bootstrap import apply_contract_v1; apply_contract_v1(create_engine('postgresql+psycopg://harness:harness@localhost:5432/harness'))"
docker compose exec -T postgres psql -U harness -d harness < tests/engine/grants.sql
```

Fetch and snapshot the complete MCP corpus. The command has no dataset-name
argument: it uses `HARNESS_MCP_DATASET_NAME` (default
`mcp-financial-data`). It fetches every document, computes a combined corpus
hash, and reuses the existing ready version without parsing or writing raw data
when that hash is unchanged.

```bash
uv run htn refresh
uv run htn dataset show
```

Set an API key and ask a question. `HARNESS_OPENAI_MODEL` is optional; the
default model is `gpt-5.6-luna`.

```bash
export OPENAI_API_KEY='your-api-key'
export HARNESS_OPENAI_MODEL='gpt-5.6-luna'
uv run htn ask "Which company has the highest revenue growth?"
```

## Important commands

| Task | Command |
| --- | --- |
| Show all CLI commands | `uv run htn --help` |
| Check the shared contracts | `uv run python -m contracts.check` |
| Run the default test suite | `uv run pytest` |
| Run database-backed engine tests | `docker compose -f tests/engine/compose.test.yml up -d` then `uv run pytest -m db` |
| Run live OpenAI tests | `uv run pytest -m live tests/engine/test_live.py -s` |
| Start the HTTP API | `uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000` |
| Start the ingest worker | `uv run python -m app.worker.main` |
| Start API, worker, and database in containers | `docker compose up --build` |
| Refresh the complete MCP corpus | `uv run htn refresh` |
| Ingest local files into a separately named dataset | `uv run htn ingest /path/to/corpus --dataset-name my-local-corpus` |
| Submit an async ingest job | `curl -X POST http://localhost:8000/v1/ingests -H 'content-type: application/json' -d '{"source":"file","path":"/workspace/contracts/fixture/raw","dataset_name":"fixture-api"}'` |
| Check the API and an ingest job | `curl http://localhost:8000/healthz` and `curl http://localhost:8000/v1/ingests/<job-id>` |
| Inspect validation findings | `uv run htn conflicts` |
| Run the RBC evaluation suite | `uv run htn eval --questions evals/rbc_sample.json` |
| Inspect an audited answer | `uv run htn trace <run-id>` |

For the complete RBC evaluation setup, see [evals/README.md](evals/README.md).

## Configuration

Settings are read from environment variables (or a local `.env` file).

| Variable | Purpose | Default |
| --- | --- | --- |
| `HARNESS_DATABASE_URL` | Ingest/API/worker PostgreSQL URL | local `harness` database |
| `DATABASE_URL_ENGINE` | Read-only engine PostgreSQL URL | `HARNESS_DATABASE_URL` |
| `HARNESS_RAW_STORAGE_PATH` | Directory for immutable raw snapshots | `.data/raw` |
| `OPENAI_API_KEY` | OpenAI API key | required for research commands |
| `HARNESS_OPENAI_MODEL` / `HTN_MODEL` | Research model | `gpt-5.6-luna` |
| `HARNESS_MCP_URL` | MCP endpoint for `htn refresh` | unset |
| `HARNESS_MCP_DATASET_NAME` | Local name for the complete refreshed MCP corpus | `mcp-financial-data` |

Local-file ingestion currently accepts `.md`, `.markdown`, `.csv`, `.json`, and
`.txt` files. Local ingests create a new immutable version. `htn refresh`
reuses a ready version when its full MCP corpus hash and parser version match;
otherwise it creates a new immutable version. Raw source documents remain
content-addressed by their individual SHA-256 hashes.
