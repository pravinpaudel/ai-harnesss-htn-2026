"""Conservative deterministic numeric normalization; uncertain text stays citable."""
from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.models import Unit

_NUMBER = re.compile(r"^\s*(?:~|≈)?\s*(\(?[+-]?)\s*(?:C\$|US\$|\$)?\s*([\d,]+(?:\.\d+)?)\s*(%|bps|x|[kKmMbB])?\s*\)?\s*$")


@dataclass(frozen=True)
class NormalizedValue:
    value: float
    unit: Unit
    currency: str | None
    scale: float


def normalize_number(raw: str, header: str = "") -> NormalizedValue | None:
    """Return a typed scalar only for unambiguous common financial cell values."""
    match = _NUMBER.match(raw)
    if not match:
        return None
    sign, number, suffix = match.groups()
    value = float(number.replace(",", "")) * (-1 if "-" in sign or "(" in sign else 1)
    prefix = raw.strip()
    currency = "CAD" if prefix.startswith("C$") else "USD" if prefix.startswith("US$") else None
    if currency is None and "$" in prefix:
        upper_header = header.upper()
        currency = "USD" if "USD" in upper_header else "CAD" if "CAD" in upper_header else None
    if suffix == "%":
        return NormalizedValue(value, Unit.pct, currency, 1.0)
    if suffix == "bps":
        return NormalizedValue(value, Unit.bps, currency, 1.0)
    if suffix == "x":
        return NormalizedValue(value, Unit.multiple, currency, 1.0)
    scales = {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "b": 1e9, "B": 1e9}
    scale = scales.get(suffix or "", 1.0)
    unit = Unit.money if currency or "$" in header else Unit.count
    return NormalizedValue(value * scale, unit, currency, scale)
