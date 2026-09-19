"""MCP refresh persists one immutable corpus version per changed hash."""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.ingest.adapters import McpSourceAdapter
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from contracts.models import IngestRequest, SourceType
from tests.engine.conftest import db_url
from tests.engine.seed_fixture import reset_schema

pytestmark = pytest.mark.db


class CorpusClient:
    def __init__(self) -> None:
        self.documents = [
            {"name": "financials.md", "content": "# Financials\n\n| Company | Revenue |\n|---|---|\n| ACME | $10M |\n"},
            {"name": "mining.md", "content": "# Mining\n\nEvidence\n"},
        ]
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return [{"name": "financialDataRetrieval", "inputSchema": {"properties": {}, "type": "object"}}]

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"structuredContent": {"documents": deepcopy(self.documents)}}


@pytest.fixture(scope="module")
def engine():
    url = db_url()
    try:
        create_engine(url).connect().close()
    except OperationalError:
        pytest.skip("test database not running: docker compose -f tests/engine/compose.test.yml up -d")
    reset_schema(url)
    return create_engine(url)


def test_refresh_reuses_an_unchanged_corpus_and_versions_changed_content(engine, tmp_path):
    client = CorpusClient()
    service = FileIngestService(
        engine,
        ImmutableRawStorage(tmp_path / "raw"),
        "test-parser",
        mcp_adapter=McpSourceAdapter(client),
        source_uri="https://example.test/mcp#financialDataRetrieval",
    )
    request = IngestRequest(source=SourceType.mcp, mcp_tool="financialDataRetrieval", dataset_name="mcp-financial-data")

    first = service.run_sync(request)
    same = service.run_sync(request)

    assert client.calls == [("financialDataRetrieval", {}), ("financialDataRetrieval", {})]
    assert first.reused is False
    assert same.reused is True
    assert same.dataset_version_id == first.dataset_version_id
    assert len(same.documents) == 2
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM dataset_version")) == 1

    client.documents[0]["content"] = "# Financials\n\n| Company | Revenue |\n|---|---|\n| ACME | $11M |\n"
    changed = service.run_sync(request)

    assert changed.reused is False
    assert changed.dataset_version_id != first.dataset_version_id
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM dataset_version")) == 2
