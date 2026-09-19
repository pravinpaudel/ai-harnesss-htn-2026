"""Phase 2 rehearsal driver: ingest every variant, then run the unchanged CLI eval on each.

    uv run python -m evals.variants /tmp/phase2
    uv run python -m evals.rehearsal /tmp/phase2 --db postgresql+psycopg://harness:harness@localhost:55498/harness

Ingest and eval go through the `htn` CLI exactly as on judging day; this script only points it at
the variant folders and a database, then scores structure (entities, periods, heading paths,
expected-fact recall) and the RBC suite for each variant. Nothing is changed between ingest and eval.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import create_engine, text

from tests.ingest.acceptance import _close, _f


def _env(db: str, engine_db: str) -> dict:
    return {**os.environ, "HARNESS_DATABASE_URL": db, "DATABASE_URL_ENGINE": engine_db}


def ingest(variant_dir: Path, db: str, engine_db: str) -> str:
    out = subprocess.run(["uv", "run", "htn", "ingest", str(variant_dir / "docs"), "--dataset-name",
                          f"phase2-{variant_dir.name}"], env=_env(db, engine_db), capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{variant_dir.name}: ingest failed\n{out.stderr[-2000:]}")
    return json.loads(out.stdout)["dataset_version_id"]


def structure(variant_dir: Path, db: str, version: str) -> dict:
    eng = create_engine(db)
    with eng.connect() as c:
        q = lambda sql: c.execute(text(sql), {"v": version})
        entities = sorted(r[0] for r in q("SELECT label FROM entity WHERE dataset_version_id = :v"))
        facts, with_period = q("SELECT count(*), count(period_label) FROM fact WHERE dataset_version_id = :v").one()
        spans, no_path = q("SELECT count(*), count(*) FILTER (WHERE cardinality(heading_path) = 0) "
                           "FROM source_span WHERE dataset_version_id = :v").one()
        findings = dict(q("SELECT rule::text, count(*) FROM validation_finding WHERE dataset_version_id = :v GROUP BY 1").all())
        periods = sorted({r[0] for r in q("SELECT DISTINCT period_label FROM fact WHERE dataset_version_id = :v "
                                           "AND period_label IS NOT NULL")})
        got = q("""SELECT d.name, s.line_start, e.label, f.role::text AS role, f.value, f.value_low, f.value_high,
                          f.value_text FROM fact f JOIN source_span s ON s.span_id = f.span_id
                   JOIN document d ON d.document_id = s.document_id LEFT JOIN entity e ON e.entity_id = f.entity_id
                   WHERE f.dataset_version_id = :v""").all()
    expected = list(csv.DictReader(open(variant_dir / "expected_facts.csv", encoding="utf-8")))
    missed = [e["fact_key"] for e in expected if not any(
        g.name == e["document_name"] and g.line_start == int(e["line_start"]) and (g.label or "") == e["entity_label"]
        and g.role == e["role"] and _close(g.value, _f(e["value"])) and _close(g.value_low, _f(e["value_low"]))
        and _close(g.value_high, _f(e["value_high"]))
        and (not e["value_text"] or e["value"] != "" or (g.value_text or "") == e["value_text"]) for g in got)]
    return {"entities": entities, "facts": facts, "facts_with_period": with_period, "spans": spans,
            "spans_without_path": no_path, "findings": findings, "period_labels": len(periods),
            "period_sample": periods[:6], "expected_facts": f"{len(expected) - len(missed)}/{len(expected)}",
            "missed_facts": missed}


def evaluate(variant_dir: Path, db: str, engine_db: str, version: str) -> dict:
    out = subprocess.run(["uv", "run", "htn", "eval", "--questions", str(variant_dir / "rbc_sample.json"),
                          "--version", version, "--json"], env=_env(db, engine_db), capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{variant_dir.name}: eval failed\n{out.stderr[-2000:]}")
    (variant_dir / "eval.json").write_text(out.stdout)
    return score(json.loads(out.stdout))


def score(rep: dict) -> dict:
    results = rep["cases"]
    return {"passed": sum(r["passed"] for r in results), "cases": len(results),
            "company_right": sum(r["values_ok"] for r in results),
            "failed": {r["case_id"]: f'{r.get("failure_class") or ""} {r.get("detail") or ""}'.strip()[:160]
                       for r in results if not r["passed"]}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--db", required=True, help="owner URL (ingest)")
    ap.add_argument("--engine-db", help="engine-role URL (eval); default: htn_engine on the same server")
    ap.add_argument("--variants", nargs="*")
    ap.add_argument("--skip-eval", action="store_true")
    ap.add_argument("--eval-only", action="store_true", help="reuse the versions in <root>/summary.json")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    engine_db = a.engine_db or a.db.replace("harness:harness@", "htn_engine:htn_engine@")
    dirs = [a.root / v for v in (a.variants or sorted(p.name for p in a.root.iterdir() if (p / "docs").is_dir()))]

    if a.eval_only:
        summary = json.loads((a.root / "summary.json").read_text())
        versions = {d.name: summary[d.name]["version"] for d in dirs}
    else:
        versions = {}
        for d in dirs:                                       # ingest everything first, then evaluate
            versions[d.name] = ingest(d, a.db, engine_db)
            print(f"ingested {d.name}: {versions[d.name]}", file=sys.stderr)
        summary = {d.name: {"version": versions[d.name], **structure(d, a.db, versions[d.name])} for d in dirs}
    if not a.skip_eval:
        with ThreadPoolExecutor(a.workers) as pool:
            for d, res in zip(dirs, pool.map(lambda d: evaluate(d, a.db, engine_db, versions[d.name]), dirs)):
                summary[d.name]["rbc"] = res
                print(f"evaluated {d.name}: {res['passed']}/{res['cases']}", file=sys.stderr)
    (a.root / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
