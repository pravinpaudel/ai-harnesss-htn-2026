"""Phase 2 rehearsal: structural variants of the known corpus.

Each variant rewrites the three reports with one generic kind of change (or all of them) and keeps
the facts. Every output line remembers which source line it came from, so the RBC suite's citation
anchors and the known-corpus expected facts are remapped to the variant's line numbers.

    uv run python -m evals.variants /tmp/phase2            # writes /tmp/phase2/<variant>/{docs,rbc_sample.json,expected_facts.csv}

The transforms use only Markdown structure and general report vocabulary; nothing is keyed to a
question or a company. See evals/README.md for the rehearsal procedure.
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ("canadian-financials-research.md", "canadian-mining-research.md", "canadian-technology-research.md")

Line = tuple[Optional[int], str]           # (source line number or None for new lines, text)
Transform = Callable[[list[Line], random.Random], list[Line]]


# ---------- helpers ----------

def _segments(lines: list[Line], starts: Callable[[str], bool]) -> tuple[list[Line], list[list[Line]]]:
    """Split into a preamble and chunks that each begin at a line where starts(text) is true."""
    pre: list[Line] = []
    chunks: list[list[Line]] = []
    for ln in lines:
        if starts(ln[1]):
            chunks.append([ln])
        elif chunks:
            chunks[-1].append(ln)
        else:
            pre.append(ln)
    return pre, chunks


def _level(n: int) -> Callable[[str], bool]:
    prefix = "#" * n + " "
    return lambda t: t.startswith(prefix)


def _table_runs(lines: list[Line]) -> list[tuple[int, int]]:
    """(start, end) indices of pipe tables: header, divider, rows."""
    runs, i = [], 0
    while i < len(lines) - 1:
        if lines[i][1].lstrip().startswith("|") and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1][1]):
            j = i + 2
            while j < len(lines) and lines[j][1].lstrip().startswith("|"):
                j += 1
            runs.append((i, j - 1))
            i = j
        else:
            i += 1
    return runs


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _sub(lines: list[Line], pairs: list[tuple[str, str]], only: Callable[[str], bool] = lambda t: True) -> list[Line]:
    out = []
    for src, t in lines:
        if only(t):
            for pat, rep in pairs:
                t = re.sub(pat, rep, t)
        out.append((src, t))
    return out


# ---------- transforms ----------

def reorder(lines: list[Line], rng: random.Random) -> list[Line]:
    """Shuffle top-level sections, the subsections inside each, and put quarter blocks newest-last/first flipped."""
    pre, sections = _segments(lines, _level(2))
    rng.shuffle(sections)
    out = list(pre)
    for sec in sections:
        head, subs = _segments(sec, _level(3))
        rng.shuffle(subs)
        out += head
        for sub in subs:
            out += _reverse_blocks(sub)
    return out


def _reverse_blocks(lines: list[Line]) -> list[Line]:
    """Reverse the order of <details> blocks inside one subsection (keeps the text around them)."""
    ranges, i = [], 0
    while i < len(lines):
        if lines[i][1].strip().startswith("<details"):
            j = i
            while j < len(lines) - 1 and not lines[j][1].strip().startswith("</details>"):
                j += 1
            ranges.append((i, j))
            i = j + 1
        else:
            i += 1
    if len(ranges) < 2:
        return lines
    first, last = ranges[0][0], ranges[-1][1]
    inside = {k for a, b in ranges for k in range(a, b + 1)}
    between = [lines[k] for k in range(first, last + 1) if k not in inside and lines[k][1].strip()]
    body = [ln for a, b in reversed(ranges) for ln in lines[a:b + 1] + [(None, "")]]
    return lines[:first] + body + between + lines[last + 1:]


_SECTION_NAMES = [
    (r"Executive Summary", "Key Takeaways"),
    (r"Macro & Regulatory Backdrop", "Operating Environment"),
    (r"Sector Snapshot — Comparable Company Table", "Peer Comparison"),
    (r"Sector-Specific Metrics Table", "Industry Metrics"),
    (r"Company Deep Dives", "Company Profiles"),
    (r"Company Performance — Trailing 8 Quarters", "Quarterly Results History"),
    (r"News Feed — Significant Events", "Event Log"),
    (r"Catalysts Calendar — Upcoming Events", "Upcoming Catalysts"),
    (r"Insider Activity — Notable Transactions", "Insider Transactions"),
    (r"Screening Output — Quantitative Rankings", "Rankings"),
    (r"Data Sources", "Sources"),
]
_HEADERS = [
    (r"\bTicker\b", "Symbol"), (r"\bCompany\b", "Name"), (r"\bRevenue\b", "Sales"), (r"\bMkt Cap\b", "Market Value"),
    (r"\bLatest Q EPS\b", "EPS (last qtr)"), (r"\bAnalyst Consensus\b", "Street View"), (r"\bMetric\b", "Measure"),
    (r"\bDate\b", "When"), (r"\bEvent\b", "Headline"), (r"\bImpact\b", "Effect"), (r"\bField\b", "Item"),
    (r"\bValue\b", "Detail"),
]
_BLOCK_LABELS = [
    (r"\*\*\d+\.\s*Headline Results\*\*", "**Results overview**"),
    (r"\*\*\d+\.\s*Segment Performance\*\*", "**Business lines**"),
    (r"\*\*\d+\.\s*Margin & Profitability\*\*", "**Profitability**"),
    (r"\*\*\d+\.\s*Balance Sheet & Capital\*\*", "**Capital and balance sheet**"),
    (r"\*\*\d+\.\s*Management Commentary\*\*", "**What management said**"),
    (r"\*\*\d+\.\s*Guidance\*\*", "**Outlook**"),
    (r"\*\*\d+\.\s*Market Reaction\*\*", "**Stock reaction**"),
    (r"\*\*\d+\.\s*Analyst/Notable Quotes\*\*", "**Street commentary**"),
    (r"\*\*Thesis:\*\*", "**Investment view:**"),
    (r"\*\*Catalysts:\*\*", "**Upcoming drivers:**"),
    (r"\*\*Risks:\*\*", "**Key risks:**"),
]


def relabel(lines: list[Line], rng: random.Random) -> list[Line]:
    """Rename sections (dropping their numbers), table headers, block labels, and the 'vs … est' phrasing."""
    out = _sub(lines, [(r"^(##\s+)\d+\.\s+", r"\1")] + [(r"^(##\s+)" + a, r"\g<1>" + b) for a, b in _SECTION_NAMES],
               only=_level(2))
    headers = {i for s, _ in _table_runs(out) for i in (s,)}
    out = [(src, _sub([(src, t)], _HEADERS)[0][1]) if i in headers else (src, t) for i, (src, t) in enumerate(out)]
    out = _sub(out, _BLOCK_LABELS)
    return _sub(out, [(r"(\$[\d.,]+)\s+vs\s+(\$[\d.,]+)\s+est\b", r"\1 against a \2 consensus")])


def heading_style(lines: list[Line], rng: random.Random) -> list[Line]:
    """'### ABX — Barrick Mining Corporation' -> '### Barrick Mining Corporation (ABX)'."""
    def fix(t: str) -> str:
        m = re.match(r"^(###\s+)(\S+)\s+[—–]\s+(.+?)(\s+[—–]\s+.+)?$", t)
        if not m:
            return t
        tail = (m.group(4) or "").replace(" — ", ": ", 1).replace(" – ", ": ", 1)
        return f"{m.group(1)}{m.group(3)} ({m.group(2)}){tail}"
    return [(src, fix(t)) for src, t in lines]


def columns(lines: list[Line], rng: random.Random) -> list[Line]:
    """Permute every table's columns (the identifier column no longer comes first)."""
    out = list(lines)
    for start, end in _table_runs(out):
        n = len(_cells(out[start][1]))
        if n < 2:
            continue
        order = list(range(n))
        while order[0] == 0:
            rng.shuffle(order)
        for i in range(start, end + 1):
            cells = _cells(out[i][1])
            if len(cells) != n:
                continue
            out[i] = (out[i][0], _row([cells[k] for k in order]))
    return out


def periods(lines: list[Line], rng: random.Random) -> list[Line]:
    """'Q3 2024' -> '3Q24', 'Q3 FY2026' -> 'FQ3 2026', 'FY2026' -> 'fiscal 2026'."""
    return _sub(lines, [
        (r"\bQ([1-4])\s+FY(\d{4})\b", r"FQ\1 \2"),
        (r"\bQ([1-4])\s+20(\d{2})\b", r"\1Q\2"),
        (r"\bFY(\d{4})\b", r"fiscal \1"),
    ])


def drop_tables(lines: list[Line], rng: random.Random) -> list[Line]:
    """Remove the cross-company tables (those outside any company subsection)."""
    drop: set[int] = set()
    in_company = False
    runs = _table_runs(lines)
    starts = {s: e for s, e in runs}
    i = 0
    while i < len(lines):
        t = lines[i][1]
        if t.startswith("## "):
            in_company = False
        elif t.startswith("### "):
            in_company = True
        if i in starts:
            if not in_company:
                drop.update(range(i, starts[i] + 1))
            i = starts[i] + 1
            continue
        i += 1
    return [ln for k, ln in enumerate(lines) if k not in drop]


def flatten(lines: list[Line], rng: random.Random) -> list[Line]:
    """<details><summary>X</summary> … </details> -> '#### X' followed by the body."""
    out = []
    for src, t in lines:
        s = t.strip()
        if s.startswith("<details") or s.startswith("</details>"):
            continue
        if s.startswith("<summary>"):
            out.append((src, "#### " + " ".join(re.sub(r"<[^>]+>", " ", s).split())))
            continue
        out.append((src, t))
    return out


TRANSFORMS: dict[str, Transform] = {
    "reorder": reorder, "relabel": relabel, "heading_style": heading_style, "columns": columns,
    "periods": periods, "drop_tables": drop_tables, "flatten": flatten,
}
VARIANTS: dict[str, list[str]] = {name: [name] for name in TRANSFORMS}
VARIANTS["combined"] = ["drop_tables", "reorder", "relabel", "heading_style", "columns", "periods", "flatten"]
RENAMES = {"combined": {"canadian-financials-research.md": "banks-sector-review.md",
                        "canadian-mining-research.md": "miners-sector-review.md",
                        "canadian-technology-research.md": "tech-sector-review.md"}}


# ---------- remapping ----------

def build(variant: str, out_dir: Path, seed: int = 7) -> dict:
    steps = VARIANTS[variant]
    rename = RENAMES.get(variant, {})
    docs = out_dir / variant / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    line_maps: dict[str, dict[int, int]] = {}
    for name in REPORTS:
        rng = random.Random(f"{seed}:{variant}:{name}")
        lines: list[Line] = [(i, t) for i, t in enumerate((ROOT / name).read_text(encoding="utf-8").split("\n"), start=1)]
        for step in steps:
            lines = TRANSFORMS[step](lines, rng)
        (docs / rename.get(name, name)).write_text("\n".join(t for _, t in lines), encoding="utf-8")
        line_maps[name] = {src: new for new, (src, _) in enumerate(lines, start=1) if src is not None}

    suite = json.loads((ROOT / "evals/rbc_sample.json").read_text())
    dropped_anchors = 0
    for case in suite["cases"]:
        exp = case["expect"]
        for key in ("must_cite", "must_cite_any"):
            kept = []
            for a in exp.get(key, []):
                m = line_maps[a["document_name"]]
                new = sorted(m[n] for n in range(a["line_start"], a["line_end"] + 1) if n in m)
                if not new:
                    dropped_anchors += 1
                    continue
                # a moved range may no longer be contiguous; anchor each surviving line block
                for lo, hi in _runs(new):
                    kept.append({**a, "document_name": rename.get(a["document_name"], a["document_name"]),
                                 "line_start": lo, "line_end": hi})
            exp[key] = kept
    suite["corpus"] = f"phase2 variant '{variant}' ({', '.join(steps)})"
    (out_dir / variant / "rbc_sample.json").write_text(json.dumps(suite, indent=2))

    rows = list(csv.DictReader(open(ROOT / "contracts/known-corpus/expected_facts.csv", encoding="utf-8")))
    kept_rows = []
    for r in rows:
        m = line_maps[r["document_name"]]
        if int(r["line_start"]) not in m:
            continue
        for step in steps:                      # text values carry the same rewording as the document
            if step in ("periods", "relabel") and r["value_text"]:
                r["value_text"] = TRANSFORMS[step]([(None, r["value_text"])], random.Random(0))[0][1]
        r = dict(r, document_name=rename.get(r["document_name"], r["document_name"]),
                 line_start=m[int(r["line_start"])], line_end=m[int(r["line_end"])], document_sha256="", char_start="", char_end="")
        kept_rows.append(r)
    with open(out_dir / variant / "expected_facts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(kept_rows)
    return {"variant": variant, "steps": steps, "anchors_dropped": dropped_anchors,
            "expected_facts": f"{len(kept_rows)}/{len(rows)}"}


def _runs(nums: list[int]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [(a, b) for a, b in out]


def main(argv: list[str]) -> None:
    out = Path(argv[0]) if argv else Path("/tmp/phase2")
    names = argv[1:] or list(VARIANTS)
    for v in names:
        print(json.dumps(build(v, out)))


if __name__ == "__main__":
    main(sys.argv[1:])
