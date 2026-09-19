"""Contract check — run in CI and before every checkpoint.

    uv run python -m contracts.check

Verifies, without touching any application code:
  1. fixture raw files match the hashes in fixture/sources.json and still equal the
     original report lines they were cut from;
  2. every fixture and known-corpus expected fact's text sits exactly at its
     line range and character offsets;
  3. fixture/questions.json parses as EvaluationCase objects and every anchor is in range;
  4. fixture/expected_findings.json only references known facts and valid rules;
  5. every example in examples/ parses as AnswerResponse and every citation quote is
     verbatim at its offsets in the cited fixture document with the right hash;
  6. JSON Schemas in json-schema/ are up to date with models.py (rewritten, then diffed by git).
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

from contracts import models
from contracts.models import AnswerResponse, EvaluationCase, FindingRule

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / "contracts"
FIX = C / "fixture"
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def check_fixture_raw() -> dict[str, str]:
    sources = json.loads((FIX / "sources.json").read_text(encoding="utf-8"))
    texts = {}
    for name, meta in sources.items():
        text = (FIX / "raw" / name).read_text(encoding="utf-8")
        texts[name] = text
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != meta["sha256"]:
            err(f"{name}: sha256 differs from sources.json (rebuild the fixture)")
        orig = (ROOT / meta["source_file"]).read_text(encoding="utf-8").split("\n")
        for i, (line, o) in enumerate(zip(text.split("\n"), meta["origin_line"]), start=1):
            if o is None:
                if line != "":
                    err(f"{name}:{i}: joiner line must be blank")
            elif orig[o - 1] != line:
                err(f"{name}:{i}: differs from {meta['source_file']}:{o}")
    return texts


def check_facts(path: Path, texts: dict[str, str]) -> set[str]:
    keys = set()
    for r in csv.DictReader(open(path, encoding="utf-8")):
        keys.add(r["fact_key"])
        text = texts.get(r["document_name"])
        if text is None:
            err(f"{path.name} {r['fact_key']}: unknown document {r['document_name']}")
            continue
        cs, ce = int(r["char_start"]), int(r["char_end"])
        if text[cs:ce] != r["original_value"]:
            err(f"{path.name} {r['fact_key']}: text at offsets is {text[cs:ce]!r}, expected {r['original_value']!r}")
        line_start = text.count("\n", 0, cs) + 1
        if line_start != int(r["line_start"]):
            err(f"{path.name} {r['fact_key']}: offsets are on line {line_start}, row says {r['line_start']}")
        if r["role"] not in {x.value for x in models.Role}:
            err(f"{path.name} {r['fact_key']}: bad role {r['role']}")
        if r["unit"] not in {x.value for x in models.Unit}:
            err(f"{path.name} {r['fact_key']}: bad unit {r['unit']}")
        if r["period_type"] not in {x.value for x in models.PeriodType}:
            err(f"{path.name} {r['fact_key']}: bad period_type {r['period_type']}")
    return keys


def check_questions(texts: dict[str, str]) -> None:
    data = json.loads((FIX / "questions.json").read_text(encoding="utf-8"))
    for raw in data["cases"]:
        case = EvaluationCase.model_validate(raw)
        for a in case.expect.must_cite:
            n = texts[a.document_name].count("\n")
            if not (1 <= a.line_start <= a.line_end <= n):
                err(f"question {case.case_id}: anchor {a} out of range")


def check_findings(fact_keys: set[str]) -> None:
    data = json.loads((FIX / "expected_findings.json").read_text(encoding="utf-8"))
    for f in data["findings"]:
        FindingRule(f["rule"])
        for k in [f["claim_fact"], *f["evidence_facts"]]:
            if k not in fact_keys:
                err(f"finding {f['finding_key']}: unknown fact {k}")


def check_examples(texts: dict[str, str]) -> None:
    hashes = {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in texts.items()}
    for p in sorted((C / "examples").glob("*.json")):
        ans = AnswerResponse.model_validate_json(p.read_text(encoding="utf-8"))
        for c in ans.citations:
            s = c.span
            if hashes.get(s.document_name) != s.document_hash:
                err(f"{p.name} [{c.citation_id}]: document hash mismatch")
            elif texts[s.document_name][s.char_start:s.char_end] != s.exact_text:
                err(f"{p.name} [{c.citation_id}]: quote not verbatim at offsets")


def export_schemas() -> None:
    out = C / "json-schema"
    out.mkdir(exist_ok=True)
    for name in ("AnswerRequest", "AnswerResponse", "IngestRequest", "IngestReport", "DatasetProfile",
                 "FactFilter", "EvidenceHit", "CalculationRequest", "CalculationResult",
                 "ValidationFinding", "EvaluationCase", "EvaluationReport"):
        schema = getattr(models, name).model_json_schema()
        (out / f"{name}.json").write_text(json.dumps(schema, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    texts = check_fixture_raw()
    keys = check_facts(FIX / "expected_facts.csv", texts)
    full = {f: (ROOT / f).read_text(encoding="utf-8") for f in
            ("canadian-financials-research.md", "canadian-mining-research.md", "canadian-technology-research.md")}
    known = check_facts(C / "known-corpus" / "expected_facts.csv", full)
    check_questions(texts)
    check_findings(keys)
    check_examples(texts)
    export_schemas()
    print(f"fixture facts: {len(keys)}  known-corpus facts: {len(known)}  "
          f"examples: {len(list((C / 'examples').glob('*.json')))}  schemas exported")
    if errors:
        print(f"\n{len(errors)} ERROR(S):")
        for e in errors:
            print("  -", e)
        return 1
    print(f"OK (contract {models.CONTRACT_VERSION})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
