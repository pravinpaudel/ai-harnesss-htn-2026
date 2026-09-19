"""Deterministic validation rules run at ingest (rule version 1.0).

Each rule recomputes a derived claim from the base facts and records a validation_finding when
the document contradicts itself. None of them knows which contradictions exist, company names or
industry vocabulary; they reason about roles (count, rank, trend, attribute), units and currencies.
The asserted claim is always the first fact in fact_ids.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import text

RULE_VERSION = "1.0"

# Verdict words a count claim can refer to. "met" and "in-line" say the same thing.
_SYNONYMS = {"met": {"met", "in-line", "inline", "in line"}, "beat": {"beat"}, "miss": {"miss", "missed"}}
_UP = {"↑", "up", "expanding", "rising", "improving", "increased", "higher"}
_DOWN = {"↓", "down", "contracting", "falling", "declining", "decreased", "lower"}


@dataclass
class F:
    fact_id: UUID
    span_id: UUID
    entity_id: Optional[UUID]
    entity: Optional[str]
    metric: str
    label: Optional[str]
    role: str
    value: Optional[float]
    low: Optional[float]
    high: Optional[float]
    text: Optional[str]
    original: str
    unit: str
    currency: Optional[str]
    period: Optional[str]
    doc: UUID
    line: int
    cell_id: Optional[UUID]
    table_id: Optional[UUID]
    col: Optional[int]
    cell_text: Optional[str] = None


def _load(conn, version: UUID) -> list[F]:
    rows = conn.execute(text("""
        SELECT f.fact_id, f.span_id, f.entity_id, e.label AS entity, f.metric, f.metric_label AS label,
               f.role::text AS role, f.value, f.value_low AS low, f.value_high AS high, f.value_text AS text,
               f.original_value AS original, f.unit::text AS unit, f.currency, f.period_label AS period,
               s.document_id AS doc, s.line_start AS line, f.cell_id, tc.table_id, tc.col_idx AS col,
               tc.raw_text AS cell_text
        FROM fact f JOIN source_span s ON s.span_id = f.span_id
        LEFT JOIN entity e ON e.entity_id = f.entity_id
        LEFT JOIN table_cell tc ON tc.cell_id = f.cell_id
        WHERE f.dataset_version_id = :v"""), {"v": version}).all()
    return [F(**r._mapping) for r in rows]


def _record(conn, version: UUID, rule: str, severity: str, explanation: str, claim: F, evidence: list[F],
            expected: str, observed: str) -> None:
    facts = [claim] + [f for f in evidence if f.fact_id != claim.fact_id]
    conn.execute(text("""INSERT INTO validation_finding (dataset_version_id, rule, rule_version, severity, explanation,
                             fact_ids, span_ids, expected, observed)
        VALUES (:v, CAST(:rule AS finding_rule), :rv, CAST(:sev AS finding_severity), :why, :facts, :spans, :exp, :obs)"""),
                 {"v": version, "rule": rule, "rv": RULE_VERSION, "sev": severity, "why": explanation,
                  "facts": [f.fact_id for f in facts], "spans": [f.span_id for f in facts],
                  "exp": expected, "obs": observed})


# ------------------------------------------------------------------------------------ count_claim --

def count_claims(conn, version: UUID, facts: list[F]) -> int:
    """'IVN (3/8)' / 'Beat/Met 6/8' claims recounted from the entity's per-period verdict attributes.
    Only checked when the entity has exactly as many verdicts as the claim's denominator."""
    verdicts: dict[UUID, list[F]] = defaultdict(list)
    for f in facts:
        if f.role == "attribute" and f.period and f.entity_id and f.text and _primary(f.text) in _ALL_VERDICTS:
            verdicts[f.entity_id].append(f)
    n = 0
    for f in facts:
        if f.role != "count" or not f.entity_id or not f.text or "/" not in f.text:
            continue
        m = re.match(r"\s*(\d+)\s*/\s*(\d+)", f.text)
        label_words = set(re.findall(r"[a-z-]+", (f.label or "").lower()))
        counted = set().union(*(_SYNONYMS[w] for w in label_words if w in _SYNONYMS)) if label_words else set()
        if not m or not counted:
            continue
        claimed, total = int(m.group(1)), int(m.group(2))
        vs = _one_per_period(verdicts.get(f.entity_id, []))
        if len(vs) != total:
            continue
        hits = [v for v in vs if _primary(v.text) in counted]
        loose = [v for v in vs if _alternatives(v.text) & counted]    # 'Met/Slight Beat' may count either way
        if not (len(hits) <= claimed <= len(loose)):
            tally = ", ".join(f"{k} {sum(1 for v in vs if _primary(v.text) == k)}" for k in sorted({_primary(v.text) for v in vs}))
            _record(conn, version, "count_claim", "high",
                    f"{f.entity} is stated as {f.text} ({f.label}), but its {total} period records show "
                    f"{len(hits)}: {tally}.", f, vs, f"{len(hits)}/{total}", f.text)
            n += 1
    return n


def _verdict_words(t: str) -> set[str]:
    low = t.lower().strip("~ ")
    words = set(re.findall(r"[a-z]+(?:-[a-z]+)?", low))
    return {w for group in _SYNONYMS.values() for w in group if w in words or w == low.split(" (")[0]}


_ALL_VERDICTS = {w for g in _SYNONYMS.values() for w in g} | {"mixed"}
_QUALIFIERS = {"slight", "narrow", "marginal", "strong", "big", "large", "small"}


def _primary(t: str) -> str:
    """The verdict itself, ignoring notes and qualifiers: 'Slight Miss' -> 'miss',
    'Mixed (EPS beat, Rev miss)' -> 'mixed', 'Beat/In-line' -> 'beat', '~In-line' -> 'in-line'."""
    head = re.split(r"\s*\(", t.strip().lstrip("~≈ "), maxsplit=1)[0].lower()
    head = head.split("/")[0]
    words = [w for w in re.findall(r"[a-z]+(?:-[a-z]+)?", head) if w not in _QUALIFIERS]
    return words[0] if words else ""


def _alternatives(t: str) -> set[str]:
    """Every verdict a combined verdict allows: 'Met/Slight Beat' -> {met, beat}."""
    head = re.split(r"\s*\(", t.strip().lstrip("~≈ "), maxsplit=1)[0]
    return {_primary(part) for part in head.split("/") if part.strip()}


def _first(t: str) -> str:
    return t.strip("~ ").split(" (")[0].lower()


def _one_per_period(vs: list[F]) -> list[F]:
    by_period: dict[str, F] = {}
    for v in sorted(vs, key=lambda x: x.line):
        by_period.setdefault(re.split(r"\s*\(", v.period)[0].strip(), v)
    return list(by_period.values())


# ------------------------------------------------------------------------------------- rank_order --

def rank_orders(conn, version: UUID, facts: list[F]) -> int:
    """Ranking rows ('#1 ... #6' cells like 'AEM (+35%)') whose ranks disagree with their own values."""
    by_line: dict[tuple, list[F]] = defaultdict(list)
    for f in facts:
        by_line[(f.doc, f.line)].append(f)
    n = 0
    for (_, line), group in by_line.items():
        ranks = [f for f in group if f.role == "rank" and f.value is not None and f.entity_id]
        if len(ranks) < 3:
            continue
        values = {f.entity_id: f for f in group if f.role in ("actual", "count") and f.value is not None
                  and f.cell_id in {r.cell_id for r in ranks}}
        pairs = [(r, values[r.entity_id]) for r in sorted(ranks, key=lambda r: r.value) if r.entity_id in values]
        if len(pairs) < 3:
            continue
        if len({_descriptors(r.text or "") for r, _ in pairs}) > 1:
            continue        # 'net cash $3.2B' vs 'net debt $1.9B', 'OCF' vs 'FCF': not one comparable quantity
        vals = [v.value for _, v in pairs]
        descending = all(a >= b for a, b in zip(vals, vals[1:]))
        ascending = all(a <= b for a, b in zip(vals, vals[1:]))
        if descending or ascending:
            continue
        order = sorted(pairs, key=lambda p: -p[1].value)       # rankings are usually high-to-low
        first_bad = next(p for p, q in zip(pairs, order) if p[0].entity_id != q[0].entity_id)
        _record(conn, version, "rank_order", "medium",
                f"The ranking '{first_bad[0].label}' places {first_bad[0].entity} "
                f"({first_bad[1].original}) at #{int(first_bad[0].value)}, which its own values do not support.",
                first_bad[0], [x for p in pairs for x in p],
                ", ".join(p[0].entity for p in order), ", ".join(p[0].entity for p in pairs))
        n += 1
    return n


def _descriptors(cell: str) -> frozenset:
    """Words inside a ranking cell's parentheses, other than estimate markers: '(net cash $3.2B)' -> {net, cash}."""
    inner = " ".join(re.findall(r"\(([^)]*)\)", cell))
    words = {w for w in re.findall(r"[a-z]{2,}", inner.lower())} - {"est", "estimate", "approx", "approximately", "e"}
    return frozenset(words)


# -------------------------------------------------------------------------------- trend_direction --

def trend_directions(conn, version: UUID, facts: list[F]) -> int:
    """'FCFE ↑ ($362M → $345M)': the stated direction contradicts the endpoints."""
    n = 0
    for f in facts:
        if f.role != "trend" or f.low is None or f.high is None or not f.text:
            continue
        word = f.text.lower()
        if (word in _UP and f.high < f.low) or (word in _DOWN and f.high > f.low):
            _record(conn, version, "trend_direction", "medium",
                    f"{f.entity or 'The document'}: {f.label} is marked '{f.text}' but runs {f.original}.",
                    f, [], "direction matching the endpoints", f.text)
            n += 1
    return n


# -------------------------------------------------------------------------- duplicate_claim (trend) --

def trend_duplicates(conn, version: UUID, facts: list[F]) -> int:
    """The same trend stated with different endpoints in different places ('58% → 68%' vs '62% → 68%')."""
    trends = [f for f in facts if f.role == "trend" and f.entity_id and f.low is not None and f.high is not None]
    n, used = 0, set()
    for i, a in enumerate(trends):
        if a.fact_id in used:
            continue
        group = [a] + [b for b in trends[i + 1:] if b.entity_id == a.entity_id and b.unit == a.unit
                       and _same_quantity(a, b)]
        if len({(g.doc, g.line) for g in group}) < 2:
            continue
        ends = {(g.low, g.high) for g in group}
        if len(ends) < 2 or not _overlap(group):
            continue
        used |= {g.fact_id for g in group}
        claim = min(group, key=lambda g: (g.line, g.doc))
        _record(conn, version, "duplicate_claim", "medium",
                f"{a.entity}: {claim.label} is stated with different endpoints: "
                + "; ".join(sorted({g.original for g in group})) + ".",
                claim, group, "one set of endpoints", "; ".join(sorted({g.original for g in group})))
        n += 1
    return n


def _same_quantity(a: F, b: F) -> bool:
    ta = {w for w in re.findall(r"[a-z]{4,}", (a.label or a.metric).lower())}
    tb = {w for w in re.findall(r"[a-z]{4,}", (b.label or b.metric).lower())}
    return bool(ta & tb)


def _overlap(group: list[F]) -> bool:
    """Endpoints disagree but describe the same movement (shared start or end), not two different trends."""
    lows, highs = {g.low for g in group}, {g.high for g in group}
    return len(lows) == 1 or len(highs) == 1


# ------------------------------------------------------------------------------ unit_currency_mix --

def currency_mixes(conn, version: UUID, facts: list[F]) -> int:
    """A table column or a ranking row that mixes currencies ('Mkt Cap (USD)' holding C$ values; a
    market-cap ranking of USD and CAD values). Two currencies inside one cell (a stated conversion,
    'C$17.1B (~$12.4B USD)') are not a mix."""
    money = [f for f in facts if f.unit == "money" and f.table_id is not None and f.value is not None
             and f.role in ("actual", "count") and _sole_value(f)]
    rank_of = {f.cell_id: f for f in facts if f.role == "rank" and f.cell_id}
    groups: dict[tuple, list[F]] = defaultdict(list)
    for f in money:
        groups[("col", f.table_id, f.col)].append(f)
        groups[("row", f.table_id, f.line)].append(f)
    n, seen = 0, set()
    for key, group in groups.items():
        marks = {_marker(f) for f in group}
        if len(marks) < 2 or len({f.cell_id for f in group}) < 2:
            continue
        if len({f.entity_id for f in group}) < 3:
            continue        # not a comparison across entities (a key/value table, one entity's row)
        if len({f.label for f in group}) > 1:
            continue        # different metrics side by side are not compared
        sig = frozenset(f.fact_id for f in group)
        if sig in seen:
            continue
        seen.add(sig)
        majority = max(marks, key=lambda m: sum(_marker(f) == m for f in group))
        odd = [f for f in group if _marker(f) != majority]
        described = ", ".join(sorted(f"{f.entity} {f.original}" for f in odd))
        if key[0] == "row":        # a ranking: the rank facts are the claims being compared
            odd = [rank_of.get(f.cell_id, f) for f in odd]
            group = group + [rank_of[f.cell_id] for f in group if f.cell_id in rank_of]
        claim = odd[0] if key[0] == "row" else odd[-1]
        where = "column" if key[0] == "col" else "ranking"
        _record(conn, version, "unit_currency_mix", "medium",
                f"The {where} '{claim.label}' mixes currencies: "
                + described + f" vs {majority} for the rest.",
                claim, group, "one currency per comparison", ", ".join(sorted(marks)))
        n += 1
    return n


def _sole_value(f: F) -> bool:
    """The value is (nearly) the whole cell, or the value of a ranking cell 'AEM ($102B)': a comparison value,
    not one number inside free text."""
    cell = (f.cell_text or "").strip()
    if not cell:
        return False
    inner = re.search(r"\(([^)]*)\)", cell)
    body = inner.group(1).strip() if inner and re.match(r"^[A-Z][A-Z0-9.]*\s*\(", cell) else cell
    return len((f.original or "").strip()) >= 0.6 * len(body)


def _marker(f: F) -> str:
    if f.currency:
        return f.currency
    m = re.match(r"[~≈\s+-]*([A-Z]{0,3}\$|€|£)", f.original or "")
    return m.group(1) if m else "unstated"


RULES = (count_claims, rank_orders, trend_directions, trend_duplicates, currency_mixes)


def run_all(conn, version: UUID) -> int:
    facts = _load(conn, version)
    return sum(rule(conn, version, facts) for rule in RULES)
