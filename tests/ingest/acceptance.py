"""Ingest the contract fixture and compare facts with contracts/fixture/expected_facts.csv.

    uv run python -m tests.ingest.acceptance            # prints recall and misses by category

Matching follows contracts/README.md: same document, line, entity, role and value (float tolerance;
ranges compare value_low/value_high; text facts compare value_text).
"""

from __future__ import annotations

import csv
import sys
import tempfile
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text

from app.ingest.embeddings import NoopEmbeddingProvider
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from contracts.models import IngestRequest, SourceType
from tests.engine.conftest import db_url
from tests.engine.seed_fixture import reset_schema

ROOT = Path(__file__).resolve().parents[2]


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(1e-6, abs(b) * 1e-6)


def _f(v: str):
    return float(v) if v != "" else None


def run(url: str | None = None, corpus: str = "fixture") -> tuple[list[dict], list[dict], list[tuple]]:
    """corpus='fixture' checks contracts/fixture; corpus='known' checks the three full reports."""
    url = url or db_url()
    reset_schema(url)
    engine = create_engine(url)
    svc = FileIngestService(engine, ImmutableRawStorage(Path(tempfile.mkdtemp())), "acceptance",
                            embedder=NoopEmbeddingProvider())
    if corpus == "known":
        src = Path(tempfile.mkdtemp())
        for name in ("canadian-financials-research.md", "canadian-mining-research.md", "canadian-technology-research.md"):
            (src / name).write_bytes((ROOT / name).read_bytes())
        expected_path = ROOT / "contracts/known-corpus/expected_facts.csv"
    else:
        src, expected_path = ROOT / "contracts/fixture/raw", ROOT / "contracts/fixture/expected_facts.csv"
    rep = svc.run_sync(IngestRequest(source=SourceType.file, path=str(src), dataset_name="acceptance"))
    with engine.connect() as c:
        got = c.execute(text("""SELECT d.name, s.line_start, e.label, f.role::text AS role, f.value, f.value_low,
                   f.value_high, f.value_text, f.original_value, f.period_label, f.basis::text AS basis, f.currency
            FROM fact f JOIN source_span s ON s.span_id = f.span_id JOIN document d ON d.document_id = s.document_id
            LEFT JOIN entity e ON e.entity_id = f.entity_id WHERE f.dataset_version_id = :v"""),
                        {"v": str(rep.dataset_version_id)}).all()
    expected = list(csv.DictReader(open(expected_path, encoding="utf-8")))
    found, missed = [], []
    for e in expected:
        ok = any(g.name == e["document_name"] and g.line_start == int(e["line_start"])
                 and (g.label or "") == e["entity_label"] and g.role == e["role"]
                 and _close(g.value, _f(e["value"])) and _close(g.value_low, _f(e["value_low"]))
                 and _close(g.value_high, _f(e["value_high"]))
                 and (not e["value_text"] or e["value"] != "" or (g.value_text or "") == e["value_text"])
                 for g in got)
        (found if ok else missed).append(e)
    return found, missed, got


def main() -> int:
    found, missed, got = run(corpus="known" if "--known" in sys.argv else "fixture")
    print(f"expected facts found: {len(found)}/{len(found) + len(missed)}   (store has {len(got)} facts)")
    cats = Counter((m["document_name"].split("-")[1] if m["document_name"].startswith("canadian") else m["document_name"].split("-")[0],
                    m["role"], m["metric_hint"]) for m in missed)
    for k, v in cats.most_common():
        print(f"  missing {v:>3}  {k}")
    if "-v" in sys.argv:
        for m in missed:
            print("   ", m["fact_key"], m["document_name"][:14], m["line_start"], m["entity_label"], m["role"],
                  repr(m["original_value"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def findings(url: str | None = None, corpus: str = "fixture") -> tuple[list[dict], list[dict], list[str]]:
    """Expected findings (contracts/fixture/expected_findings.json) matched by rule + claim fact.
    Returns (found, missed, extra explanations). Run after run(corpus=...) has ingested."""
    import json
    url = url or db_url()
    engine = create_engine(url)
    with engine.connect() as c:
        v = c.execute(text("SELECT dataset_version_id FROM dataset_version ORDER BY created_at DESC LIMIT 1")).scalar()
        produced = c.execute(text("""SELECT vf.rule::text AS rule, vf.explanation, array_agg(
                   d.name || ':' || s.line_start || ':' || coalesce(e.label, '') || ':' || f.role::text) AS facts
            FROM validation_finding vf CROSS JOIN LATERAL unnest(vf.fact_ids) AS fid
            JOIN fact f ON f.fact_id = fid JOIN source_span s ON s.span_id = f.span_id
            JOIN document d ON d.document_id = s.document_id LEFT JOIN entity e ON e.entity_id = f.entity_id
            WHERE vf.dataset_version_id = :v GROUP BY vf.finding_id, vf.rule, vf.explanation"""), {"v": v}).all()
    if corpus != "fixture":
        return [], [], [p.explanation for p in produced]
    facts = {r["fact_key"]: r for r in csv.DictReader(open(ROOT / "contracts/fixture/expected_facts.csv", encoding="utf-8"))}
    expected = json.loads((ROOT / "contracts/fixture/expected_findings.json").read_text())["findings"]
    found, missed, matched = [], [], set()
    for e in expected:
        cf = facts[e["claim_fact"]]
        key = f'{cf["document_name"]}:{cf["line_start"]}:{cf["entity_label"]}:{cf["role"]}'
        hit = next((i for i, p in enumerate(produced) if p.rule == e["rule"] and key in p.facts), None)
        (found if hit is not None else missed).append(e)
        if hit is not None:
            matched.add(hit)
    extra = [p.explanation for i, p in enumerate(produced) if i not in matched]
    return found, missed, extra
