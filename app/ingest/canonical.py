"""Canonical text and provenance helpers.

Canonical text is the only string source spans index into. It retains every line
and normalizes only newlines, as required by the shared contract.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    number: int
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class CanonicalDocument:
    text: str
    lines: tuple[Line, ...]

    def span_for_lines(self, start: int, end: int) -> tuple[int, int, str]:
        if start < 1 or end < start or end > len(self.lines):
            raise ValueError("invalid inclusive line range")
        first, last = self.lines[start - 1], self.lines[end - 1]
        return first.char_start, last.char_end, self.text[first.char_start:last.char_end]

    def span_for_text(self, line_number: int, start_column: int, end_column: int) -> tuple[int, int, str]:
        line = self.lines[line_number - 1]
        start, end = line.char_start + start_column, line.char_start + end_column
        return start, end, self.text[start:end]


def canonicalize_text(content: bytes) -> CanonicalDocument:
    """Decode UTF-8 source and make line endings deterministic without trimming."""
    text = content.decode("utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = text.split("\n")
    lines: list[Line] = []
    offset = 0
    for number, row in enumerate(rows, start=1):
        end = offset + len(row)
        lines.append(Line(number, row, offset, end))
        offset = end + (1 if number < len(rows) else 0)
    return CanonicalDocument(text=text, lines=tuple(lines))
