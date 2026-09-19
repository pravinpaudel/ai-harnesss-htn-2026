"""Report layouts the Phase 2 rehearsal found (evals/variants.py): period spellings, heading styles,
estimate wording, reordered table columns, <details> written as headings."""

import random
from datetime import date

import pytest

from app.ingest.canonical import canonicalize_text
from app.ingest.entities import EntityCatalog, build_catalog
from app.ingest.extract import cell_values, labelled_values
from app.ingest.parse import parse_markdown_tables
from app.ingest.service import label_column
from app.markdown import Outline, heading_period, heading_subject
from app.periods import find_period
from app.retrieval.context import SpanContext, entity_from_path
from evals.variants import TRANSFORMS


@pytest.mark.parametrize("text,label,kind", [
    ("Q3 2024 — Revenue", "Q3 2024", "calendar"),
    ("3Q24 — Revenue", "3Q24", "calendar"),
    ("3Q 2024", "3Q 2024", "calendar"),
    ("Q3'24", "Q3'24", "calendar"),
    ("1H26", "1H26", "calendar"),
    ("FQ3 2026 (Jul 31, 2026)", "FQ3 2026 (Jul 31, 2026)", "fiscal"),
    ("Q3 FY26", "Q3 FY26", "fiscal"),
    ("fiscal Q3 2026", "fiscal Q3 2026", "fiscal"),
    ("fiscal 2026 revenue", "fiscal 2026", "fiscal"),
    ("FY26 guidance", "FY26", "fiscal"),
])
def test_period_spellings(text, label, kind):
    got = find_period(text)
    assert got is not None and got[0] == label and got[2] == kind


def test_not_periods():
    for text in ("Q3 24 stores", "Revenue $3,368M", "EPS 2025 target", "Calendar 2027"):
        assert find_period(text) is None, text


def test_stated_dates():
    assert find_period("1Q25 (Mar 31, 2025)")[1] == date(2025, 3, 31)
    assert find_period("Q2 2026 (Jun 30)")[1] == date(2026, 6, 30)
    assert find_period("FQ3 2026 (Jul 31)")[1] is None          # fiscal year is not the calendar year


def test_heading_subject_styles():
    assert heading_subject("ABX — Barrick Mining Corporation") == ("ABX", "Barrick Mining Corporation")
    assert heading_subject("Barrick Mining Corporation (ABX)") == ("ABX", "Barrick Mining Corporation")
    assert heading_subject("Bank of Nova Scotia (Scotiabank) (BNS): Quarterly Performance") == \
        ("BNS", "Bank of Nova Scotia (Scotiabank)")
    assert heading_subject("Executive Summary") is None
    assert entity_from_path(["Results", "Teck Resources Limited (TECK.B)", "3Q24 — Revenue"], {"TECK.B"}) == "TECK.B"


def test_period_headings_are_not_entities_and_give_the_period():
    doc = "\n".join(["# R", "## Results", "### Barrick Mining (ABX)", "#### 3Q24 — Revenue $1M", "text",
                     "#### 4Q24 — Revenue $2M", "### Agnico Eagle (AEM)", "#### 3Q24 — Revenue $3M",
                     "#### 4Q24 — Revenue $4M", "## Profiles", "### Barrick Mining (ABX)", "### Agnico Eagle (AEM)"])
    outline = Outline.parse(doc)
    assert build_catalog(outline, []).labels() == {"ABX", "AEM"}
    assert heading_period(outline.path(5)) == "3Q24"
    assert SpanContext(outline.path(5), None, "ABX").period == "3Q24"


@pytest.mark.parametrize("text", [
    "$0.30 vs $0.31 est", "$0.30 against a $0.31 consensus", "$0.30 versus consensus of $0.31",
    "$0.30 (consensus $0.31)", "$0.30 compared with $0.31 expected",
])
def test_estimate_wordings(text):
    vals = cell_values(text)
    assert [(v.role, v.value) for v in vals] == [("actual", 0.30), ("estimate", 0.31)]


def test_summary_verdict_with_consensus_wording():
    vals = labelled_values("Revenue $152.6M; EPS $0.03 against a $0.06 consensus (Miss)")
    assert ("EPS vs estimate", "Miss") in [(v.label, v.text) for v in vals]


def test_label_column_after_columns_move():
    doc = "\n".join(["| Mkt Cap | Ticker | Revenue |", "|---|---|---|", "| $70B | ABX | $5,292M |",
                     "| $102B | AEM | $3,803M |", "| $32B | TECK.B | C$3,605M |"])
    table = parse_markdown_tables(canonicalize_text(doc.encode()))[0]
    cat = EntityCatalog()
    for t in ("ABX", "AEM", "TECK.B"):
        cat.add(t)
    assert label_column(table, cat) == 1

    kv = "\n".join(["| Value | Field |", "|---|---|", "| ~$70B USD | Market Cap |", "| C$43.69 | Price |",
                    "| Outperform | Analyst consensus |"])
    assert label_column(parse_markdown_tables(canonicalize_text(kv.encode()))[0], EntityCatalog()) == 1


def test_variant_transforms_keep_every_source_line_traceable():
    lines = [(i, t) for i, t in enumerate(open("canadian-mining-research.md", encoding="utf-8").read().split("\n"), 1)]
    for name, fn in TRANSFORMS.items():
        out = fn(list(lines), random.Random(1))
        sources = [s for s, _ in out if s is not None]
        assert len(sources) == len(set(sources)), name              # no source line duplicated
        if name not in ("drop_tables", "flatten"):
            assert set(sources) >= {s for s, t in lines if t.strip()}, name   # no text lost
