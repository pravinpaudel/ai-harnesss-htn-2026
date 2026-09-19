"""Where in a document a span sits: heading path, enclosing <details> summary, and the entity it is about.

Uses only generic Markdown structure (`#` headings and `<details>/<summary>` blocks) of the stored
canonical text, never section numbers or domain vocabulary. The contract's `heading_path` is used
when the evidence store provides it; this fills the gap when it does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.markdown import Outline, block_label, heading_period, heading_subject, period_of  # noqa: F401  (re-exported)

_TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,7}$")


@dataclass
class SpanContext:
    heading_path: list[str]
    block: Optional[str]           # the <summary> of the enclosing <details> block, e.g. "Q3 FY2026 — ..."
    entity: Optional[str]          # entity label the section is about, if a heading names one
    row_period: Optional[str] = None   # for a table row whose first cell names a reporting period

    @property
    def period(self) -> Optional[str]:
        """The reporting period this span belongs to: its <details> block, the row's period cell, or a
        heading that starts with a period (reports that use '#### Q3 2024 — …' instead of <details>)."""
        return period_of(self.block) or self.row_period or heading_period(self.heading_path)

    @property
    def label(self) -> str:
        parts = list(self.heading_path)
        if self.block:
            parts.append(self.block[:80])
        return " > ".join(parts)


def entity_from_path(path: list[str], labels: set[str]) -> Optional[str]:
    """Deepest heading about a known entity: 'ABX — Barrick …', 'ABX: …' or 'Barrick … (ABX)'."""
    for text in reversed(path):
        head = re.split(r"\s+[—–-]\s+|:", text, maxsplit=1)[0].strip()
        if head in labels:
            return head
        subject = heading_subject(text)
        if subject and subject[0] in labels:
            return subject[0]
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


class EntityMatcher:
    """Finds which entities a piece of text is about, by label or alias.

    Short all-caps terms (tickers, acronyms) match case-sensitively as whole words; longer names
    and aliases match case-insensitively. Terms under four characters that are not all-caps are
    ignored (too ambiguous). Nothing here assumes entities are stock tickers.
    """

    def __init__(self, names: dict[str, list[str]]):
        self._patterns: list[tuple[str, re.Pattern]] = []
        for label, aliases in names.items():
            for term in sorted({label, *aliases}, key=len, reverse=True):
                term = term.strip()
                exact = term.isupper() and 2 <= len(term) <= 8
                if not exact and len(term) < 4:
                    continue
                pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", 0 if exact else re.IGNORECASE)
                self._patterns.append((label, pat))

    def find(self, text: str) -> list[str]:
        first: dict[str, int] = {}
        for label, pat in self._patterns:
            m = pat.search(text)
            if m and (label not in first or m.start() < first[label]):
                first[label] = m.start()
        return sorted(first, key=first.get)
