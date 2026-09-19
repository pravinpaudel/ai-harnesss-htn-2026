"""Build contract/fixture/store.duckdb from hand-specified facts, and check the contract files.

Run from the repo root:
    uv run --with duckdb python contract/build_fixture.py

What it does:
  1. Copies the mining and technology reports into contract/fixture/raw/ (same line numbers as the originals).
  2. Creates store.duckdb from contract/schema.sql.
  3. Inserts documents, sections, entities, aliases, 87 hand-read facts, 12 chunks and 3 conflicts.
  4. Verifies every fact's exact_text appears verbatim on its cited line.
  5. Verifies every golden_facts.csv snippet appears on its cited line (all three reports),
     and that golden rows covered by the fixture agree with the fixture's values.

This script is NOT Lane 1's parser. Everything here is typed by hand so both lanes share a trusted
reference. Lane 1 must regenerate equivalent rows from the raw files; Lane 2 develops against this store.
"""

from __future__ import annotations

import csv
import hashlib
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import duckdb

CONTRACT_VERSION = "1.0"
ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contract"
FIXTURE = CONTRACT / "fixture"
RAW = FIXTURE / "raw"
DB_PATH = FIXTURE / "store.duckdb"

SOURCE_FILES = {
    "financials": "canadian-financials-research.md",
    "mining": "canadian-mining-research.md",
    "technology": "canadian-technology-research.md",
}
FIXTURE_DOCS = ["mining", "technology"]
AS_OF = date(2026, 9, 17)


# ---------------------------------------------------------------------------------------------
# Raw files
# ---------------------------------------------------------------------------------------------

def load_lines(doc_id: str, raw_dir: Path) -> list[str]:
    return (raw_dir / SOURCE_FILES[doc_id]).read_text(encoding="utf-8").split("\n")


def copy_raw() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for doc_id in FIXTURE_DOCS:
        shutil.copyfile(ROOT / SOURCE_FILES[doc_id], RAW / SOURCE_FILES[doc_id])


# ---------------------------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------------------------

ENTITIES = {
    "mining": [
        ("ABX", "Barrick Mining", ["barrick", "barrick gold", "barrick mining corporation", "abx.to"]),
        ("AEM", "Agnico Eagle Mines", ["agnico", "agnico eagle", "agnico eagle mines limited", "aem.to"]),
        ("TECK.B", "Teck Resources", ["teck", "teck.b", "teck resources limited", "teck.b.to"]),
        ("FM", "First Quantum Minerals", ["first quantum", "first quantum minerals ltd", "fm.to"]),
        ("WPM", "Wheaton Precious Metals", ["wheaton", "wheaton precious metals corp", "wpm.to"]),
        ("IVN", "Ivanhoe Mines", ["ivanhoe", "ivanhoe mines ltd", "ivn.to"]),
    ],
    "technology": [
        ("SHOP", "Shopify", ["shopify", "shopify inc.", "shop.to"]),
        ("CSU", "Constellation Software", ["constellation", "constellation software inc.", "csu.to"]),
        ("GIB.A", "CGI Group", ["cgi", "cgi group inc.", "gib", "gib.a.to"]),
        ("OTEX", "OpenText", ["opentext corporation", "open text", "otex.to"]),
        ("DSG", "Descartes Systems", ["descartes", "descartes systems group", "dsg.to"]),
        ("KXS", "Kinaxis", ["kinaxis inc.", "kxs.to"]),
    ],
}


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------

H2 = re.compile(r"^## (\d+)\. (.+?)\s*$")
H3 = re.compile(r"^### (.+?)\s*$")
ANCHOR = re.compile(r"\s*\{#([^}]+)\}\s*$")


def parse_sections(doc_id: str, lines: list[str], tickers: set[str]) -> list[dict]:
    sections: list[dict] = []
    current_h2 = None
    for i, line in enumerate(lines, start=1):
        m2, m3 = H2.match(line), H3.match(line)
        if m2:
            current_h2 = {
                "section_id": f"{doc_id}#{m2.group(1)}", "doc_id": doc_id, "parent_section_id": None,
                "level": 2, "number": m2.group(1), "heading": m2.group(2), "anchor": None,
                "entity_id": None, "line_start": i,
            }
            sections.append(current_h2)
        elif m3 and current_h2:
            raw = m3.group(1)
            am = ANCHOR.search(raw)
            anchor = am.group(1) if am else None
            heading = ANCHOR.sub("", raw)
            first = heading.split(" — ")[0].strip()
            entity_id = first if first in tickers else None
            slug = anchor or (entity_id or re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")).lower()
            sections.append({
                "section_id": f"{current_h2['section_id']}/{slug}", "doc_id": doc_id,
                "parent_section_id": current_h2["section_id"], "level": 3, "number": current_h2["number"],
                "heading": heading, "anchor": anchor, "entity_id": entity_id, "line_start": i,
            })
    # line_end: next section of same-or-higher level minus 1, else EOF
    n = len(lines)
    for idx, s in enumerate(sections):
        end = n
        for later in sections[idx + 1:]:
            if later["level"] <= s["level"]:
                end = later["line_start"] - 1
                break
        s["line_end"] = end
    return sections


def section_for(sections: list[dict], doc_id: str, line: int) -> str:
    best = None
    for s in sections:
        if s["doc_id"] == doc_id and s["line_start"] <= line <= s["line_end"]:
            if best is None or s["level"] > best["level"]:
                best = s
    if best is None:
        raise ValueError(f"no section for {doc_id}:{line}")
    return best["section_id"]


# ---------------------------------------------------------------------------------------------
# Facts — typed by hand from the raw files. exact_text must appear verbatim on `line`.
# ---------------------------------------------------------------------------------------------

FACTS: list[dict] = []


def F(doc, line, entity, concept, key, role, exact, *, num=None, text=None, low=None, high=None,
      unit=None, unit_label=None, cur=None, scale=1.0, est=False, basis=None,
      plabel=None, pend=None, ptype=None, ext="table"):
    FACTS.append(dict(
        doc_id=doc, line=line, entity_id=entity, concept=concept, concept_key=key, basis=basis,
        value_num=num, value_text=text, value_low=low, value_high=high, unit=unit, unit_label=unit_label,
        currency=cur, scale=scale, is_estimate=est, period_label=plabel, period_end=pend,
        period_type=ptype, role=role, exact_text=exact, extractor=ext,
    ))


Q = {  # calendar quarter ends
    "Q3 2024": date(2024, 9, 30), "Q4 2024": date(2024, 12, 31), "Q1 2025": date(2025, 3, 31),
    "Q2 2025": date(2025, 6, 30), "Q3 2025": date(2025, 9, 30), "Q4 2025": date(2025, 12, 31),
    "Q1 2026": date(2026, 3, 31), "Q2 2026": date(2026, 6, 30),
}
M6 = 1e6

# --- mining §3: IVN row (line 44). Header "Mkt Cap (USD)" but the cell says C$ -> CAD (cell wins).
cal = dict(plabel="Q2 2026", pend=Q["Q2 2026"], ptype="calendar")
F("mining", 44, "IVN", "Mkt Cap (USD)", "market_cap", "actual", "| C$17.1B |",
  num=17.1e9, unit="money", cur="CAD", scale=1e9, pend=AS_OF, ptype="point")
F("mining", 44, "IVN", "Latest Q EPS", "eps", "actual", "| $0.03 |", num=0.03, unit="money", **cal)
F("mining", 44, "IVN", "EPS YoY%", "eps_yoy_pct", "actual", "| N/M |", text="N/M", unit="text", **cal)
F("mining", 44, "IVN", "Revenue", "revenue", "actual", "| $153M |", num=153 * M6, unit="money", scale=M6, **cal)
F("mining", 44, "IVN", "Rev YoY%", "revenue_yoy_pct", "actual", "| +58% |", num=58, unit="pct", **cal)
F("mining", 44, "IVN", "Primary Commodity", "primary_commodity", "attribute", "| Copper/Zinc |",
  text="Copper/Zinc", unit="text", pend=AS_OF, ptype="point")
F("mining", 44, "IVN", "Analyst Consensus", "analyst_consensus", "attribute", "Moderate Buy (avg tgt C$15.19)",
  text="Moderate Buy (avg tgt C$15.19)", unit="text", pend=AS_OF, ptype="point")

# --- mining §6: IVN quarterly <summary> lines (no table in the mining report)
IVN_Q = [
    # line, quarter, revenue exact, revenue, eps exact, eps, est exact, est, verdict exact, verdict
    (1567, "Q3 2024", "Revenue $0M (equity method)", 0.0, "EPS $0.09", 0.09, "vs $0.13 est", 0.13, "(Slight Miss)", "Slight Miss"),
    (1599, "Q4 2024", "Revenue $40.8M", 40.8 * M6, "EPS $0.07", 0.07, "vs $0.12 est", 0.12, "(Miss)", "Miss"),
    (1631, "Q1 2025", "Revenue $77.0M", 77.0 * M6, "EPS $0.10", 0.10, "vs $0.07 est", 0.07, "(Beat)", "Beat"),
    (1663, "Q2 2025", "Revenue $96.8M", 96.8 * M6, "EPS $0.03", 0.03, "vs $0.04 est", 0.04, "(Miss)", "Miss"),
    (1695, "Q3 2025", "Revenue $129.4M", 129.4 * M6, "EPS $0.02", 0.02, "vs -$0.02 est", -0.02, "(Beat)", "Beat"),
    (1727, "Q4 2025", "Revenue $138.4M", 138.4 * M6, "EPS $0.04", 0.04, "vs $0.04 est", 0.04, "(Met)", "Met"),
    (1759, "Q1 2026", "Revenue $165.5M", 165.5 * M6, "EPS $0.00", 0.00, "vs $0.06 est", 0.06, "(Miss)", "Miss"),
    (1791, "Q2 2026", "Revenue $152.6M", 152.6 * M6, "EPS $0.03", 0.03, "vs $0.06 est", 0.06, "(Miss)", "Miss"),
]
for line, qtr, rx, rv, ex, ev, sx, sv, vx, vv in IVN_Q:
    p = dict(plabel=qtr, pend=Q[qtr], ptype="calendar", ext="summary_line")
    F("mining", line, "IVN", "Revenue", "revenue", "actual", rx, num=rv, unit="money", scale=M6, **p)
    F("mining", line, "IVN", "EPS", "eps", "actual", ex, num=ev, unit="money", **p)
    F("mining", line, "IVN", "EPS", "eps", "estimate", sx, num=sv, unit="money", **p)
    F("mining", line, "IVN", "Beat/Miss", "beat_miss", "attribute", vx, text=vv, unit="text", **p)

# --- mining §10: rank matrix rows
RANK_REV = [("WPM", 85), ("TECK.B", 78), ("AEM", 35), ("IVN", 58), ("ABX", 44), ("FM", 24)]
for rank, (t, v) in enumerate(RANK_REV, start=1):
    cell = f"{t} (+{v}%)"
    F("mining", 1898, t, "Revenue Growth (YoY, Latest Q)", "revenue_yoy_pct", "rank", cell,
      num=rank, text=cell, unit="rank", ext="rank_matrix", **cal)
    F("mining", 1898, t, "Revenue Growth (YoY, Latest Q)", "revenue_yoy_pct", "actual", cell,
      num=v, unit="pct", ext="rank_matrix", **cal)
RANK_BEAT = [("TECK.B", 8), ("AEM", 7), ("WPM", 6), ("ABX", 5), ("FM", 5), ("IVN", 3)]
t8 = dict(plabel="trailing 8Q", pend=Q["Q2 2026"], ptype="range", ext="rank_matrix")
for rank, (t, v) in enumerate(RANK_BEAT, start=1):
    cell = f"{t} ({v}/8)"
    F("mining", 1899, t, "EPS Beat Rate (8Q)", "eps_beat_count", "rank", cell, num=rank, text=cell, unit="rank", **t8)
    F("mining", 1899, t, "EPS Beat Rate (8Q)", "eps_beat_count", "count", cell, num=v, text=f"{v}/8", unit="count", **t8)

# --- technology §1: SHOP executive-summary claim (no period stated)
F("technology", 9, "SHOP", "Payments penetration", "payments_penetration", "trend",
  "Payments penetration expanding (58% → 68%)", text="↑", low=58, high=68, unit="pct",
  ptype="unspecified", ext="bullet")

# --- technology §3: SHOP row (line 38)
tq = dict(plabel="Q2 2026", pend=Q["Q2 2026"], ptype="calendar")
F("technology", 38, "SHOP", "Mkt Cap", "market_cap", "actual", "~$185B CAD",
  num=185e9, unit="money", cur="CAD", scale=1e9, est=True, pend=AS_OF, ptype="point")
F("technology", 38, "SHOP", "Last Q Rev", "revenue", "actual", "$3.58B (Q2 2026)", num=3.58e9, unit="money", scale=1e9, **tq)
F("technology", 38, "SHOP", "Rev YoY%", "revenue_yoy_pct", "actual", "| +33.7% |", num=33.7, unit="pct", **tq)
F("technology", 38, "SHOP", "EPS (Adj)", "eps", "actual", "| $0.42 |", num=0.42, unit="money", basis="adjusted", **tq)
F("technology", 38, "SHOP", "EPS YoY%", "eps_yoy_pct", "actual", "| +20% |", num=20, unit="pct", **tq)
F("technology", 38, "SHOP", "FY End", "fiscal_year_end", "attribute", "| Dec 31 |", text="Dec 31", unit="text",
  pend=AS_OF, ptype="point")
F("technology", 38, "SHOP", "Analyst Consensus", "analyst_consensus", "attribute", "| Buy |", text="Buy", unit="text",
  pend=AS_OF, ptype="point")

# --- technology §3: GIB.A and OTEX growth (the 'slowest grower' pair)
F("technology", 40, "GIB.A", "Rev YoY%", "revenue_yoy_pct", "actual", "| +2.5% |", num=2.5, unit="pct",
  plabel="Q3 FY2026", pend=date(2026, 6, 30), ptype="fiscal")
F("technology", 41, "OTEX", "Rev YoY%", "revenue_yoy_pct", "actual", "| +2.9% |", num=2.9, unit="pct",
  plabel="Q4 FY2026", pend=date(2026, 6, 30), ptype="fiscal")

# --- technology §6: SHOP trend-read line (241)
tr = dict(plabel="trailing 8Q", pend=Q["Q2 2026"], ptype="range", ext="trend_line")
F("technology", 241, "SHOP", "Rev", "revenue", "trend", "Rev ↑ ($2.16B → $3.58B)",
  text="↑", low=2.16e9, high=3.58e9, unit="money", scale=1e9, **tr)
F("technology", 241, "SHOP", "Payments penetration", "payments_penetration", "trend",
  "Payments penetration ↑ (62% → 68%)", text="↑", low=62, high=68, unit="pct", **tr)
F("technology", 241, "SHOP", "Beat/Met", "eps_beat_or_met_count", "count", "Beat/Met 6/8",
  num=6, text="6/8", unit="count", **tr)

# --- technology §6: SHOP quarterly table rows
for line, qtr, qend, rx, rv, yx, yv, ex, ev, sx, sv, vx, basis in [
    (245, "Q2 2026 (Jun 30)", Q["Q2 2026"], "| $3.58B |", 3.58e9, "| +33.7% |", 33.7,
     "$0.42", 0.42, "vs $0.39", 0.39, "Beat (+7.7%)", None),
    (252, "Q3 2024 (Sep 30)", Q["Q3 2024"], "| $2.16B |", 2.16e9, "| +26% |", 26,
     "$0.27 vs $0.27 (adj)", 0.27, "$0.27 vs $0.27 (adj)", 0.27, "In-line", "adjusted"),
]:
    p = dict(plabel=qtr, pend=qend, ptype="calendar")
    F("technology", line, "SHOP", "Revenue", "revenue", "actual", rx, num=rv, unit="money", scale=1e9, **p)
    F("technology", line, "SHOP", "YoY%", "revenue_yoy_pct", "actual", yx, num=yv, unit="pct", **p)
    F("technology", line, "SHOP", "EPS (Act vs Est)", "eps", "actual", ex, num=ev, unit="money", basis=basis, **p)
    F("technology", line, "SHOP", "EPS (Act vs Est)", "eps", "estimate", sx, num=sv, unit="money", **p)
    F("technology", line, "SHOP", "Beat/Miss", "beat_miss", "attribute", vx, text=vx, unit="text", **p)

# --- technology §5: SHOP thesis restates the §1 number
F("technology", 75, "SHOP", "Payments flywheel", "payments_penetration", "trend",
  "penetration expanding from 58% to 68% over 2 years", text="↑", low=58, high=68, unit="pct",
  plabel="over 2 years", ptype="unspecified", ext="bullet")


def fact_id(f: dict, seen: dict) -> str:
    base = f"{f['doc_id']}:{f['line']}:{f['entity_id'] or '_'}:{f['concept_key']}:{f['role']}"
    if f["basis"]:
        base += f":{f['basis']}"
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}:{seen[base]}"


# ---------------------------------------------------------------------------------------------
# Chunks — verbatim blocks. Start at a bold heading line; stop before the next bold heading,
# '</details>', '---' or markdown heading.
# ---------------------------------------------------------------------------------------------

CHUNK_STARTS = [
    # doc, start line, entity, period_label
    ("mining", 11, None, None),          # §1 top opportunities
    ("mining", 13, None, None),          # §1 top risks
    ("mining", 239, "IVN", None),        # §5 IVN thesis
    ("mining", 241, "IVN", None),        # §5 IVN catalysts
    ("mining", 1793, "IVN", "Q2 2026"),  # §6 IVN Q2 2026 headline results
    ("mining", 1796, "IVN", "Q2 2026"),  # segment performance
    ("mining", 1799, "IVN", "Q2 2026"),  # sector-specific metrics
    ("mining", 1814, "IVN", "Q2 2026"),  # market reaction
    ("technology", 9, None, None),       # §1 top opportunities
    ("technology", 75, "SHOP", None),    # §5 SHOP thesis
    ("technology", 257, "SHOP", "Q2 2026"),  # §6 SHOP Q2 2026 headline results
    ("technology", 263, "SHOP", "Q2 2026"),  # sector-specific metrics
]
STOP = re.compile(r"^(\*\*|</details>|---|#|<details>|- \*\*)")


def chunk_block(lines: list[str], start: int) -> tuple[int, int, str]:
    end = start
    for j in range(start + 1, len(lines) + 1):
        if STOP.match(lines[j - 1]):
            break
        end = j
    while end > start and not lines[end - 1].strip():
        end -= 1
    return start, end, "\n".join(lines[start - 1:end])


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------

def main() -> int:
    copy_raw()
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))
    con.execute(Path(CONTRACT / "schema.sql").read_text())
    errors: list[str] = []

    con.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("contract_version", CONTRACT_VERSION),
        ("built_at", date.today().isoformat()),
        ("source", "contract/fixture/raw (hand-built fixture)"),
        ("builder", "contract/build_fixture.py"),
    ])

    lines_by_doc = {d: load_lines(d, RAW) for d in FIXTURE_DOCS}
    all_sections: list[dict] = []
    for doc_id in FIXTURE_DOCS:
        lines = lines_by_doc[doc_id]
        raw_bytes = (RAW / SOURCE_FILES[doc_id]).read_bytes()
        con.execute("INSERT INTO document VALUES (?, ?, ?, ?, ?, ?, ?)", [
            doc_id, SOURCE_FILES[doc_id], doc_id.capitalize(), lines[0].lstrip("# ").strip(),
            AS_OF, hashlib.sha256(raw_bytes).hexdigest(), len(lines),
        ])
        tickers = {t for t, _, _ in ENTITIES[doc_id]}
        for t, name, aliases in ENTITIES[doc_id]:
            con.execute("INSERT INTO entity VALUES (?, ?, ?, ?, ?)", [t, t, name, doc_id.capitalize(), doc_id])
            for a in sorted({t.lower(), name.lower(), *aliases}):
                con.execute("INSERT INTO entity_alias VALUES (?, ?)", [a, t])
        secs = parse_sections(doc_id, lines, tickers)
        all_sections += secs
        for s in secs:
            con.execute("INSERT INTO section VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                s["section_id"], s["doc_id"], s["parent_section_id"], s["level"], s["number"],
                s["heading"], s["anchor"], s["entity_id"], s["line_start"], s["line_end"],
            ])

    # facts
    seen: dict = {}
    ids_by_key: dict[tuple, str] = {}
    for f in FACTS:
        src = lines_by_doc[f["doc_id"]][f["line"] - 1]
        if f["exact_text"] not in src:
            errors.append(f"fact exact_text not on line: {f['doc_id']}:{f['line']} {f['exact_text']!r}")
        fid = fact_id(f, seen)
        f["fact_id"] = fid
        ids_by_key[(f["doc_id"], f["line"], f["entity_id"], f["concept_key"], f["role"])] = fid
        con.execute("INSERT INTO fact VALUES (" + ", ".join(["?"] * 24) + ")", [
            fid, f["doc_id"], section_for(all_sections, f["doc_id"], f["line"]), f["entity_id"],
            f["concept"], f["concept_key"], f["basis"],
            f["value_num"], f["value_text"], f["value_low"], f["value_high"],
            f["unit"], f["unit_label"], f["currency"], f["scale"], f["is_estimate"],
            f["period_label"], f["period_end"], f["period_type"], f["role"],
            f["line"], f["line"], f["exact_text"], f["extractor"],
        ])

    # chunks
    sec_heading = {s["section_id"]: s["heading"] for s in all_sections}
    sec_parent = {s["section_id"]: s["parent_section_id"] for s in all_sections}
    for doc_id, start, entity, plabel in CHUNK_STARTS:
        lines = lines_by_doc[doc_id]
        s, e, text = chunk_block(lines, start)
        if e == s and not text.startswith(("- **", "**")) or not text.strip():
            errors.append(f"chunk {doc_id}:{start} is empty or does not start at a labelled block")
        elif e == s and len(text) < 80:
            errors.append(f"chunk {doc_id}:{start} is a heading with no body")
        sid = section_for(all_sections, doc_id, s)
        path = [sec_heading[sid]]
        if sec_parent[sid]:
            path.insert(0, sec_heading[sec_parent[sid]])
        if plabel:
            path.append(plabel)
        label = re.match(r"^(?:- )?\*\*([^*]+?):?\*\*", lines[s - 1])
        if label:
            path.append(label.group(1).rstrip(":"))
        con.execute("INSERT INTO chunk VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            f"{doc_id}:{s}-{e}", doc_id, sid, entity, " > ".join(path), plabel, text, s, e,
        ])

    # conflicts
    def fid(doc, line, ent, key, role):
        return ids_by_key[(doc, line, ent, key, role)]

    ivn_verdicts = [fid("mining", q[0], "IVN", "beat_miss", "attribute") for q in IVN_Q]
    rev_rank_ids = [fid("mining", 1898, t, "revenue_yoy_pct", r) for t, _ in RANK_REV for r in ("rank", "actual")]
    conflicts = [
        ("count:mining:IVN:eps_beat_count", "count", "high", "mining", "IVN", "eps_beat_count",
         "Screening table says IVN beat EPS estimates in 3 of 8 quarters; the 8 quarterly summaries show "
         "2 beats, 1 met and 5 misses.",
         fid("mining", 1899, "IVN", "eps_beat_count", "count"), None, ivn_verdicts,
         "2/8 (2 beat, 1 met, 5 miss)", "3/8"),
        ("ordering:mining:_:revenue_yoy_pct", "ordering", "medium", "mining", None, "revenue_yoy_pct",
         "Revenue-growth ranking places AEM (+35%) at #3 ahead of IVN (+58%) and ABX (+44%); sorted by "
         "its own values the order is WPM, TECK.B, IVN, ABX, AEM, FM.",
         fid("mining", 1898, "AEM", "revenue_yoy_pct", "rank"),
         fid("mining", 1898, "IVN", "revenue_yoy_pct", "rank"), rev_rank_ids,
         "WPM, TECK.B, IVN, ABX, AEM, FM", "WPM, TECK.B, AEM, IVN, ABX, FM"),
        ("cross_section:technology:SHOP:payments_penetration", "cross_section", "medium", "technology", "SHOP",
         "payments_penetration",
         "Executive summary (and §5 thesis) say Payments penetration went 58% → 68%; the §6 trend read says "
         "62% → 68%.",
         fid("technology", 9, "SHOP", "payments_penetration", "trend"),
         fid("technology", 241, "SHOP", "payments_penetration", "trend"),
         [fid("technology", 9, "SHOP", "payments_penetration", "trend"),
          fid("technology", 75, "SHOP", "payments_penetration", "trend"),
          fid("technology", 241, "SHOP", "payments_penetration", "trend")],
         "start 62%", "start 58%"),
    ]
    con.executemany("INSERT INTO conflict VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", conflicts)

    # golden facts: snippet on line for all three docs; value agreement where the fixture overlaps
    golden = list(csv.DictReader(open(CONTRACT / "golden_facts.csv", encoding="utf-8")))
    all_lines = {d: load_lines(d, ROOT) for d in SOURCE_FILES}
    fixture_by_key = {(f["doc_id"], f["line"], f["entity_id"], f["concept_key"], f["role"], f["basis"] or ""): f
                      for f in FACTS}
    overlap = 0
    for g in golden:
        src = all_lines[g["doc_id"]][int(g["line"]) - 1]
        if g["snippet"] not in src:
            errors.append(f"golden {g['golden_id']}: snippet not on line {g['doc_id']}:{g['line']}: {g['snippet']!r}")
        k = (g["doc_id"], int(g["line"]), g["entity_id"], g["concept_key"], g["role"], g["basis"])
        if k in fixture_by_key:
            overlap += 1
            f = fixture_by_key[k]
            for col in ("value_num", "value_low", "value_high"):
                gv = float(g[col]) if g[col] else None
                if (gv is None) != (f[col] is None) or (gv is not None and abs(gv - f[col]) > 1e-9):
                    errors.append(f"golden {g['golden_id']} disagrees with fixture on {col}: {gv} vs {f[col]}")
            if g["value_text"] and g["value_text"] != f["value_text"]:
                errors.append(f"golden {g['golden_id']} disagrees with fixture on value_text: "
                              f"{g['value_text']!r} vs {f['value_text']!r}")

    con.execute("CHECKPOINT")
    con.close()
    counts = duckdb.connect(str(DB_PATH), read_only=True)
    summary = {t: counts.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
               for t in ("document", "section", "entity", "entity_alias", "fact", "chunk", "conflict")}
    counts.close()
    print("store.duckdb:", ", ".join(f"{k}={v}" for k, v in summary.items()))
    print(f"golden_facts.csv: {len(golden)} rows checked against raw lines, {overlap} also in fixture")
    if errors:
        print(f"\n{len(errors)} ERROR(S):")
        for e in errors:
            print("  -", e)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
