"""Build evals/rbc_sample.json from the RBC sample questions and answer key in question-set.md.

    uv run python -m evals.build_rbc_sample

Each case expects the right company (by ticker), answered or partial (a caveat is fine; the name must
be right). A case also requires a citation of the evidence the answer key itself points at: the lines
its "Evidence" column lists, and, when the key pins a single quarter with high confidence, that
company's quarter in §6 (its narrative block or its row in the quarterly table). Any one of those
anchors satisfies the case, so an answer that names the right company from the wrong evidence does
not pass, while an answer citing the key's own lines does — several of these questions describe an
event the report narrates across more than one quarter and in the company's deep dive.
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


def evidence_lines(doc: str, column: str) -> list[int]:
    """Line numbers the answer key's evidence column lists ("206, 1494, 1499 (Antamina $4.3B)").

    Only plain numbers are read: an item like "22 operating assets 59" mixes a count with a line, so
    it is skipped rather than guessed at. Every number must be a non-empty line of the document."""
    body = re.sub(r"\([^)]*\)", " ", column).strip()
    lines = (ROOT / doc).read_text(encoding="utf-8").split("\n")
    out = []
    for item in re.split(r"[;,]", body):
        item = item.strip()
        if item.isdigit() and 1 <= int(item) <= len(lines) and lines[int(item) - 1].strip():
            out.append(int(item))
    return out


def deep_dive_thesis(doc: str, ticker: str) -> tuple[int, int] | None:
    """The company profile's own summary paragraph: the first prose paragraph of its section outside §6.

    For a claim about several quarters this is where the report restates what the sector summary says,
    so it is the one paragraph outside the quarters that counts as the same evidence."""
    lines = (ROOT / doc).read_text(encoding="utf-8").split("\n")
    in_s6, inside = False, False
    for i, line in enumerate(lines, start=1):
        if line.startswith("## "):
            in_s6, inside = line.startswith("## 6."), False
        elif line.startswith("### "):
            if inside:
                return None
            inside = not in_s6 and line[4:].startswith(f"{ticker} —")
        elif inside and len(line) > 120 and not re.match(r"\s*(\||[-+]\s|\*\s)", line):
            end = next((j for j in range(i, len(lines) + 1) if not lines[j - 1].strip()), len(lines) + 1) - 1
            return i, end
    return None


def enclosing_block(doc: str, line: int) -> tuple[int, int]:
    """The narrative unit a line sits in: its <details> block, else its paragraph.

    A quarter's block states the same fact in more than one of its subsections and the answer key
    lists one of them, so a citation anywhere in that block is the same evidence. Outside a block the
    unit is the paragraph, which is also how the store chunks text, so anchors stay tight."""
    from app.markdown import Outline

    text = (ROOT / doc).read_text(encoding="utf-8")
    lines = text.split("\n")
    for start, end, _ in Outline.parse(text).blocks:
        if start <= line <= end:
            return start, end
    start = next((i for i in range(line, 0, -1) if not lines[i - 1].strip()), 0) + 1
    end = next((i for i in range(line, len(lines) + 1) if not lines[i - 1].strip()), len(lines) + 1) - 1
    return start, end


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
        m = re.match(r"^\| (\d+) \| (.+?) \| \*\*([A-Z.]+)\*\* \| (.+?) \| (.+?) \| (High|Medium)", line)
        if not (section and m):
            continue
        n, question, ticker, quarter_col, evidence_col, confidence = m.groups()   # noqa: F841 (confidence: notes)
        doc = DOCS[section]
        anchors = [SpanAnchor(document_name=doc, line_start=a, line_end=b)
                   for a, b in {enclosing_block(doc, line) for line in evidence_lines(doc, evidence_col)}]
        qm = QUARTER.match(quarter_col.strip())
        if not qm:
            # a claim about several quarters (trailing periods, a year, a range): the report states it
            # in the sector summary and again in the company's own deep dive, and the key lists one of them
            thesis = deep_dive_thesis(doc, ticker)
            if thesis:
                anchors.append(SpanAnchor(document_name=doc, line_start=thesis[0], line_end=thesis[1]))
        if qm:      # the key names one quarter (certain or not): that quarter's own block is the evidence
            block = quarter_block(doc, ticker, qm.group(1))
            if block is None:
                raise SystemExit(f"{PREFIX[section]}{n}: no §6 block for {ticker} {qm.group(1)}")
            anchors.append(SpanAnchor(document_name=doc, line_start=block[0], line_end=block[1]))
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
    spans = sum(len(c["expect"].get("must_cite_any", [])) for c in cases)
    print(f"wrote {out.relative_to(ROOT)}: {len(cases)} cases, {anchored} with citation anchors ({spans} spans)")


if __name__ == "__main__":
    main()
