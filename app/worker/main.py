"""Worker entry point. Job dispatch is introduced with the ingest service in A1.3."""
from __future__ import annotations

import logging
import time

from app.db import create_engine, create_session_factory
from app.db.jobs import JobRepository
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from app.settings import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    factory = create_session_factory(create_engine(settings.database_url))
    service = FileIngestService(create_engine(settings.database_url), ImmutableRawStorage(settings.raw_storage_path), settings.parser_version)
    while True:
        with factory.begin() as session:
            job = JobRepository(session).claim_next("ingest")
        if job is None:
            time.sleep(1)
            continue
        try:
            service.run_job(job.job_id)
        except Exception as exc:
            logging.exception("ingest job %s failed", job.job_id)
            with factory.begin() as session:
                current = session.get(type(job), job.job_id)
                JobRepository(session).fail(current, str(exc), settings.job_max_attempts)


if __name__ == "__main__":
    main()
