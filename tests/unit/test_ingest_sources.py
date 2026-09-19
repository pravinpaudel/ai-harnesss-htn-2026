from __future__ import annotations

import hashlib

import pytest

from app.ingest.adapters import FileSourceAdapter, McpSourceAdapter
from app.ingest.storage import ImmutableRawStorage
from contracts.models import IngestRequest, SourceType


def test_file_adapter_snapshots_supported_files(tmp_path):
    source = tmp_path / "report.md"
    source.write_text("# Report\n", encoding="utf-8")
    (tmp_path / "ignored.exe").write_bytes(b"no")
    docs = FileSourceAdapter().snapshot(IngestRequest(source=SourceType.file, path=str(tmp_path), dataset_name="fixture"))
    assert [(d.name, d.content) for d in docs] == [("report.md", b"# Report\n")]


def test_file_adapter_rejects_missing_path(tmp_path):
    request = IngestRequest(source=SourceType.file, path=str(tmp_path / "missing"), dataset_name="fixture")
    with pytest.raises(FileNotFoundError):
        FileSourceAdapter().snapshot(request)


def test_raw_storage_is_content_addressed_and_immutable(tmp_path):
    storage = ImmutableRawStorage(tmp_path)
    key, digest = storage.put(b"source bytes")
    assert digest == hashlib.sha256(b"source bytes").hexdigest()
    assert storage.read(key) == b"source bytes"
    assert storage.put(b"source bytes") == (key, digest)


def test_mcp_adapter_discovers_and_snapshots_text_documents():
    class Client:
        def list_tools(self):
            return [{"name": "corpus", "inputSchema": {"type": "object", "properties": {"dataset_name": {}}}}]

        def call_tool(self, name, arguments):
            assert (name, arguments) == ("corpus", {})
            return {"documents": [{"name": "source.md", "content": "# Source"}]}

    adapter = McpSourceAdapter(Client())
    assert adapter.discover().tools[0]["name"] == "corpus"
    docs = adapter.snapshot(IngestRequest(source=SourceType.mcp, mcp_tool="corpus", dataset_name="fixture"))
    assert docs[0].content == b"# Source"


def test_mcp_adapter_sends_only_declared_arguments_and_keeps_filenames():
    """The RBC endpoint's financialDataRetrieval takes no arguments and returns structuredContent.documents."""
    class Client:
        def list_tools(self):
            return [{"name": "financialDataRetrieval",
                     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]

        def call_tool(self, name, arguments):
            assert arguments == {}
            return {"isError": False, "content": [],
                    "structuredContent": {"file_count": 1, "documents": [
                        {"filename": "canadian-mining-research.md", "path": "financial-data/x.md", "content": "# M"}]}}

    docs = McpSourceAdapter(Client()).snapshot(
        IngestRequest(source=SourceType.mcp, mcp_tool="financialDataRetrieval", dataset_name="rbc"))
    assert [d.name for d in docs] == ["canadian-mining-research.md"]


def test_mcp_tool_error_fails_the_ingest_instead_of_becoming_a_document():
    class Client:
        def list_tools(self):
            return [{"name": "corpus", "inputSchema": {"properties": {}}}]

        def call_tool(self, name, arguments):
            return {"isError": True, "content": [{"type": "text", "text": "1 validation error"}]}

    try:
        McpSourceAdapter(Client()).snapshot(IngestRequest(source=SourceType.mcp, mcp_tool="corpus", dataset_name="x"))
    except RuntimeError as e:
        assert "validation error" in str(e)
    else:
        raise AssertionError("tool error was not raised")
