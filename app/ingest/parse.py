"""Generic line-preserving Markdown/CSV table discovery."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from .canonical import CanonicalDocument

_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(frozen=True)
class ParsedTable:
    start_line: int
    end_line: int
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    row_lines: tuple[int, ...]


def _pipe_cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def parse_markdown_tables(document: CanonicalDocument) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    i = 0
    lines = document.lines
    while i + 1 < len(lines):
        if "|" not in lines[i].text or not _DIVIDER.match(lines[i + 1].text):
            i += 1
            continue
        headers, start, rows, row_lines = _pipe_cells(lines[i].text), i + 1, [], []
        j = i + 2
        while j < len(lines) and "|" in lines[j].text and lines[j].text.strip():
            cells = _pipe_cells(lines[j].text)
            if len(cells) == len(headers):
                rows.append(cells)
                row_lines.append(j + 1)
            j += 1
        if rows:
            tables.append(ParsedTable(start, j, headers, tuple(rows), tuple(row_lines)))
        i = max(j, i + 1)
    return tables


def parse_csv_table(document: CanonicalDocument) -> ParsedTable | None:
    rows = list(csv.reader(io.StringIO(document.text)))
    if len(rows) < 2 or not rows[0]:
        return None
    width = len(rows[0])
    good = [(tuple(r), line) for line, r in enumerate(rows[1:], start=2) if len(r) == width]
    return ParsedTable(1, len(rows), tuple(rows[0]), tuple(r for r, _ in good), tuple(n for _, n in good)) if good else None
