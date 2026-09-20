"""The RBC suite's citation anchors: built from the answer key's own evidence, and kept tight.

A case passes on any one anchor, so anchors must cover the evidence the key points at without
covering so much of a report that citing anything about the company would pass.
"""

import json
from pathlib import Path

import pytest

from evals.build_rbc_sample import deep_dive_thesis, enclosing_block, evidence_lines

ROOT = Path(__file__).resolve().parents[2]
SUITE = json.loads((ROOT / "evals/rbc_sample.json").read_text(encoding="utf-8"))


def test_evidence_lines_reads_plain_numbers_only():
    doc = "canadian-mining-research.md"
    assert evidence_lines(doc, "206, 1494, 1499 (Antamina $4.3B)") == [206, 1494, 1499]
    assert evidence_lines(doc, "1371, 1435, 1494; 22 operating assets 59") == [1371, 1435, 1494]
    assert evidence_lines(doc, "11 (7 of 8 beats; $632M net debt → $3,158M net cash)") == [11]
    assert evidence_lines(doc, "no lines here") == []


def test_enclosing_block_is_the_quarter_block_or_the_paragraph():
    doc = "canadian-mining-research.md"
    start, end = enclosing_block(doc, 1505)            # inside WPM's Q1 2026 <details> block
    assert start <= 1499 and end >= 1523
    para_start, para_end = enclosing_block(doc, 11)    # an executive-summary bullet
    assert para_start == para_end == 11


def test_deep_dive_thesis_is_one_paragraph_of_the_company_profile():
    start, end = deep_dive_thesis("canadian-mining-research.md", "AEM")
    body = (ROOT / "canadian-mining-research.md").read_text(encoding="utf-8").split("\n")[start - 1:end]
    assert end - start < 3 and "Thesis" in body[0] and "Agnico" in body[0]


def test_every_case_is_anchored():
    assert len(SUITE["cases"]) == 30
    assert all(c["expect"]["must_cite_any"] for c in SUITE["cases"])


@pytest.mark.parametrize("case", SUITE["cases"], ids=[c["case_id"] for c in SUITE["cases"]])
def test_anchors_stay_a_small_part_of_the_report(case):
    lines = {n for a in case["expect"]["must_cite_any"]
             for n in range(a["line_start"], a["line_end"] + 1)}
    doc = case["expect"]["must_cite_any"][0]["document_name"]
    total = len((ROOT / doc).read_text(encoding="utf-8").split("\n"))
    assert len(lines) / total < 0.08, f"{case['case_id']} anchors cover {len(lines)}/{total} lines"
