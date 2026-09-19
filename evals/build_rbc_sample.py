"""Build evals/rbc_sample.json from the RBC sample questions and answer key in question-set.md.

    uv run python -m evals.build_rbc_sample

Each case expects the right company (by ticker), answered or partial (a caveat is fine; the name must be right). When the answer key pins a single quarter with
high confidence, the case also requires a citation of that company's quarter in §6 of the report
(its narrative block or its row in the quarterly table), so an answer that names the right company from the wrong evidence does not pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from contracts.models import (
    AnswerStatus, EvaluationCase, Expectation, ExpectedValue, QuestionCategory, SpanAnchor,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = {"Canadian Financials": "canadian-financials-research.md",
        "Canadian Mining": "canadian-mining-research.md",
        "Canadian Technology": "canadian-technology-research.md"}
PREFIX = {"Canadian Financials": "FIN", "Canadian Mining": "MIN", "Canadian Technology": "TEC"}
QUARTER = re.compile(r"^(Q[1-4] (?:FY)?\d{4})(?: \(.*\))?$")


def quarter_row(doc: str, ticker: str, quarter: str) -> int | None:
    """Line of the ticker's §6 quarterly-table row for the quarter, e.g. '| Q3 FY2026 (Jul 31, 2026) | ...'."""
    lines = (ROOT / doc).read_text(encoding="utf-8").split("\n")
    in_s6, in_ticker = False, False
    for i, line in enumerate(lines, start=1):
        if line.startswith("## "):
            in_s6 = line.startswith("## 6.")
        elif line.startswith("### "):
            in_ticker = in_s6 and line[4:].startswith(f"{ticker} —")
        elif in_ticker and line.startswith("|") and line.strip("| ").split("|")[0].strip().startswith(quarter + " "):
            return i
    return None


def quarter_block(doc: str, ticker: str, quarter: str) -> tuple[int, int] | None:
    """Line range of the <details> block for ticker's quarter in §6 (1-based, inclusive)."""
    lines = (ROOT / doc).read_text(encoding="utf-8").split("\n")
    in_s6, in_ticker = False, False
    for i, line in enumerate(lines, start=1):
        if line.startswith("## "):
            in_s6 = line.startswith("## 6.")
        elif line.startswith("### "):
            in_ticker = in_s6 and line[4:].startswith(f"{ticker} —")
        elif in_ticker and "<summary>" in line and re.sub(r"<[^>]+>", "", line).startswith(quarter):
            start = i - 1 if lines[i - 2].strip() == "<details>" else i
            end = next(j for j in range(i, len(lines) + 1) if lines[j - 1].strip() == "</details>")
            return start, end
    return None


def main() -> None:
    text = (ROOT / "question-set.md").read_text(encoding="utf-8")
    section, cases = None, []
    for line in text.split("\n"):
        if line.startswith("## ") and line[3:] in DOCS:
            section = line[3:]
            continue
        m = re.match(r"^\| (\d+) \| (.+?) \| \*\*([A-Z.]+)\*\* \| (.+?) \| .+? \| (High|Medium)", line)
        if not (section and m):
            continue
        n, question, ticker, quarter_col, confidence = m.groups()
        doc = DOCS[section]
        anchors = []
        qm = QUARTER.match(quarter_col.strip())
        if confidence == "High" and qm:
            block = quarter_block(doc, ticker, qm.group(1))
            if block is None:
                raise SystemExit(f"{PREFIX[section]}{n}: no §6 block for {ticker} {qm.group(1)}")
            anchors = [SpanAnchor(document_name=doc, line_start=block[0], line_end=block[1])]
            row = quarter_row(doc, ticker, qm.group(1))
            if row:
                anchors.append(SpanAnchor(document_name=doc, line_start=row, line_end=row))
        case = EvaluationCase(
            case_id=f"{PREFIX[section]}{int(n):02d}", question=question, category=QuestionCategory.identification,
            expect=Expectation(acceptable_statuses=[AnswerStatus.answered, AnswerStatus.partial],
                               values=[ExpectedValue(label="company", value_text=ticker)], must_cite_any=anchors),
            notes=f"Answer {ticker}, {quarter_col.strip()} (confidence {confidence.lower()}).")
        cases.append(json.loads(case.model_dump_json(exclude_none=True)))
    if len(cases) != 30:
        raise SystemExit(f"expected 30 cases, parsed {len(cases)}")
    out = ROOT / "evals" / "rbc_sample.json"
    out.write_text(json.dumps({"suite": "rbc-sample", "contract_version": "1.2",
                               "corpus": list(DOCS.values()), "cases": cases}, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    anchored = sum(bool(c["expect"].get("must_cite_any")) for c in cases)
    print(f"wrote {out.relative_to(ROOT)}: {len(cases)} cases, {anchored} with a quarter-block citation anchor")


if __name__ == "__main__":
    main()
