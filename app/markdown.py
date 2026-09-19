"""Generic Markdown structure: headings and <details>/<summary> blocks, by line.

Shared by ingest (to write heading_path) and the research engine (to derive context when a store
has none). Uses only Markdown structure, never section numbers or domain vocabulary.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Optional

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*(?:\{#[^}]*\})?\s*$")
_TAG = re.compile(r"<[^>]+>")


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

    def periods(self) -> list[str]:
        """Period labels of all <details> blocks, e.g. ['Q3 FY2026 (Jul 31, 2026)', ...]."""
        return [p for p in (period_of(b[2]) for b in self.blocks) if p]

    def block(self, line: int) -> Optional[str]:
        for start, end, summary in self.blocks:
            if start <= line <= end:
                return summary or None
        return None


_BOLD_LABEL = re.compile(r"^\s*(?:[-*]\s+)?\*\*([^*]{1,80}?)\s*:?\s*\*\*")


def block_label(first_line: str) -> Optional[str]:
    """'**1. Headline Results**' / '- **Top risks/avoid:** ...' -> '1. Headline Results' / 'Top risks/avoid'."""
    m = _BOLD_LABEL.match(first_line)
    return m.group(1).strip().rstrip(":") if m else None


def period_of(block: Optional[str]) -> Optional[str]:
    """Leading period label of a <summary>, e.g. 'Q3 FY2026 (Jul 31, 2026)' from 'Q3 FY2026 (Jul 31, 2026) — Full Summary'."""
    if not block:
        return None
    return re.split(r"\s+[—–]\s+", block, maxsplit=1)[0].strip() or None


_SUBJECT_SPLIT = re.compile(r"\s+[—–]\s+|\s+-\s+|:\s+")
_NAMED_LABEL = re.compile(r"^(?P<name>.+?)\s*\((?P<label>[A-Z][A-Z0-9.&-]{0,9})\)$")


def heading_subject(text: str) -> Optional[tuple[str, str]]:
    """(label, name) a heading is about, in either common style:
    'ABX — Barrick Mining Corporation [— tail]' / 'ABX: Barrick …' or 'Barrick Mining Corporation (ABX)[: tail]'."""
    parts = _SUBJECT_SPLIT.split(text.strip(), maxsplit=1)
    head = parts[0].strip()
    m = _NAMED_LABEL.match(head)
    if m:
        return m.group("label"), m.group("name").strip()
    if len(parts) > 1:
        rest = _SUBJECT_SPLIT.split(parts[1], maxsplit=1)[0].strip()
        return head, rest
    return None


def heading_period(path: list[str]) -> Optional[str]:
    """Period of the deepest heading that starts with one ('#### 3Q24 — Revenue …' -> '3Q24')."""
    from app.periods import leading_period
    for text in reversed(path):
        p = leading_period(text)
        if p:
            return p[0]
    return None

