"""Deterministic checks for whether differently written values actually disagree.

"$153M" and "$152.6M" are the same number at different precisions; flagging them as a
contradiction is wrong. A value's precision comes from how it is written in the source
(digits after the decimal point, times its scale), so this needs no domain knowledge.
"""

from __future__ import annotations

import re
from typing import Optional

from contracts.models import Fact

_NUM = re.compile(r"\d[\d,]*(?:\.(\d+))?")


def step(f: Fact) -> Optional[float]:
    """Smallest increment the source's written form can express, in the fact's own units."""
    m = _NUM.search(f.value.original_value)
    if not m or f.value.value is None:
        return None
    decimals = len(m.group(1) or "")
    return (10 ** -decimals) * (f.value.scale or 1.0)


def agree(a: Fact, b: Fact) -> bool:
    """True when a and b are equal once the more precise one is rounded to the coarser precision."""
    if a.value.value is None or b.value.value is None:
        return a.value.value_text == b.value.value_text
    if a.value.unit != b.value.unit:
        return False
    if a.value.currency and b.value.currency and a.value.currency != b.value.currency:
        return False
    sa, sb = step(a), step(b)
    if sa is None or sb is None:
        return a.value.value == b.value.value
    return abs(a.value.value - b.value.value) <= max(sa, sb) / 2 + 1e-9


def _period_key(label: Optional[str]) -> str:
    return (label or "").split(" (")[0].strip().lower()


def group_key(f: Fact) -> tuple:
    return (f.entity_id, f.metric, f.role.value, f.basis.value if f.basis else None, _period_key(f.period.label))


def comparable_groups(facts: list[Fact]) -> list[list[Fact]]:
    """Facts that state the same thing (entity, metric, role, basis, period) — only groups of 2+."""
    groups: dict[tuple, list[Fact]] = {}
    for f in facts:
        if f.period.label and f.value.value is not None:
            groups.setdefault(group_key(f), []).append(f)
    return [g for g in groups.values() if len(g) > 1]


def most_precise(group: list[Fact]) -> Fact:
    return min(group, key=lambda f: step(f) or float("inf"))
