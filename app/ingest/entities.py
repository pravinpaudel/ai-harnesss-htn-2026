"""Entity discovery from document structure only.

An entity is something the document is *about*:
  * a heading subject — the leading part of a heading ("IVN — Ivanhoe Mines Ltd") that heads
    more than one section, or
  * a value in a table column whose header is a generic identifier word (Ticker, Symbol,
    Company, Name, Entity, Issuer).
Quarter rows, field labels and rank numbers are never entities. Aliases come from heading names
("Ivanhoe Mines Ltd", "Bank of Nova Scotia", "Scotiabank") and Company/Name columns, plus a
variant without a trailing corporate suffix. No company names or industry terms are built in.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from app.markdown import Outline, heading_subject
from app.periods import leading_period

from .parse import ParsedTable

_LABEL_HEADERS = {"ticker", "symbol", "code", "id", "entity"}
_NAME_HEADERS = {"company", "name", "issuer", "firm", "entity name", "company name"}
_SUFFIX = re.compile(r"[,\s]+(inc\.?|corp\.?|corporation|ltd\.?|limited|plc|co\.?|group|holdings|s\.a\.|n\.v\.)$",
                     re.IGNORECASE)
_PERIODISH = re.compile(r"^(q[1-4]|h[12]|fy|\d{4}\b|\d+$)", re.IGNORECASE)
_GROUP = re.compile(r"^(all|every|each|total|other|others|various|sector|none)\b|^(n/a|-)$", re.IGNORECASE)


@dataclass
class EntityCatalog:
    aliases: dict[str, set[str]] = field(default_factory=dict)       # label -> lowercased aliases

    def add(self, label: str, *names: str, from_table: bool = False) -> None:
        """Heading subjects are trusted; table values are also screened for group placeholders ('All 6')."""
        label = label.strip()
        if (not label or _PERIODISH.match(label) or leading_period(label) or len(label) > 60
                or (from_table and _GROUP.match(label))):
            return
        bucket = self.aliases.setdefault(label, set())
        for n in (label, *names):
            for variant in _variants(n):
                bucket.add(variant)

    def labels(self) -> set[str]:
        return set(self.aliases)

    def owner_of(self, text: str) -> Optional[str]:
        """The entity a table cell names: exactly, or as its leading token ('AEM ($102B)')."""
        cell = text.strip().strip("*").strip()
        if cell in self.aliases:
            return cell
        head = re.split(r"\s*[(\[]", cell, maxsplit=1)[0].strip()
        if head in self.aliases:
            return head
        low = cell.lower()
        for label, names in self.aliases.items():
            if low in names:
                return label
        return None


def _variants(name: str) -> set[str]:
    name = " ".join(name.strip().strip("*").split())
    if not name:
        return set()
    out = {name.lower()}
    for inner in re.findall(r"\(([^)]{2,40})\)", name):          # "Bank of Nova Scotia (Scotiabank)"
        out.add(inner.strip().lower())
    base = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if base:
        out.add(base.lower())
        stripped = _SUFFIX.sub("", base).strip()
        if stripped and stripped != base:
            out.add(stripped.lower())
    return out


def heading_subjects(outline: Outline) -> dict[str, list[str]]:
    """Subjects of headings that head more than one section, with their names:
    'ABX — Barrick Mining Corporation' and 'Barrick Mining Corporation (ABX)' both give ABX."""
    found = [s for s in (heading_subject(text) for _, level, text in outline.headings if level >= 2) if s]
    counts = Counter(label for label, _ in found)
    subjects: dict[str, list[str]] = {}
    for label, name in found:
        if counts[label] >= 2 and len(label) <= 40 and not leading_period(label):
            subjects.setdefault(label, []).append(name)
    return subjects


def table_subjects(table: ParsedTable) -> list[tuple[str, Optional[str]]]:
    """(label, name) pairs from identifier columns, e.g. Ticker + Company."""
    headers = [h.strip().strip("*").lower() for h in table.headers]
    label_col = next((i for i, h in enumerate(headers) if h in _LABEL_HEADERS), None)
    name_col = next((i for i, h in enumerate(headers) if h in _NAME_HEADERS), None)
    if label_col is None and name_col is None:
        return []
    out = []
    for row in table.rows:
        label = row[label_col] if label_col is not None else row[name_col]
        name = row[name_col] if name_col is not None and name_col != label_col else None
        if label and label.strip():
            out.append((label.strip(), name.strip() if name else None))
    return out


def build_catalog(outline: Outline, tables: list[ParsedTable]) -> EntityCatalog:
    cat = EntityCatalog()
    for label, names in heading_subjects(outline).items():
        cat.add(label, *names)
    for table in tables:
        for label, name in table_subjects(table):
            cat.add(label, *([name] if name else []), from_table=True)
    return cat
