"""Deterministic value extraction from table cells and structured text lines.

Every extracted value records its exact character offsets inside the text it came from, so a
fact's span is the value itself ("$0.39"), not the whole cell. Nothing here knows company names
or industry vocabulary; patterns are about how numbers are written:

  * scalars           "$5,292M", "~C$30B", "+33.7%", "36 bps", "3.0x", "~$185B CAD", "N/M"
  * actual vs est.    "$0.42 vs $0.39", "$0.02 vs -$0.02 est", "$0.48 vs $0.51 (adj); $0.57 vs $0.43 (GAAP)"
  * several values    "C$17.1B (~$12.4B USD)", "~$185B CAD / ~$135B USD"
  * counts            "3/8"
  * trends            "Rev ↑ ($2.16B → $3.58B)", "expanding (58% → 68%)", "from 58% to 68%"
  * labelled lists    "Revenue $152.6M; EPS $0.03 vs $0.06 est (Miss)"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from contracts.models import Unit

_CUR_PREFIX = r"(?:C\$|US\$|A\$|€|£|\$)"
_AMOUNT = rf"(?<![A-Za-z0-9.])(?P<est>~|≈|approximately\s+)?(?P<sign>[-+−]?)\s*(?P<cur>{_CUR_PREFIX})?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<suf>%|bps|x|[KMBT](?![A-Za-z]))?(?:\s+(?P<code>CAD|USD|EUR|GBP|AUD))?"
AMOUNT = re.compile(_AMOUNT)
_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_CUR = {"C$": "CAD", "US$": "USD", "A$": "AUD", "€": "EUR", "£": "GBP"}
_BASIS = {"adj": "adjusted", "adjusted": "adjusted", "gaap": "gaap", "non-gaap": "non_gaap"}


@dataclass
class Value:
    start: int                    # offsets within the source text
    end: int
    original: str
    value: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    text: Optional[str] = None
    unit: Unit = Unit.unknown
    currency: Optional[str] = None
    scale: float = 1.0
    is_estimate: bool = False
    basis: Optional[str] = None
    role: str = "actual"
    label: Optional[str] = None   # metric label, when the text names it ("Revenue", "Payments penetration")
    extra: dict = field(default_factory=dict)


def _amount(m: re.Match, header_currency: Optional[str] = None) -> Value:
    num = float(m.group("num").replace(",", ""))
    if m.group("sign") in ("-", "−"):
        num = -num
    suf = m.group("suf") or ""
    scale = _SCALE.get(suf, 1.0)
    cur = m.group("cur")
    currency = m.group("code") or (_CUR.get(cur) if cur else None) or (header_currency if cur == "$" else None)
    if suf == "%":
        unit = Unit.pct
    elif suf == "bps":
        unit = Unit.bps
    elif suf == "x":
        unit = Unit.multiple
    elif cur:
        unit = Unit.money
    else:
        unit = Unit.count if not suf else Unit.money
    start, end = m.start(), m.end()
    return Value(start, end, m.string[start:end].strip(), value=num * scale, unit=unit, currency=currency,
                 scale=scale, is_estimate=bool(m.group("est")))


def header_currency(header: str) -> Optional[str]:
    h = header.upper()
    return "USD" if "USD" in h else "CAD" if "CAD" in h else None


_VS = re.compile(rf"(?P<a>{_AMOUNT.replace('?P<', '?P<a_')})(?:\s+(?P<abasis>adj|adjusted|GAAP|non-GAAP)\.?)?"
                 r"(?:\s*\([^)]*\))?"
                 rf"\s+vs\.?\s+(?P<b>{_AMOUNT.replace('?P<', '?P<b_')})"
                 rf"(?:\s*[-–]\s*(?P<bhi>{_AMOUNT.replace('?P<', '?P<h_')}))?"
                 r"(?P<est>\s+est\.?)?(?:\s*\((?P<basis>adj|adjusted|gaap|GAAP|non-GAAP)\))?", re.IGNORECASE)
_QUANTITY = re.compile(r"(?P<est>~|≈)?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>(?:[KMG]?t|[KM]?oz|lbs?|Koz|Moz|GEOs?|boe|bbl|MWh|GWh|tpa)\b)(?:\s+[A-Z][a-z]?\b)?")
_COUNT = re.compile(r"(?<![\d/])(?P<n>\d+)\s*/\s*(?P<d>\d+)(?![\d/])")
_TREND = re.compile(rf"(?P<a>{_AMOUNT.replace('?P<', '?P<a_')})\s*(?:→|->|to)\s*(?P<b>{_AMOUNT.replace('?P<', '?P<b_')})")


def _sub(m: re.Match, prefix: str) -> Value:
    """Re-parse one side of a two-sided match (group names prefixed a_/b_)."""
    txt = m.group(prefix)
    inner = AMOUNT.search(txt)
    v = _amount(inner)
    offset = m.start(prefix)
    v.start, v.end = offset + inner.start(), offset + inner.end()
    v.original = m.string[v.start:v.end].strip()
    return v


def cell_values(cell: str, header: str = "") -> list[Value]:
    """All facts a table cell states."""
    cell_stripped = cell.strip()
    if not cell_stripped:
        return []
    hcur = header_currency(header)
    out: list[Value] = []
    # actual vs estimate pairs (possibly several, each with its basis)
    for m in _VS.finditer(cell):
        a, b = _sub(m, "a"), _sub(m, "b")
        basis = _BASIS.get((m.group("basis") or m.group("abasis") or "").lower())
        if m.group("bhi"):                      # estimate range "$2.89-$3.10 est"
            hi = _sub(m, "bhi")
            b = Value(b.start, hi.end, m.string[b.start:hi.end], low=b.value, high=hi.value, unit=b.unit,
                      currency=b.currency, scale=b.scale, is_estimate=b.is_estimate)
        for v, role in ((a, "actual"), (b, "estimate")):
            v.role, v.basis = role, basis
            if v.currency is None and hcur and "$" in v.original:
                v.currency = hcur
            out.append(v)
    if out:
        return out
    if cell_stripped.upper() in ("N/M", "N/A", "NM", "—", "-"):
        s = cell.index(cell_stripped)
        return [Value(s, s + len(cell_stripped), cell_stripped, text=cell_stripped, unit=Unit.text)]
    if re.fullmatch(r"~?[A-Za-z][A-Za-z /-]{0,30}\s*\([^)]*\)", cell_stripped) and not re.match(r"[A-Z]{1,3}\$", cell_stripped):
        # "Beat (+7.7%)", "Moderate Buy (avg tgt C$15.19)": a verdict/label with a note -> text attribute
        s = cell.index(cell_stripped)
        return [Value(s, s + len(cell_stripped), cell_stripped, text=cell_stripped, unit=Unit.text, role="attribute")]
    count = _COUNT.search(cell)
    if count and not AMOUNT.fullmatch(cell_stripped):
        n = int(count.group("n"))
        return [Value(count.start(), count.end(), count.group(0), value=float(n), text=count.group(0),
                      unit=Unit.count, role="count")]
    if header.strip().lower() in ("rank", "#", "position") and re.fullmatch(r"#?\d{1,3}", cell_stripped):
        s = cell.index(cell_stripped)
        return [Value(s, s + len(cell_stripped), cell_stripped, value=float(cell_stripped.lstrip("#")),
                      text=cell_stripped, unit=Unit.rank, role="rank")]
    qty = _QUANTITY.match(cell_stripped)
    if qty:                                     # "135,900t Cu (Q2)", "796 Koz Au"
        s = cell.index(cell_stripped)
        num = float(qty.group("num").replace(",", ""))
        return [Value(s + qty.start("num"), s + qty.end("unit"), cell_stripped[qty.start("num"):qty.end("unit")],
                      value=num, unit=Unit.quantity, is_estimate=bool(qty.group("est")),
                      extra={"unit_label": qty.group("unit")})]
    lead = re.match(r"(?P<est>~|≈)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*\(", cell_stripped)
    if lead:                                    # "~59 (11.8% growth + 47% EBITDA)": the leading figure
        s = cell.index(cell_stripped)
        return [Value(s, s + lead.end("num"), cell_stripped[:lead.end("num")],
                      value=float(lead.group("num").replace(",", "")), unit=Unit.score,
                      is_estimate=bool(lead.group("est")))]
    amounts = [m for m in AMOUNT.finditer(cell) if m.group("num")]
    if len(amounts) > 1 and len(re.findall(r"[A-Za-z]{3,}", cell)) > 4:
        return []     # free-text cell ("GMV +31.6% to $115.6B; FCF margin 18%"): left to the text index
    for m in amounts:
        # skip numbers that are only part of a date or label ("Q2 2026", "Jul 31")
        before = cell[max(0, m.start() - 4):m.start()]
        if re.search(r"(Q[1-4]|FY|H[12])\s*$", before) or (not m.group("cur") and not m.group("suf")
                                                         and len(amounts) > 1):
            continue
        v = _amount(m, hcur)
        if not m.group("cur") and not m.group("suf") and not m.group("code"):
            if len(amounts) == 1 and AMOUNT.fullmatch(cell_stripped):
                out.append(v)          # a bare number cell
            continue
        out.append(v)
    if out:
        return out
    # short categorical text ("Beat (+7.7%)", "Buy", "Copper/Zinc") -> attribute
    if len(cell_stripped) <= 40 and not re.search(r"\d{3,}", cell_stripped):
        s = cell.index(cell_stripped)
        return [Value(s, s + len(cell_stripped), cell_stripped, text=cell_stripped.strip("*"), unit=Unit.text,
                      role="attribute")]
    return []


_SUMMARY_SEG = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z &/.'-]{0,40}?)\s+(?=[-~≈+]?\s*(?:C\$|US\$|\$)?\d)")
_VERDICT = re.compile(r"\((?P<v>[A-Za-z][A-Za-z /-]{1,30})\)\s*$")


def labelled_values(text: str) -> list[Value]:
    """'Revenue $152.6M; EPS $0.03 vs $0.06 est (Miss); KK record 133,120t' -> labelled facts.

    Only segments that start with a label followed directly by a number are read; free prose
    segments are left to the text index."""
    out: list[Value] = []
    pos = 0
    for seg in text.split(";"):
        seg_start = text.index(seg, pos)
        pos = seg_start + len(seg)
        m = _SUMMARY_SEG.match(seg)
        if not m:
            continue
        label = m.group("label").strip()
        rest_off = seg_start + m.end()
        rest = text[rest_off:pos]
        vals = cell_values(rest)
        vals = [v for v in vals if v.role in ("actual", "estimate")][:2] or [v for v in vals if v.value is not None][:1]
        if not vals:
            continue
        for v in vals:
            v.start += rest_off
            v.end += rest_off
            v.label = label
            out.append(v)
        verdict = _VERDICT.search(rest)
        if verdict and any(v.role == "estimate" for v in vals):
            s = rest_off + verdict.start("v")
            out.append(Value(s, s + len(verdict.group("v")), verdict.group("v"), text=verdict.group("v"),
                             unit=Unit.text, role="attribute", label=f"{label} vs estimate"))
    return out


_TREND_LABELLED = re.compile(r"(?P<label>[A-Za-z][A-Za-z &/-]{1,40}?)\s+(?P<dir>↑|↓|→|up|down|expanding|contracting|rising|falling|improving|declining)\s*\((?P<body>[^)]*?(?:→|->)[^)]*)\)")
_FROM_TO = re.compile(r"(?P<label>[A-Za-z][A-Za-z &/-]{1,40}?)\s+(?:(?P<dir>\w+)\s+)?(?:\w+\s+)?from\s+(?P<a>[-~]?\s*(?:C\$|US\$|\$)?\d[\d,.]*\s*(?:%|bps|x|[KMB])?)\s+to\s+(?P<b>[-~]?\s*(?:C\$|US\$|\$)?\d[\d,.]*\s*(?:%|bps|x|[KMB])?)")
_COUNT_LABELLED = re.compile(r"(?P<label>[A-Za-z][A-Za-z/ ]{1,30}?)\s+(?P<n>\d+)\s*/\s*(?P<d>\d+)")


def trend_values(text: str) -> list[Value]:
    """Trend statements ('X ↑ (A → B)', 'X expanding (A → B)', 'X ... from A to B') and 'Label N/M' counts."""
    out: list[Value] = []
    for m in _TREND_LABELLED.finditer(text):
        body_off = m.start("body")
        t = _TREND.search(m.group("body"))
        if not t:
            continue
        a, b = _sub(t, "a"), _sub(t, "b")
        s, e = body_off + t.start(), body_off + t.end()
        out.append(Value(s, e, text[s:e], low=a.value, high=b.value, text=m.group("dir"), unit=a.unit,
                         currency=a.currency, scale=a.scale, role="trend", label=m.group("label").strip()))
    for m in _FROM_TO.finditer(text):
        a, b = AMOUNT.search(m.group("a")), AMOUNT.search(m.group("b"))
        if not (a and b):
            continue
        s = m.start("label")
        s = text.index("from", s)
        e = m.end("b")
        va, vb = _amount(a), _amount(b)
        direction = m.group("dir") if m.group("dir") and m.group("dir").lower().endswith(("ing", "ed")) else "from-to"
        out.append(Value(s, e, text[s:e], low=va.value, high=vb.value, text=direction, unit=va.unit,
                         currency=va.currency, scale=va.scale, role="trend", label=m.group("label").strip()))
    for m in _COUNT_LABELLED.finditer(text):
        if int(m.group("d")) < 2:
            continue
        s, e = m.start("n"), m.end("d")
        out.append(Value(s, e, text[s:e], value=float(m.group("n")), text=text[s:e], unit=Unit.count,
                         role="count", label=m.group("label").strip()))
    return out


# ------------------------------------------------------------------------------------------ periods --

_PERIOD = re.compile(r"\b(?P<label>(?:Q[1-4]|H[12])\s+(?:FY)?\d{4}|FY\s?\d{4})(?:\s*\((?P<date>[^)]*)\))?")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def find_period(text: str) -> Optional[tuple[str, Optional[date], str]]:
    """First period label in a text: (label as written, end date if stated, period type)."""
    m = _PERIOD.search(text)
    if not m:
        return None
    label = m.group(0).strip()
    ptype = "fiscal" if "FY" in m.group("label") else "calendar"
    return label, _stated_date(m.group("date") or "", m.group("label")), ptype


def _stated_date(hint: str, label: str) -> Optional[date]:
    """'Jun 30' + 'Q2 2026' -> 2026-06-30; 'Jul 31, 2026'; 'Jul 31/26'. Only dates the source states."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:\s*[,/]\s*(\d{2,4}))?", hint)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    year_s = m.group(3)
    if year_s:
        year = int(year_s) + (2000 if len(year_s) == 2 else 0)
    else:
        y = re.search(r"(\d{4})", label)
        if not y or "FY" in label:
            return None      # a fiscal label's year is not the calendar year of the date
        year = int(y.group(1))
    try:
        return date(year, month, int(m.group(2)))
    except ValueError:
        return None
