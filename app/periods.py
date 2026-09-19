"""Reporting-period labels as reports write them. Shared by ingest and the research engine.

Recognised (case as usual in reports; labels are kept as written):
  quarters   Q3 2024, Q3 FY2026, Q3 FY26, Q3'24, Q3-2024, 3Q24, 3Q 2024, 3Q FY26, FQ3 2026, F3Q26,
             fiscal Q3 2026, Q3 fiscal 2026
  halves     H1 2026, 1H26, H2 FY2026
  years      FY2026, FY26, FY 2026, fiscal 2026
A trailing stated date is read too: 'Q2 2026 (Jun 30, 2026)'. Two-digit years need an attached
marker (3Q24, FY26, Q3'24) so that 'Q3 24 stores' is not a period. Fiscal year-ends are never assumed.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

_PART = r"(?:F?Q[1-4]|F?[1-4]Q|H[12]|[12]H)"
_PATTERNS = [
    # fiscal Q3 2026
    re.compile(rf"(?<![A-Za-z0-9])(?P<fy>fiscal\s+)(?P<part>{_PART})\s+(?P<y4>20\d{{2}})\b", re.IGNORECASE),
    # Q3 2024 / Q3 FY2026 / Q3 fiscal 2026 / Q3-2024 / 3Q 2024
    re.compile(rf"(?<![A-Za-z0-9])(?P<part>{_PART})\s*[-/]?\s*(?P<fy>FY\s?|fiscal\s+)?(?P<y4>20\d{{2}})\b"),
    # 3Q24 / Q3'24 / Q3 FY26 / F3Q26 / 1H26
    re.compile(rf"(?<![A-Za-z0-9])(?P<part>{_PART})(?:\s*'|\s*(?P<fy>FY)|)(?P<y2>\d{{2}})(?![\d.,%])"),
    # FY2026 / FY 2026 / fiscal 2026 / FY26
    re.compile(r"(?<![A-Za-z0-9])(?P<fy>FY\s?|fiscal\s+)(?P<y4>20\d{2})\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9])(?P<fy>FY)(?P<y2>\d{2})(?![\d.,%])"),
]
_DATE = re.compile(r"\s*\((?P<date>[^)]*)\)")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _match(text: str) -> Optional[re.Match]:
    best = None
    for pat in _PATTERNS:
        m = pat.search(text)
        if m and (best is None or m.start() < best.start() or (m.start() == best.start() and m.end() > best.end())):
            best = m
    return best


def period_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges of every period label in a text (for not reading '2026' as a number)."""
    out = []
    for pat in _PATTERNS:
        out += [(m.start(), m.end()) for m in pat.finditer(text)]
    return out


def find_period(text: str) -> Optional[tuple[str, Optional[date], str]]:
    """First period label in a text: (label as written, end date if stated, 'fiscal' | 'calendar')."""
    m = _match(text)
    if not m:
        return None
    label = m.group(0).strip()
    g = m.groupdict()
    fiscal = bool(g.get("fy")) or (g.get("part") or "").upper().startswith("F")
    year = int(g["y4"]) if g.get("y4") else 2000 + int(g["y2"])
    tail = _DATE.match(text, m.end())
    stated = None
    if tail:
        label = text[m.start():tail.end()].strip()
        stated = stated_date(tail.group("date"), year, fiscal)
    return label, stated, "fiscal" if fiscal else "calendar"


def leading_period(text: str) -> Optional[tuple[str, Optional[date], str]]:
    """A period only when the text starts with it ('3Q24 — Revenue …', '**Q3 FY2026**')."""
    stripped = text.strip().lstrip("*#_ ").strip()
    m = _match(stripped)
    return find_period(stripped) if m and m.start() == 0 else None


def stated_date(hint: str, year: int, fiscal: bool) -> Optional[date]:
    """'Jun 30' (+ calendar year) -> 2026-06-30; 'Jul 31, 2026'; 'Jul 31/26'. Only dates the source states."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:\s*[,/]\s*(\d{2,4}))?", hint)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    if m.group(3):
        y = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
    elif fiscal:
        return None       # a fiscal label's year is not the calendar year of the date
    else:
        y = year
    try:
        return date(y, month, int(m.group(2)))
    except ValueError:
        return None
