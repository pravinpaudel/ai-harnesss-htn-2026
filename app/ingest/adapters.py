from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

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
    supported_suffixes = {".md", ".markdown", ".csv", ".json", ".xlsx", ".pdf", ".txt"}

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
            raise ValueError("no supported files found (.md, .csv, .json, .xlsx, .pdf, .txt)")
        return documents
