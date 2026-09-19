from __future__ import annotations

import hashlib
from pathlib import Path


class ImmutableRawStorage:
    """Content-addressed local raw storage. Existing bytes are never replaced."""
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}"
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != content:
                raise RuntimeError("content-addressed raw storage collision")
        else:
            target.write_bytes(content)
        return key, digest

    def read(self, key: str) -> bytes:
        return (self.root / key).read_bytes()
