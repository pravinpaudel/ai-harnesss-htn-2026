"""Where in a document a span sits: heading path, enclosing <details> summary, and the entity it is about.

Uses only generic Markdown structure (`#` headings and `<details>/<summary>` blocks) of the stored
canonical text, never section numbers or domain vocabulary. The contract's `heading_path` is used
when the evidence store provides it; this fills the gap when it does not.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Optional

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*(?:\{#[^}]*\})?\s*$")
_TAG = re.compile(r"<[^>]+>")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,7}$")


@dataclass
class Outline:
    headings: list[tuple[int, int, str]] = field(default_factory=list)   # (line, level, text)
    blocks: list[tuple[int, int, str]] = field(default_factory=list)     # (start, end, summary text)
    _lines: list[int] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "Outline":
        o = cls()
        open_block: Optional[int] = None
        summary = ""
        for i, line in enumerate(text.split("\n"), start=1):
            m = _HEADING.match(line)
            if m:
                o.headings.append((i, len(m.group(1)), m.group(2).strip()))
            s = line.strip()
            if s.startswith("<details"):
                open_block, summary = i, ""
            elif "<summary>" in s and open_block is not None and not summary:
                summary = " ".join(_TAG.sub(" ", s).split())
            elif s.startswith("</details>") and open_block is not None:
                o.blocks.append((open_block, i, summary))
                open_block = None
        o._lines = [h[0] for h in o.headings]
        return o

    def path(self, line: int) -> list[str]:
        stack: list[tuple[int, str]] = []
        for ln, level, text in self.headings[: bisect_right(self._lines, line)]:
            stack = [x for x in stack if x[0] < level] + [(level, text)]
        return [t for _, t in stack]

    def block(self, line: int) -> Optional[str]:
        for start, end, summary in self.blocks:
            if start <= line <= end:
                return summary or None
        return None


@dataclass
class SpanContext:
    heading_path: list[str]
    block: Optional[str]           # the <summary> of the enclosing <details> block, e.g. "Q3 FY2026 — ..."
    entity: Optional[str]          # entity label the section is about, if a heading names one

    @property
    def label(self) -> str:
        parts = list(self.heading_path)
        if self.block:
            parts.append(self.block[:80])
        return " > ".join(parts)


def entity_from_path(path: list[str], labels: set[str]) -> Optional[str]:
    """Deepest heading whose leading token (before ' — ' / ' - ' / ':') is a known entity label."""
    for text in reversed(path):
        head = re.split(r"\s+[—–-]\s+|:", text, maxsplit=1)[0].strip()
        if head in labels:
            return head
    return None


def entities_mentioned(text: str, labels: set[str]) -> list[str]:
    """Ticker-shaped entity labels mentioned as whole words (case-sensitive), in order of appearance."""
    found: dict[str, int] = {}
    for label in labels:
        if not _TICKER.match(label):
            continue
        m = re.search(rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])", text)
        if m:
            found[label] = m.start()
    return sorted(found, key=found.get)


def period_of(block: Optional[str]) -> Optional[str]:
    """Leading period label of a <summary>, e.g. 'Q3 FY2026 (Jul 31, 2026)' from 'Q3 FY2026 (Jul 31, 2026) — Full Summary'."""
    if not block:
        return None
    return re.split(r"\s+[—–]\s+", block, maxsplit=1)[0].strip() or None
