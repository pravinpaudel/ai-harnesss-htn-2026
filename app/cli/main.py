from __future__ import annotations

import typer

from app.db import create_engine
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from app.settings import settings
from contracts.models import IngestRequest, SourceType

app = typer.Typer(help="Finance research harness")


@app.command()
def version() -> None:
    """Print the current platform version."""
    typer.echo("htn 0.1.0")


@app.command()
def ingest(path: str, dataset_name: str = typer.Option(..., "--dataset-name")) -> None:
    """Ingest a local file or directory into a new immutable dataset version."""
    service = FileIngestService(create_engine(settings.database_url), ImmutableRawStorage(settings.raw_storage_path), settings.parser_version)
    report = service.run_sync(IngestRequest(source=SourceType.file, path=path, dataset_name=dataset_name))
    typer.echo(report.model_dump_json(indent=2))


if __name__ == "__main__":
    app()
