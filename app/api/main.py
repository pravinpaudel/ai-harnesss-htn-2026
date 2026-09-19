from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from uuid import UUID

from app.db import create_engine
from app.settings import settings
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from contracts.models import IngestRequest, IngestReport

app = FastAPI(title="Finance Research Harness", version="0.1.0")


def ingest_service() -> FileIngestService:
    return FileIngestService(create_engine(settings.database_url), ImmutableRawStorage(settings.raw_storage_path), settings.parser_version)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    with create_engine(settings.database_url).connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/v1/ingests", status_code=202)
def submit_ingest(request: IngestRequest) -> dict[str, str]:
    return {"job_id": str(ingest_service().submit(request))}


@app.get("/v1/ingests/{job_id}", response_model=IngestReport)
def ingest_report(job_id: str) -> IngestReport:
    try:
        return ingest_service().report(UUID(job_id))
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 409, detail=str(exc)) from exc
