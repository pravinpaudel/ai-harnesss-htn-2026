"""Build the shared fixture corpus and its expectations.

Run from the repo root:
    uv run --python 3.12 --with "pydantic>=2" python -m contracts.fixture.build

Outputs (all committed):
    contracts/fixture/raw/mining-excerpt.md, technology-excerpt.md
        Verbatim line ranges from the supplied reports, joined by blank lines.
    contracts/fixture/sources.json
        For every excerpt line: the original file and line number (null for joiner blanks).
    contracts/fixture/expected_facts.csv
        Hand-typed facts with computed line ranges and character offsets.
    contracts/fixture/expected_findings.json
        Validation findings Developer A's validators must produce on the fixture.
    contracts/fixture/questions.json
        Ten EvaluationCase objects Developer B's engine must pass on the fixture.

Every fact value here is typed by hand. The script only computes positions and
verifies that each value's text appears verbatim where it claims to. It is not
Developer A's parser and must not be imported by application code.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from contracts.models import (
    AnswerStatus,
    DeclineReason,
    EvaluationCase,
    Expectation,
    ExpectedValue,
    FindingRule,
    QuestionCategory,
    SourceSpan,
    SpanAnchor,
)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"

# ---------------------------------------------------------------------------
# 1. Excerpts: (original file, [inclusive original line ranges])
# ---------------------------------------------------------------------------

EXCERPTS = {
    "mining-excerpt.md": ("canadian-mining-research.md", [
        (1, 3),          # title, scope, as-of
        (7, 13),         # executive summary bullets
        (34, 46),        # comparable company table (mixed USD/CAD market caps) + notes
        (64, 64),        # "## 5. Company Deep Dives"
        (229, 253),      # IVN deep dive: key-value table, thesis, catalysts, risks
        (262, 262),      # "## 6. Company Performance"
        (1564, 1564),    # "### IVN"
        (1566, 1567), (1596, 1596),   # Q3 2024 <details><summary> ... </details>
        (1598, 1599), (1628, 1628),   # Q4 2024
        (1630, 1631), (1660, 1660),   # Q1 2025
        (1662, 1663), (1692, 1692),   # Q2 2025
        (1694, 1695), (1724, 1724),   # Q3 2025
        (1726, 1727), (1756, 1756),   # Q4 2025
        (1758, 1759), (1788, 1788),   # Q1 2026
        (1790, 1820),    # Q2 2026, full narrative
        (1893, 1899),    # screening: market cap, revenue growth, EPS beat rate rows
    ]),
    "technology-excerpt.md": ("canadian-technology-research.md", [
        (1, 3),
        (7, 9),          # executive summary incl. SHOP Payments 58% -> 68%
        (33, 45),        # comparable company table + aggregates
        (63, 63),        # "## 5. Company Deep Dives"
        (65, 75),        # SHOP key-value table + thesis (58% -> 68% again)
        (233, 233),      # "## 6. Company Performance"
        (239, 252),      # SHOP heading, trend read (62% -> 68%), 8-quarter table
        (254, 284),      # SHOP Q2 2026 full narrative
    ]),
}


@dataclass
class Doc:
    name: str
    source_file: str
    lines: list[str]
    origin: list[Optional[int]]              # original line number per excerpt line
    text: str = ""
    sha256: str = ""
    offsets: list[int] = field(default_factory=list)   # char offset of each line start

    def excerpt_line(self, orig_line: int) -> int:
        return self.origin.index(orig_line) + 1


def build_excerpts() -> dict[str, Doc]:
    RAW.mkdir(parents=True, exist_ok=True)
    docs: dict[str, Doc] = {}
    for name, (src, ranges) in EXCERPTS.items():
        src_lines = (ROOT / src).read_text(encoding="utf-8").split("\n")
        lines: list[str] = []
        origin: list[Optional[int]] = []
        for i, (a, b) in enumerate(ranges):
            if i > 0 and (a, b) != ranges[i - 1] and a != ranges[i - 1][1] + 1:
                lines.append("")
                origin.append(None)
            for n in range(a, b + 1):
                lines.append(src_lines[n - 1])
                origin.append(n)
        text = "\n".join(lines) + "\n"
        (RAW / name).write_text(text, encoding="utf-8")
        offsets, pos = [], 0
        for line in lines:
            offsets.append(pos)
            pos += len(line) + 1
        docs[name] = Doc(name, src, lines, origin, text,
                         hashlib.sha256(text.encode("utf-8")).hexdigest(), offsets)
    return docs


# ---------------------------------------------------------------------------
# 2. Facts (hand-typed). Positions are given as ORIGINAL line numbers.
# ---------------------------------------------------------------------------

@dataclass
class F:
    key: str
    doc: str                    # excerpt name
    orig_line: int
    entity: Optional[str]
    metric_hint: str            # human hint only; implementations choose their own metric keys
    metric_label: Optional[str]  # label as written in the source, if any
    role: str
    original_value: str         # verbatim value text
    anchor: Optional[str] = None  # verbatim context on the line that contains original_value
    occ: int = 0                # which occurrence of original_value inside anchor
    value: Optional[float] = None
    value_low: Optional[float] = None
    value_high: Optional[float] = None
    value_text: Optional[str] = None
    unit: str = "money"
    currency: Optional[str] = None
    scale: float = 1.0
    is_estimate: bool = False
    basis: Optional[str] = None
    period_label: Optional[str] = None
    period_type: str = "unspecified"
    period_end: Optional[date] = None


FACTS: list[F] = []
M, T = "mining-excerpt.md", "technology-excerpt.md"
ASOF = date(2026, 9, 17)

# --- mining: comparable company table (header "Mkt Cap (USD)"; C$ cells are CAD)
for line, tk, mcap, mv, mcur, rev, rv, rcur, yoy in [
    (39, "ABX", "~$70B", 70e9, "USD", "$5,292M", 5292e6, None, 44),
    (40, "AEM", "~$102B", 102e9, "USD", "$3,803M", 3803e6, None, 35),
    (41, "TECK.B", "~$32B", 32e9, "USD", "C$3,605M", 3605e6, "CAD", 78),
    (42, "FM", "~C$30B", 30e9, "CAD", "$1,522M", 1522e6, None, 24),
    (43, "WPM", "~$67B", 67e9, "USD", "$929M", 929e6, None, 85),
    (44, "IVN", "C$17.1B", 17.1e9, "CAD", "$153M", 153e6, None, 58),
]:
    FACTS += [
        F(f"m:{tk}:market_cap:asof", M, line, tk, "market_cap", "Mkt Cap (USD)", "actual", mcap,
          anchor=f"| {mcap} |", value=mv, currency=mcur, scale=1e9, is_estimate=mcap.startswith("~"),
          period_type="point", period_end=ASOF),
        F(f"m:{tk}:revenue:comp", M, line, tk, "revenue", "Revenue", "actual", rev,
          anchor=f"| {rev} |", value=rv, currency=rcur, scale=1e6,
          period_label="Q2 2026", period_type="calendar"),
        F(f"m:{tk}:revenue_yoy:comp", M, line, tk, "revenue_yoy", "Rev YoY%", "actual", f"+{yoy}%",
          anchor=f"| {rev} | +{yoy}% |", value=yoy, unit="pct",
          period_label="Q2 2026", period_type="calendar"),
    ]
FACTS += [
    F("m:IVN:eps:comp", M, 44, "IVN", "eps", "Latest Q EPS", "actual", "$0.03", anchor="| $0.03 |",
      value=0.03, period_label="Q2 2026", period_type="calendar"),
    F("m:IVN:eps_yoy:comp", M, 44, "IVN", "eps_yoy", "EPS YoY%", "actual", "N/M", anchor="| N/M |",
      value_text="N/M", unit="text", period_label="Q2 2026", period_type="calendar"),
    # IVN deep-dive key-value table: CAD value with a source-backed USD conversion
    F("m:IVN:market_cap:kv:CAD", M, 232, "IVN", "market_cap", "Market Cap", "actual", "C$17.1B",
      value=17.1e9, currency="CAD", scale=1e9, period_type="point", period_end=ASOF),
    F("m:IVN:market_cap:kv:USD", M, 232, "IVN", "market_cap", "Market Cap", "actual", "~$12.4B USD",
      value=12.4e9, currency="USD", scale=1e9, is_estimate=True, period_type="point", period_end=ASOF),
]

# --- mining: IVN quarterly <summary> lines (no table in this report)
IVN_Q = [
    (1567, "Q3 2024", "Revenue $0M (equity method)", 0.0, "$0.09", 0.09, "$0.13", 0.13, "Slight Miss"),
    (1599, "Q4 2024", "$40.8M", 40.8e6, "$0.07", 0.07, "$0.12", 0.12, "Miss"),
    (1631, "Q1 2025", "$77.0M", 77.0e6, "$0.10", 0.10, "$0.07", 0.07, "Beat"),
    (1663, "Q2 2025", "$96.8M", 96.8e6, "$0.03", 0.03, "$0.04", 0.04, "Miss"),
    (1695, "Q3 2025", "$129.4M", 129.4e6, "$0.02", 0.02, "-$0.02", -0.02, "Beat"),
    (1727, "Q4 2025", "$138.4M", 138.4e6, "$0.04", 0.04, "$0.04", 0.04, "Met"),
    (1759, "Q1 2026", "$165.5M", 165.5e6, "$0.00", 0.00, "$0.06", 0.06, "Miss"),
    (1791, "Q2 2026", "$152.6M", 152.6e6, "$0.03", 0.03, "$0.06", 0.06, "Miss"),
]
for line, q, rx, rv, ex, ev, sx, sv, verdict in IVN_Q:
    p = dict(period_label=q, period_type="calendar")
    rev_anchor = rx if rx.startswith("Revenue") else f"Revenue {rx}"
    FACTS += [
        F(f"m:IVN:revenue:{q}", M, line, "IVN", "revenue", "Revenue", "actual",
          "$0M" if rv == 0 else rx, anchor=rev_anchor, value=rv, scale=1e6,
          **p),
        F(f"m:IVN:eps:{q}", M, line, "IVN", "eps", "EPS", "actual", ex, anchor=f"EPS {ex} vs {sx} est",
          value=ev, **p),
        F(f"m:IVN:eps_estimate:{q}", M, line, "IVN", "eps", "EPS", "estimate", sx,
          anchor=f"EPS {ex} vs {sx} est", occ=1 if ex == sx else 0, value=sv, **p),
        F(f"m:IVN:beat_miss:{q}", M, line, "IVN", "beat_miss", None, "attribute", verdict,
          anchor=f"({verdict})", value_text=verdict, unit="text", **p),
    ]

# --- mining: screening matrix rows (rank + embedded value)
for line, label, hint, cells in [
    (1897, "Market Cap", "market_cap", [
        ("AEM", "$102B", 102e9, "USD", "money", False), ("ABX", "$70B", 70e9, "USD", "money", False),
        ("WPM", "$67B", 67e9, "USD", "money", False), ("TECK.B", "$32B", 32e9, "USD", "money", False),
        ("FM", "~C$30B", 30e9, "CAD", "money", True), ("IVN", "C$17B", 17e9, "CAD", "money", False)]),
    (1898, "Revenue Growth (YoY, Latest Q)", "revenue_yoy", [
        ("WPM", "+85%", 85, None, "pct", False), ("TECK.B", "+78%", 78, None, "pct", False),
        ("AEM", "+35%", 35, None, "pct", False), ("IVN", "+58%", 58, None, "pct", False),
        ("ABX", "+44%", 44, None, "pct", False), ("FM", "+24%", 24, None, "pct", False)]),
    (1899, "EPS Beat Rate (8Q)", "eps_beat_count", [
        ("TECK.B", "8/8", 8, None, "count", False), ("AEM", "7/8", 7, None, "count", False),
        ("WPM", "6/8", 6, None, "count", False), ("ABX", "5/8", 5, None, "count", False),
        ("FM", "5/8", 5, None, "count", False), ("IVN", "3/8", 3, None, "count", False)]),
]:
    for rank, (tk, raw, v, cur, unit, est) in enumerate(cells, start=1):
        cell = f"{tk} ({raw})"
        per = dict(period_label="trailing 8Q", period_type="trailing") if unit == "count" else (
            dict(period_type="point", period_end=ASOF) if unit == "money" else
            dict(period_label="Latest Q", period_type="unspecified"))
        FACTS += [
            F(f"m:{tk}:{hint}:rank", M, line, tk, hint, label, "rank", cell, anchor=f"| {cell} |",
              value=rank, value_text=cell, unit="rank", **per),
            F(f"m:{tk}:{hint}:screen", M, line, tk, hint, label, "count" if unit == "count" else "actual",
              raw, anchor=f"| {cell} |", value=v, value_text=raw if unit == "count" else None, unit=unit,
              currency=cur, scale=1e9 if unit == "money" else 1.0, is_estimate=est, **per),
        ]

# --- technology: executive summary and thesis trend claims (period not stated)
FACTS += [
    F("t:SHOP:payments_penetration:summary", T, 9, "SHOP", "payments_penetration", "Payments penetration",
      "trend", "58% → 68%", anchor="Payments penetration expanding (58% → 68%)",
      value_low=58, value_high=68, value_text="expanding", unit="pct"),
    F("t:SHOP:payments_penetration:thesis", T, 75, "SHOP", "payments_penetration", "penetration",
      "trend", "from 58% to 68% over 2 years", value_low=58, value_high=68, value_text="expanding",
      unit="pct", period_label="over 2 years"),
]

# --- technology: comparable company table (all market caps stated in CAD)
for line, tk, mcap, mv, yoy, yv, q in [
    (38, "SHOP", "~$185B CAD", 185e9, "+33.7%", 33.7, "Q2 2026"),
    (39, "CSU", "~$68B CAD", 68e9, "+17%", 17, "Q2 2026"),
    (40, "GIB.A", "~$30B CAD", 30e9, "+2.5%", 2.5, "Q3 FY2026"),
    (41, "OTEX", "~$8B CAD", 8e9, "+2.9%", 2.9, "Q4 FY2026"),
    (42, "DSG", "~$13B CAD", 13e9, "+11.8%", 11.8, "Q2 FY2027"),
    (43, "KXS", "~$4.9B CAD", 4.9e9, "+16.4%", 16.4, "Q2 2026"),
]:
    FACTS += [
        F(f"t:{tk}:market_cap:asof", T, line, tk, "market_cap", "Mkt Cap", "actual", mcap,
          anchor=f"| {mcap} |", value=mv, currency="CAD", scale=1e9, is_estimate=True,
          period_type="point", period_end=ASOF),
        F(f"t:{tk}:revenue_yoy:{q}", T, line, tk, "revenue_yoy", "Rev YoY%", "actual", yoy,
          anchor=f"| {yoy} |", value=yv, unit="pct", period_label=q,
          period_type="fiscal" if "FY" in q else "calendar"),
    ]
FACTS += [
    F("t:SHOP:market_cap:kv:CAD", T, 68, "SHOP", "market_cap", "Market Cap", "actual", "~$185B CAD",
      value=185e9, currency="CAD", scale=1e9, is_estimate=True, period_type="point", period_end=ASOF),
    F("t:SHOP:market_cap:kv:USD", T, 68, "SHOP", "market_cap", "Market Cap", "actual", "~$135B USD",
      value=135e9, currency="USD", scale=1e9, is_estimate=True, period_type="point", period_end=ASOF),
]

# --- technology: SHOP trend-read line
tr = dict(period_label="trailing 8Q", period_type="trailing")
FACTS += [
    F("t:SHOP:revenue:trend", T, 241, "SHOP", "revenue", "Rev", "trend", "$2.16B → $3.58B",
      anchor="Rev ↑ ($2.16B → $3.58B)", value_low=2.16e9, value_high=3.58e9, value_text="↑", scale=1e9, **tr),
    F("t:SHOP:payments_penetration:trend", T, 241, "SHOP", "payments_penetration", "Payments penetration",
      "trend", "62% → 68%", anchor="Payments penetration ↑ (62% → 68%)", value_low=62, value_high=68,
      value_text="↑", unit="pct", **tr),
    F("t:SHOP:eps_beat_or_met_count:trend", T, 241, "SHOP", "eps_beat_or_met_count", "Beat/Met", "count",
      "6/8", anchor="Beat/Met 6/8", value=6, value_text="6/8", unit="count", **tr),
]

# --- technology: SHOP 8-quarter table
SHOP_Q = [
    # line, label, end, revenue, rev value, eps cell, [(orig, value, role, basis, occ, est)], verdict
    (245, "Q2 2026 (Jun 30)", date(2026, 6, 30), "$3.58B", 3.58e9, "$0.42 vs $0.39",
     [("$0.42", 0.42, "actual", None, 0, False), ("$0.39", 0.39, "estimate", None, 0, False)], "Beat (+7.7%)"),
    (246, "Q1 2026 (Mar 31)", date(2026, 3, 31), "$3.17B", 3.17e9, "$0.36 vs $0.32",
     [("$0.36", 0.36, "actual", None, 0, False), ("$0.32", 0.32, "estimate", None, 0, False)], "Beat (+12.5%)"),
    (247, "Q4 2025 (Dec 31)", date(2025, 12, 31), "$3.67B", 3.67e9, "$0.48 vs $0.51 (adj); $0.57 vs $0.43 (GAAP)",
     [("$0.48", 0.48, "actual", "adjusted", 0, False), ("$0.51", 0.51, "estimate", "adjusted", 0, False),
      ("$0.57", 0.57, "actual", "gaap", 0, False), ("$0.43", 0.43, "estimate", "gaap", 0, False)], "Mixed"),
    (248, "Q3 2025 (Sep 30)", date(2025, 9, 30), "$2.84B", 2.84e9, "$0.34 vs $0.34",
     [("$0.34", 0.34, "actual", None, 0, False), ("$0.34", 0.34, "estimate", None, 1, False)], "In-line"),
    (249, "Q2 2025 (Jun 30)", date(2025, 6, 30), "$2.68B", 2.68e9, "~$0.26 vs ~$0.26",
     [("~$0.26", 0.26, "actual", None, 0, True), ("~$0.26", 0.26, "estimate", None, 1, True)], "~In-line"),
    (250, "Q1 2025 (Mar 31)", date(2025, 3, 31), "$2.36B", 2.36e9, "$0.25 vs $0.26",
     [("$0.25", 0.25, "actual", None, 0, False), ("$0.26", 0.26, "estimate", None, 0, False)], "Miss (-3.9%)"),
    (251, "Q4 2024 (Dec 31)", date(2024, 12, 31), "$2.81B", 2.81e9, "$0.44 vs $0.44",
     [("$0.44", 0.44, "actual", None, 0, False), ("$0.44", 0.44, "estimate", None, 1, False)], "In-line"),
    (252, "Q3 2024 (Sep 30)", date(2024, 9, 30), "$2.16B", 2.16e9, "$0.27 vs $0.27 (adj)",
     [("$0.27", 0.27, "actual", "adjusted", 0, False), ("$0.27", 0.27, "estimate", "adjusted", 1, False)], "In-line"),
]
for line, label, end, rx, rv, eps_cell, eps, verdict in SHOP_Q:
    p = dict(period_label=label, period_type="calendar", period_end=end)
    short = label.split(" (")[0]
    FACTS.append(F(f"t:SHOP:revenue:{short}", T, line, "SHOP", "revenue", "Revenue", "actual", rx,
                   anchor=f"| {rx} |", value=rv, scale=1e9, **p))
    seen_roles: dict[tuple, int] = {}
    for orig, v, role, basis, occ, est in eps:
        k = (role, basis)
        seen_roles[k] = seen_roles.get(k, 0) + 1
        sub_anchor = eps_cell
        if basis == "gaap":
            sub_anchor = "$0.57 vs $0.43 (GAAP)"
        elif basis == "adjusted" and "; " in eps_cell:
            sub_anchor = "$0.48 vs $0.51 (adj)"
        FACTS.append(F(f"t:SHOP:eps{'_estimate' if role == 'estimate' else ''}:{short}{':' + basis if basis else ''}",
                       T, line, "SHOP", "eps", "EPS (Act vs Est)", role, orig, anchor=sub_anchor, occ=occ,
                       value=v, basis=basis, is_estimate=est, **p))
    FACTS.append(F(f"t:SHOP:beat_miss:{short}", T, line, "SHOP", "beat_miss", "Beat/Miss", "attribute", verdict,
                   anchor=f"| {verdict} |", value_text=verdict, unit="text", **p))


# ---------------------------------------------------------------------------
# 3. Findings (by fact key)
# ---------------------------------------------------------------------------

FINDINGS = [
    dict(key="count:IVN:eps_beat", rule="count_claim", severity="high",
         explanation="The screening table says IVN beat EPS estimates in 3 of 8 quarters; the eight quarterly "
                     "summaries show 2 beats, 1 met and 5 misses.",
         claim="m:IVN:eps_beat_count:screen",
         evidence=[f"m:IVN:beat_miss:{q[1]}" for q in IVN_Q],
         expected="2 of 8 (2 beat, 1 met, 5 miss)", observed="3/8"),
    dict(key="rank:mining:revenue_yoy", rule="rank_order", severity="medium",
         explanation="The revenue-growth ranking places AEM (+35%) third, ahead of IVN (+58%) and ABX (+44%); "
                     "sorted by its own values the order is WPM, TECK.B, IVN, ABX, AEM, FM.",
         claim="m:AEM:revenue_yoy:rank",
         evidence=[f"m:{t}:revenue_yoy:{r}" for t in ("WPM", "TECK.B", "AEM", "IVN", "ABX", "FM")
                   for r in ("rank", "screen")],
         expected="WPM, TECK.B, IVN, ABX, AEM, FM", observed="WPM, TECK.B, AEM, IVN, ABX, FM"),
    dict(key="dup:SHOP:payments_penetration", rule="duplicate_claim", severity="medium",
         explanation="The executive summary and thesis say Shopify Payments penetration went from 58% to 68%; "
                     "the quarterly trend read says 62% to 68%.",
         claim="t:SHOP:payments_penetration:summary",
         evidence=["t:SHOP:payments_penetration:summary", "t:SHOP:payments_penetration:thesis",
                   "t:SHOP:payments_penetration:trend"],
         expected="start 62% (trend read)", observed="start 58% (summary, thesis)"),
    dict(key="currency:mining:market_cap_column", rule="unit_currency_mix", severity="medium",
         explanation="The comparable-company column is headed 'Mkt Cap (USD)' but FM (~C$30B) and IVN "
                     "(C$17.1B) are stated in Canadian dollars.",
         claim="m:IVN:market_cap:asof",
         evidence=[f"m:{t}:market_cap:asof" for t in ("ABX", "AEM", "TECK.B", "FM", "WPM", "IVN")],
         expected="one currency per column", observed="USD and CAD mixed"),
    dict(key="currency:mining:market_cap_rank", rule="unit_currency_mix", severity="medium",
         explanation="The market-cap ranking orders USD values (AEM, ABX, WPM, TECK.B) and CAD values "
                     "(FM, IVN) as if they were comparable.",
         claim="m:FM:market_cap:rank",
         evidence=[f"m:{t}:market_cap:{r}" for t in ("AEM", "ABX", "WPM", "TECK.B", "FM", "IVN")
                   for r in ("rank", "screen")],
         expected="rank within one currency or convert with a cited rate", observed="USD and CAD ranked together"),
]


# ---------------------------------------------------------------------------
# 4. Questions (anchors given as ORIGINAL line ranges)
# ---------------------------------------------------------------------------

def A(doc: str, a: int, b: Optional[int] = None) -> tuple:
    return (doc, a, b or a)


QUESTIONS = [
    dict(case_id="FX01", category=QuestionCategory.lookup,
         question="What was Ivanhoe Mines' revenue in Q2 2026?",
         expect=dict(acceptable_statuses=[AnswerStatus.answered],
                     values=[ExpectedValue(label="revenue", value=152.6e6, tolerance=0.1e6)],
                     must_cite=[A(M, 1791)])),
    dict(case_id="FX02", category=QuestionCategory.lookup,
         question="Did Shopify beat its EPS estimate in Q2 2026, and by how much?",
         expect=dict(acceptable_statuses=[AnswerStatus.answered],
                     values=[ExpectedValue(label="eps actual", value=0.42),
                             ExpectedValue(label="eps estimate", value=0.39)],
                     must_mention=["beat"], must_cite=[A(T, 245)])),
    dict(case_id="FX03", category=QuestionCategory.comparison,
         question="Which grew revenue faster in its latest quarter, Shopify or Kinaxis?",
         expect=dict(acceptable_statuses=[AnswerStatus.answered],
                     values=[ExpectedValue(label="winner", value_text="SHOP")],
                     must_mention=["33.7", "16.4"], must_cite=[A(T, 38), A(T, 43)])),
    dict(case_id="FX04", category=QuestionCategory.calculation,
         question="By what percentage did Ivanhoe's revenue change from Q1 2026 to Q2 2026?",
         expect=dict(acceptable_statuses=[AnswerStatus.answered],
                     values=[ExpectedValue(label="percent change", value=-7.79, tolerance=0.05)],
                     requires_calculation=True, must_cite=[A(M, 1759), A(M, 1791)])),
    dict(case_id="FX05", category=QuestionCategory.narrative,
         question="Why did Ivanhoe's Q2 2026 revenue miss consensus?",
         expect=dict(acceptable_statuses=[AnswerStatus.answered],
                     must_mention=["payable", "logistic"], must_cite=[A(M, 1794)])),
    dict(case_id="FX06", category=QuestionCategory.conflict,
         question="How many of the last eight quarters did Ivanhoe beat EPS estimates?",
         expect=dict(acceptable_statuses=[AnswerStatus.conflict],
                     finding_rules=[FindingRule.count_claim], must_mention=["3", "2"],
                     must_cite=[A(M, 1899), A(M, 1631), A(M, 1695)])),
    dict(case_id="FX07", category=QuestionCategory.conflict,
         question="What was Shopify's Payments penetration at the start of the period, and where did it end?",
         expect=dict(acceptable_statuses=[AnswerStatus.conflict],
                     finding_rules=[FindingRule.duplicate_claim], must_mention=["58", "62", "68"],
                     must_cite=[A(T, 9), A(T, 241)])),
    dict(case_id="FX08", category=QuestionCategory.currency_ambiguity,
         question="Rank the six mining companies by market capitalization.",
         expect=dict(acceptable_statuses=[AnswerStatus.declined, AnswerStatus.partial, AnswerStatus.conflict],
                     decline_reasons=[DeclineReason.incompatible_currency],
                     finding_rules=[FindingRule.unit_currency_mix], must_mention=["CAD"],
                     must_cite=[A(M, 42), A(M, 44)]),
         notes="Market caps mix USD and CAD. Only IVN has a source-backed USD figure (line 232); FM does not. "
               "An answer that silently ranks the raw numbers fails."),
    dict(case_id="FX09", category=QuestionCategory.decline,
         question="What was Ivanhoe's all-in sustaining cost (AISC) per ounce in Q2 2026?",
         expect=dict(acceptable_statuses=[AnswerStatus.declined],
                     decline_reasons=[DeclineReason.insufficient_evidence]),
         notes="The corpus reports C1 cash costs per pound of copper/zinc for IVN, not AISC per ounce."),
    dict(case_id="FX10", category=QuestionCategory.false_premise,
         question="Why did Shopify miss its EPS estimate in Q2 2026?",
         expect=dict(acceptable_statuses=[AnswerStatus.declined],
                     decline_reasons=[DeclineReason.false_premise], must_cite=[A(T, 245)]),
         notes="Shopify beat ($0.42 vs $0.39); a good answer corrects the premise with a citation."),
]


# ---------------------------------------------------------------------------
# Build + verify
# ---------------------------------------------------------------------------

def locate(doc: Doc, f: F) -> tuple[int, int, int, str]:
    line_no = doc.excerpt_line(f.orig_line)
    line = doc.lines[line_no - 1]
    anchor = f.anchor or f.original_value
    a = line.find(anchor)
    if a < 0 or line.find(anchor, a + 1) >= 0:
        raise ValueError(f"{f.key}: anchor {anchor!r} not found exactly once on {doc.name}:{line_no}")
    idx = -1
    for _ in range(f.occ + 1):
        idx = anchor.find(f.original_value, idx + 1)
        if idx < 0:
            raise ValueError(f"{f.key}: value {f.original_value!r} (occurrence {f.occ}) not inside anchor")
    start = doc.offsets[line_no - 1] + a + idx
    end = start + len(f.original_value)
    assert doc.text[start:end] == f.original_value
    return line_no, start, end, line


def main() -> int:
    docs = build_excerpts()
    errors: list[str] = []

    # sources.json
    (HERE / "sources.json").write_text(json.dumps({
        name: {"source_file": d.source_file, "sha256": d.sha256, "lines": len(d.lines),
               "origin_line": d.origin}
        for name, d in docs.items()}, indent=1) + "\n", encoding="utf-8")

    # facts
    keys = [f.key for f in FACTS]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        errors.append(f"duplicate fact keys: {sorted(dupes)}")
    rows, spans = [], {}
    for f in FACTS:
        d = docs[f.doc]
        try:
            line_no, cs, ce, _ = locate(d, f)
        except ValueError as e:
            errors.append(str(e))
            continue
        span = SourceSpan(document_name=d.name, document_hash=d.sha256, line_start=line_no,
                          line_end=line_no, char_start=cs, char_end=ce, exact_text=f.original_value)
        spans[f.key] = span
        rows.append({
            "fact_key": f.key, "document_name": d.name, "entity_label": f.entity or "",
            "metric_hint": f.metric_hint, "metric_label": f.metric_label or "", "role": f.role,
            "basis": f.basis or "", "value": "" if f.value is None else repr(f.value),
            "value_low": "" if f.value_low is None else repr(f.value_low),
            "value_high": "" if f.value_high is None else repr(f.value_high),
            "value_text": f.value_text or "", "original_value": f.original_value, "unit": f.unit,
            "currency": f.currency or "", "scale": repr(f.scale), "is_estimate": str(f.is_estimate).lower(),
            "period_label": f.period_label or "", "period_type": f.period_type,
            "period_end": f.period_end.isoformat() if f.period_end else "",
            "line_start": line_no, "line_end": line_no, "char_start": cs, "char_end": ce,
            "source_line": d.origin[line_no - 1],
        })
    with open(HERE / "expected_facts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # findings
    out_findings = []
    for fd in FINDINGS:
        missing = [k for k in [fd["claim"], *fd["evidence"]] if k not in spans]
        if missing:
            errors.append(f"finding {fd['key']} references unknown facts {missing}")
            continue
        FindingRule(fd["rule"])
        out_findings.append({
            "finding_key": fd["key"], "rule": fd["rule"], "severity": fd["severity"],
            "explanation": fd["explanation"], "claim_fact": fd["claim"], "evidence_facts": fd["evidence"],
            "expected": fd["expected"], "observed": fd["observed"],
            "spans": sorted({(spans[k].document_name, spans[k].line_start) for k in [fd["claim"], *fd["evidence"]]}),
        })
    (HERE / "expected_findings.json").write_text(json.dumps(
        {"contract_version": "1.0",
         "note": "Every finding here must be produced (matched by rule + claim fact). Extra findings are "
                 "allowed but are reviewed as possible false positives.",
         "findings": out_findings}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    # questions
    cases = []
    for q in QUESTIONS:
        exp = dict(q["expect"])
        exp["must_cite"] = [SpanAnchor(document_name=doc, line_start=docs[doc].excerpt_line(a),
                                       line_end=docs[doc].excerpt_line(b)) for doc, a, b in exp.get("must_cite", [])]
        case = EvaluationCase(case_id=q["case_id"], question=q["question"], category=q["category"],
                              expect=Expectation(**exp), notes=q.get("notes"))
        cases.append(json.loads(case.model_dump_json(exclude_none=True)))
    categories = {c["category"] for c in cases}
    for need in ("lookup", "comparison", "calculation", "narrative", "conflict", "currency_ambiguity", "decline"):
        if need not in categories:
            errors.append(f"questions missing category {need}")
    (HERE / "questions.json").write_text(json.dumps(
        {"contract_version": "1.0", "cases": cases}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"excerpts: " + ", ".join(f"{n} ({len(d.lines)} lines, sha256 {d.sha256[:12]})" for n, d in docs.items()))
    print(f"facts: {len(rows)}  findings: {len(out_findings)}  questions: {len(cases)}")
    if errors:
        print(f"\n{len(errors)} ERROR(S):")
        for e in errors:
            print("  -", e)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
