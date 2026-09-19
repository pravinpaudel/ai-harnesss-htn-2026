"""htn db init: schema + read-only engine role; MCP ingest records the server's capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from contracts.models import IngestRequest, SourceType
from app.db.bootstrap import init_database
from app.ingest.adapters import McpSourceAdapter
from app.ingest.embeddings import NoopEmbeddingProvider
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from tests.engine.conftest import db_url

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def fresh():
    url = db_url()
    try:
        admin = create_engine(url)
        admin.connect().close()
    except OperationalError:
        pytest.skip("test database not running")
    with admin.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
    return admin


def test_init_creates_schema_then_is_idempotent(fresh):
    assert init_database(fresh) == {"schema_created": True, "grants_applied": True}
    assert init_database(fresh) == {"schema_created": False, "grants_applied": True}


def test_engine_role_reads_evidence_and_writes_only_its_audit_tables(fresh):
    init_database(fresh)
    with fresh.connect() as c:
        priv = lambda table, p: c.execute(text("SELECT has_table_privilege('htn_engine', :t, :p)"),  # noqa: E731
                                          {"t": table, "p": p}).scalar()
        for table in ("fact", "chunk", "document", "validation_finding", "entity"):
            assert priv(table, "SELECT") and not priv(table, "INSERT") and not priv(table, "UPDATE")
        for table in ("answer_run", "tool_event", "research_memory"):
            assert priv(table, "INSERT") and priv(table, "UPDATE") and not priv(table, "DELETE")


def test_mcp_ingest_records_capabilities(fresh, tmp_path):
    init_database(fresh)
    body = (ROOT / "contracts/fixture/raw/mining-excerpt.md").read_text()

    class Client:
        def list_tools(self):
            return [{"name": "financialDataRetrieval", "description": "docs",
                     "inputSchema": {"type": "object", "properties": {}}}]

        def call_tool(self, name, arguments):
            return {"isError": False, "content": [], "structuredContent": {
                "file_count": 1, "documents": [{"filename": "mining-excerpt.md", "content": body}]}}

    svc = FileIngestService(fresh, ImmutableRawStorage(tmp_path), "t", mcp_adapter=McpSourceAdapter(Client()),
                            embedder=NoopEmbeddingProvider())
    rep = svc.run_sync(IngestRequest(source=SourceType.mcp, mcp_tool="financialDataRetrieval", dataset_name="mcp"))
    caps = rep.mcp_capabilities
    assert caps["tool_called"] == "financialDataRetrieval" and caps["arguments"] == {}
    assert caps["documents_returned"] == 1 and caps["tools"][0]["name"] == "financialDataRetrieval"
    with fresh.connect() as c:
        stored = c.execute(text("SELECT mcp_capabilities->>'tool_called' FROM dataset_version "
                                "WHERE dataset_version_id = :v"), {"v": str(rep.dataset_version_id)}).scalar()
    assert stored == "financialDataRetrieval"
