"""Seed the contract fixture into PostgreSQL as a ready dataset version (test-only stand-in for A's ingest).

    uv run python -m tests.engine.seed_fixture postgresql+psycopg://postgres:postgres@localhost:55432/htn_test

Resets the public schema, applies contracts/schema.sql and tests/engine/grants.sql, then loads
contracts/fixture via fixture_data.load(). Pass --versions 2 to add a second version (isolation tests).
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from tests.engine.fixture_data import FixtureData, load

ROOT = Path(__file__).resolve().parents[2]


def reset_schema(admin_url: str) -> None:
    eng = create_engine(admin_url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
        c.exec_driver_sql((ROOT / "contracts" / "schema.sql").read_text())
        c.exec_driver_sql((Path(__file__).parent / "grants.sql").read_text())
    eng.dispose()


def seed(admin_url: str, data: FixtureData, version_no: int = 1, embedder=None) -> None:
    """Load one fixture version; with an embedder, also write chunk embeddings (stands in for A's ingest)."""
    eng = create_engine(admin_url)
    v = str(data.dataset_version_id)
    with eng.begin() as c:
        c.execute(text("""INSERT INTO dataset (dataset_id, name, source_type, source_uri)
                          VALUES (:d, 'fixture', 'file', 'contracts/fixture/raw') ON CONFLICT DO NOTHING"""),
                  {"d": str(data.dataset_id)})
        c.execute(text("""INSERT INTO dataset_version (dataset_version_id, dataset_id, version_no, status, source_hash,
                                                       parser_version, ready_at)
                          VALUES (:v, :d, :n, 'ready', :h, 'fixture-seed', now() + make_interval(secs => :n))"""),
                  {"v": v, "d": str(data.dataset_id), "n": version_no, "h": data.source_hash})
        for doc in data.docs.values():
            c.execute(text("""INSERT INTO document (document_id, dataset_version_id, name, title, media_type, sha256,
                                                    raw_object_key, canonical_text, line_count)
                              VALUES (:id, :v, :n, :t, 'text/markdown', :h, :k, :txt, :lc)"""),
                      {"id": str(doc.document_id), "v": v, "n": doc.name, "t": doc.lines[0].lstrip("# "),
                       "h": doc.sha256, "k": f"fixture/{doc.name}", "txt": doc.text, "lc": len(doc.lines)})
        spans = {}
        for f in data.facts:
            spans[f.span.span_id] = f.span
        for ch in data.chunks:
            spans[ch.span.span_id] = ch.span
        for s in spans.values():
            c.execute(text("""INSERT INTO source_span (span_id, dataset_version_id, document_id, line_start, line_end,
                                                       char_start, char_end, exact_text, heading_path)
                              VALUES (:id, :v, :doc, :ls, :le, :cs, :ce, :t, :hp)"""),
                      {"id": str(s.span_id), "v": v, "doc": str(s.doc.document_id), "ls": s.line_start,
                       "le": s.line_end, "cs": s.char_start, "ce": s.char_end, "t": s.exact_text,
                       "hp": s.doc.heading_path(s.line_start)})
        for label, eid in data.entities.items():
            c.execute(text("INSERT INTO entity (entity_id, dataset_version_id, label) VALUES (:e, :v, :l)"),
                      {"e": str(eid), "v": v, "l": label})
            for alias in sorted(data.aliases.get(label, {label.lower()})):
                c.execute(text("""INSERT INTO entity_alias (dataset_version_id, entity_id, alias, origin)
                                  VALUES (:v, :e, :a, 'document')"""), {"v": v, "e": str(eid), "a": alias})
        for f in data.facts:
            c.execute(text("""INSERT INTO fact (fact_id, dataset_version_id, entity_id, span_id, metric, metric_label,
                                value, value_low, value_high, value_text, original_value, unit, currency, scale,
                                is_estimate, basis, role, period_label, period_end, period_type, extraction_confidence)
                              VALUES (:id, :v, :e, :s, :m, :ml, :val, :lo, :hi, :vt, :ov, CAST(:u AS value_unit), :cur,
                                      :sc, :est, CAST(:b AS fact_basis), CAST(:r AS fact_role), :pl, :pe,
                                      CAST(:pt AS period_type), 1.0)"""),
                      {"id": str(f.fact_id), "v": v, "e": str(data.entities[f.entity_label]) if f.entity_label else None,
                       "s": str(f.span.span_id), "m": f.metric, "ml": f.metric_label, "val": f.value,
                       "lo": f.value_low, "hi": f.value_high, "vt": f.value_text, "ov": f.original_value, "u": f.unit,
                       "cur": f.currency, "sc": f.scale, "est": f.is_estimate, "b": f.basis, "r": f.role,
                       "pl": f.period_label, "pe": f.period_end, "pt": f.period_type})
        for ch in data.chunks:
            emb = None
            if embedder is not None:
                emb = "[" + ",".join(f"{x:.7g}" for x in embedder.embed_query(ch.text)) + "]"
            c.execute(text("""INSERT INTO chunk (chunk_id, dataset_version_id, span_id, text, token_count, embedding)
                              VALUES (:id, :v, :s, :t, :n, CAST(:e AS vector))"""),
                      {"id": str(ch.chunk_id), "v": v, "s": str(ch.span.span_id), "t": ch.text,
                       "n": len(ch.text.split()), "e": emb})
        for fd in data.findings:
            c.execute(text("""INSERT INTO validation_finding (finding_id, dataset_version_id, rule, rule_version,
                                severity, explanation, fact_ids, span_ids, expected, observed)
                              VALUES (:id, :v, CAST(:r AS finding_rule), 'fixture', CAST(:sev AS finding_severity),
                                      :ex, CAST(:f AS uuid[]), CAST(:s AS uuid[]), :exp, :obs)"""),
                      {"id": str(fd.finding_id), "v": v, "r": fd.rule, "sev": fd.severity, "ex": fd.explanation,
                       "f": [str(x) for x in fd.fact_ids], "s": [str(s.span_id) for s in fd.spans],
                       "exp": fd.expected, "obs": fd.observed})
    eng.dispose()


def main(argv: list[str]) -> None:
    url = argv[1] if len(argv) > 1 else "postgresql+psycopg://postgres:postgres@localhost:55432/htn_test"
    versions = int(argv[argv.index("--versions") + 1]) if "--versions" in argv else 1
    reset_schema(url)
    for n in range(1, versions + 1):
        seed(url, load(f"v{n}"), version_no=n)
    print(f"seeded {versions} fixture version(s) into {url.rsplit('@', 1)[-1]}")


if __name__ == "__main__":
    main(sys.argv)
