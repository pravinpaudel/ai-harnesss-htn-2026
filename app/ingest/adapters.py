from __future__ import annotations

import mimetypes
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from contracts.models import IngestRequest, SourceType


@dataclass(frozen=True)
class SnapshotDocument:
    name: str
    content: bytes
    media_type: str
    metadata: dict


@dataclass(frozen=True)
class SnapshotCapabilities:
    source_type: str
    tools: list[dict]
    notes: list[str]


class FileSourceAdapter:
    """Snapshots supported local input without mutating or interpreting it."""
    supported_suffixes = {".md", ".markdown", ".csv", ".json", ".txt"}

    def discover(self) -> SnapshotCapabilities:
        return SnapshotCapabilities(source_type="file", tools=[], notes=["local file adapter"])

    def snapshot(self, request: IngestRequest) -> list[SnapshotDocument]:
        if request.source != SourceType.file or not request.path:
            raise ValueError("FileSourceAdapter requires a file IngestRequest")
        source = Path(request.path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        candidates = sorted(p for p in (source.iterdir() if source.is_dir() else [source]) if p.is_file())
        documents: list[SnapshotDocument] = []
        for path in candidates:
            if path.suffix.lower() not in self.supported_suffixes:
                continue
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            documents.append(SnapshotDocument(path.name, path.read_bytes(), media_type, {"source_path": str(path)}))
        if not documents:
            raise ValueError("no supported files found (.md, .csv, .json, .txt)")
        return documents


class McpClient(Protocol):
    """Small compatibility boundary for an MCP SDK/client implementation."""
    def list_tools(self) -> list[dict[str, Any]]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class McpSourceAdapter:
    """Snapshots MCP tool output without assuming a vendor-specific response shape."""
    def __init__(self, client: McpClient) -> None:
        self.client = client

    def discover(self) -> SnapshotCapabilities:
        return SnapshotCapabilities(source_type="mcp", tools=self.client.list_tools(), notes=["capabilities discovered from MCP server"])

    def snapshot(self, request: IngestRequest) -> list[SnapshotDocument]:
        if request.source != SourceType.mcp:
            raise ValueError("McpSourceAdapter requires an MCP IngestRequest")
        tools = self.client.list_tools()
        tool = request.mcp_tool or self._default_tool(tools)
        arguments = self._arguments(tool, tools)
        payload = self.client.call_tool(tool, arguments)
        if isinstance(payload, dict) and payload.get("isError"):
            # A tool error must fail the ingest; never snapshot the error text as if it were a document.
            detail = " ".join(item.get("text", "") for item in payload.get("content", []) if isinstance(item, dict))
            raise RuntimeError(f"MCP tool {tool!r} returned an error: {detail.strip()[:500]}")
        items = self._documents(payload)
        documents: list[SnapshotDocument] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                raise ValueError(f"MCP document {index} is not a text document")
            documents.append(SnapshotDocument(item.get("name", f"mcp-{index}.md"), item["content"].encode(),
                                              item.get("media_type", "text/markdown"), item.get("metadata", {})))
        if not documents:
            raise ValueError("MCP tool returned no text documents")
        # what the server offered and exactly what was called, for the dataset's audit record
        self.last_capabilities = {
            "tools": [{k: item.get(k) for k in ("name", "description", "inputSchema")} for item in tools],
            "tool_called": tool, "arguments": arguments,
            "documents_returned": len(documents),
            "payload_shape": sorted(payload.keys()) if isinstance(payload, dict) else type(payload).__name__,
        }
        return documents

    @staticmethod
    def _default_tool(tools: list[dict[str, Any]]) -> str:
        """Choose the standard financial-data tool, or the only tool a server offers."""
        names = [tool.get("name") for tool in tools if isinstance(tool.get("name"), str)]
        if "financialDataRetrieval" in names:
            return "financialDataRetrieval"
        if len(names) == 1:
            return names[0]
        available = ", ".join(names) or "none"
        raise ValueError(f"MCP server did not identify one data tool; available tools: {available}")

    @staticmethod
    def _arguments(tool: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Check the tool is offered and send only what its input schema declares. The dataset name is
        our own snapshot namespace, never a tool parameter, so a schemaless tool is called with {}."""
        schema = next((item.get("inputSchema") or {} for item in tools if item.get("name") == tool), None)
        if schema is None:
            raise ValueError(f"MCP tool {tool!r} not offered by the server")
        return {}

    @staticmethod
    def _documents(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and isinstance(payload.get("documents"), list):
            return payload["documents"]
        if isinstance(payload, dict) and isinstance(payload.get("structuredContent"), dict):
            structured = payload["structuredContent"]
            if isinstance(structured.get("documents"), list):
                return [{**item, "name": item.get("name", item.get("filename"))}
                        for item in structured["documents"] if isinstance(item, dict)]
        # Standard MCP tool results commonly carry one or more text content items.
        texts = [item["text"] for item in payload.get("content", []) if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)] if isinstance(payload, dict) else []
        documents: list[dict[str, Any]] = []
        for index, value in enumerate(texts):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                documents.append({"name": f"mcp-{index}.md", "content": value})
                continue
            if isinstance(decoded, dict) and isinstance(decoded.get("documents"), list):
                documents.extend(decoded["documents"])
            elif isinstance(decoded, list):
                documents.extend(item for item in decoded if isinstance(item, dict))
            elif isinstance(decoded, dict) and isinstance(decoded.get("content"), str):
                documents.append(decoded)
        return documents
