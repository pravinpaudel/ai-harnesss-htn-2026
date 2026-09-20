from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from uuid import UUID

from app.db import create_engine
from app.settings import settings
from app.ingest.service import FileIngestService, get_ingest_service
from contracts.models import IngestRequest, IngestReport
from app.api.queries import router as queries_router

def create_app(*, cors_origins: list[str] | None = None) -> FastAPI:
    """Create the HTTP API with browser access limited to configured origins."""
    api = FastAPI(title="Finance Research Harness", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else list(settings.allowed_cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    api.include_router(queries_router)
    return api


app = create_app()


def ingest_service() -> FileIngestService:
    return get_ingest_service()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    with create_engine(settings.database_url).connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/v1/config")
def config() -> dict[str, str | None]:
    """What a client needs to fill the load form: the configured MCP endpoint and default corpus name."""
    return {"mcp_url": settings.mcp_url, "mcp_tool": settings.mcp_financial_data_tool,
            "dataset_name": settings.mcp_dataset_name}


@app.post("/v1/ingests", status_code=202)
def submit_ingest(request: IngestRequest) -> dict[str, str]:
    return {"job_id": str(ingest_service().submit(request))}


@app.get("/v1/ingests/{job_id}", response_model=IngestReport)
def ingest_report(job_id: str) -> IngestReport:
    try:
        return ingest_service().report(UUID(job_id))
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 409, detail=str(exc)) from exc
