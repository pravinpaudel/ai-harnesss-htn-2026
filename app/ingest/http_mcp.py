"""Streamable HTTP MCP client for the supplied Hack the North RBC endpoint."""
from __future__ import annotations

import json
from typing import Any

import httpx


class HttpMcpClient:
    """Minimal stateful JSON-RPC MCP client with strict response validation."""
    def __init__(self, url: str, *, timeout_seconds: float = 120) -> None:
        self.url = url
        self._http = httpx.Client(timeout=timeout_seconds, headers={"Accept": "application/json, text/event-stream"})
        self._session_id: str | None = None
        self._request_id = 0

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        self._request_id += 1
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        response = self._http.post(self.url, headers=headers, json={"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params})
        response.raise_for_status()
        self._session_id = response.headers.get("mcp-session-id", self._session_id)
        payload = self._event_payload(response.text)
        if "error" in payload:
            raise RuntimeError(f"MCP {method} failed: {payload['error']}")
        return payload["result"]

    @staticmethod
    def _event_payload(body: str) -> dict[str, Any]:
        for line in body.splitlines():
            if line.startswith("data: "):
                value = json.loads(line[6:])
                if isinstance(value, dict):
                    return value
        raise RuntimeError("MCP response did not contain an SSE JSON-RPC data event")

    def initialize(self) -> dict[str, Any]:
        return self._call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                           "clientInfo": {"name": "finance-research-harness", "version": "0.1.0"}})

    def list_tools(self) -> list[dict[str, Any]]:
        if not self._session_id:
            self.initialize()
        result = self._call("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list returned an invalid tools collection")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self._session_id:
            self.initialize()
        return self._call("tools/call", {"name": name, "arguments": arguments})
