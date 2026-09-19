"""Read-only PostgreSQL implementation of contracts.repositories.EvidenceRepository.

Queries the contract schema (contracts/schema.sql) with SQLAlchemy Core. Every query is
scoped to one dataset_version_id. This module never writes; it connects with the
htn_engine role, which has no write grants on evidence tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Engine, create_engine, text

from contracts.models import (
    Basis, DatasetProfile, DatasetStatus, DocumentReport, EvidenceHit, EvidenceKind, Fact, FactFilter,
    FactValue, FindingRule, Period, PeriodType, ProfileEntry, Role, SourceSpan, Unit, ValidationFinding,
)


@dataclass(frozen=True)
class EntityRecord:
    entity_id: UUID
    label: str
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VersionInfo:
    dataset_id: UUID
    dataset_version_id: UUID
    dataset_name: str
    source_hash: str
    parser_version: str


_FACT_SELECT = """
SELECT f.fact_id, f.dataset_version_id, f.entity_id, e.label AS entity_label, f.metric, f.metric_label,
       f.value, f.value_low, f.value_high, f.value_text, f.original_value, f.unit::text AS unit, f.unit_label,
       f.currency, f.scale, f.is_estimate, f.qualifier, f.basis::text AS basis, f.role::text AS role,
       f.period_label, f.period_end, f.period_type::text AS period_type, f.extraction_confidence,
       s.span_id, s.line_start, s.line_end, s.char_start, s.char_end, s.exact_text, s.heading_path,
       d.document_id, d.name AS document_name, d.sha256 AS document_hash
FROM fact f
JOIN source_span s ON s.span_id = f.span_id
JOIN document d ON d.document_id = s.document_id
LEFT JOIN entity e ON e.entity_id = f.entity_id
"""


def _span(r: Any) -> SourceSpan:
    return SourceSpan(
        span_id=r.span_id, document_id=r.document_id, document_name=r.document_name,
        document_hash=r.document_hash.strip(), line_start=r.line_start, line_end=r.line_end,
        char_start=r.char_start, char_end=r.char_end, exact_text=r.exact_text,
        heading_path=list(r.heading_path or []),
    )


def _fact(r: Any) -> Fact:
    return Fact(
        fact_id=r.fact_id, dataset_version_id=r.dataset_version_id, entity_id=r.entity_id,
        entity_label=r.entity_label, metric=r.metric, metric_label=r.metric_label,
        value=FactValue(
            value=r.value, value_low=r.value_low, value_high=r.value_high, value_text=r.value_text,
            original_value=r.original_value, unit=Unit(r.unit), unit_label=r.unit_label,
            currency=(r.currency or None) and r.currency.strip(), scale=r.scale, is_estimate=r.is_estimate,
            qualifier=r.qualifier,
        ),
        basis=Basis(r.basis) if r.basis else None, role=Role(r.role),
        period=Period(label=r.period_label, end=r.period_end, type=PeriodType(r.period_type)),
        span=_span(r), extraction_confidence=r.extraction_confidence,
    )


class PgEvidenceRepository:
    """contracts.repositories.EvidenceRepository over PostgreSQL."""

    def __init__(self, engine: Engine | str):
        self.engine = create_engine(engine) if isinstance(engine, str) else engine

    # ------------------------------------------------------------- datasets --

    def resolve_version(self, dataset: str | UUID = "latest", version: UUID | str = "latest") -> UUID:
        return self.version_info(dataset, version).dataset_version_id

    def version_info(self, dataset: str | UUID = "latest", version: UUID | str = "latest") -> VersionInfo:
        sql = """
            SELECT v.dataset_id, v.dataset_version_id, ds.name, v.source_hash, v.parser_version
            FROM dataset_version v JOIN dataset ds ON ds.dataset_id = v.dataset_id
            WHERE v.status = 'ready'
        """
        params: dict[str, Any] = {}
        if isinstance(version, UUID) or (isinstance(version, str) and version != "latest"):
            sql += " AND v.dataset_version_id = :version"
            params["version"] = str(version)
        if isinstance(dataset, UUID):
            sql += " AND v.dataset_id = :dataset"
            params["dataset"] = str(dataset)
        elif dataset != "latest":
            sql += " AND ds.name = :dataset"
            params["dataset"] = dataset
        sql += " ORDER BY v.ready_at DESC NULLS LAST, v.version_no DESC LIMIT 1"
        with self.engine.connect() as c:
            r = c.execute(text(sql), params).first()
        if r is None:
            raise LookupError(f"no ready dataset version for dataset={dataset!r} version={version!r}")
        return VersionInfo(r.dataset_id, r.dataset_version_id, r.name, r.source_hash or "", r.parser_version)

    def profile(self, dataset_version_id: UUID) -> DatasetProfile:
        v = str(dataset_version_id)
        with self.engine.connect() as c:
            head = c.execute(text("""
                SELECT dataset_id, status::text AS status, coalesce(source_hash, '') AS source_hash,
                       parser_version, coalesce(quality_report, '{}'::jsonb) AS qr
                FROM dataset_version WHERE dataset_version_id = :v"""), {"v": v}).one()
            docs = c.execute(text("""
                SELECT d.document_id, d.name, d.sha256, d.line_count,
                  (SELECT count(*) FROM chunk k JOIN source_span s ON s.span_id = k.span_id
                     WHERE s.document_id = d.document_id) AS chunks,
                  (SELECT count(*) FROM source_table t JOIN source_span s ON s.span_id = t.span_id
                     WHERE s.document_id = d.document_id) AS tables,
                  (SELECT count(*) FROM table_cell tc JOIN source_span s ON s.span_id = tc.span_id
                     WHERE s.document_id = d.document_id) AS cells,
                  (SELECT count(*) FROM table_cell tc JOIN source_span s ON s.span_id = tc.span_id
                     WHERE s.document_id = d.document_id AND NOT tc.typed) AS untyped,
                  (SELECT count(*) FROM fact f JOIN source_span s ON s.span_id = f.span_id
                     WHERE s.document_id = d.document_id) AS facts
                FROM document d WHERE d.dataset_version_id = :v ORDER BY d.name"""), {"v": v}).all()

            def entries(sql: str) -> list[ProfileEntry]:
                return [ProfileEntry(label=r[0], count=r[1]) for r in c.execute(text(sql), {"v": v})]

            entities = entries("""SELECT e.label, count(f.fact_id) FROM entity e
                LEFT JOIN fact f ON f.entity_id = e.entity_id WHERE e.dataset_version_id = :v
                GROUP BY e.label ORDER BY count(f.fact_id) DESC, e.label""")
            metrics = entries("""SELECT metric, count(*) FROM fact WHERE dataset_version_id = :v
                GROUP BY metric ORDER BY count(*) DESC, metric""")
            periods = entries("""SELECT period_label, count(*) FROM fact
                WHERE dataset_version_id = :v AND period_label IS NOT NULL
                GROUP BY period_label ORDER BY min(period_end) NULLS LAST, period_label""")
            currencies = entries("""SELECT currency, count(*) FROM fact
                WHERE dataset_version_id = :v AND currency IS NOT NULL GROUP BY currency ORDER BY 2 DESC""")
            units = entries("""SELECT unit::text, count(*) FROM fact WHERE dataset_version_id = :v
                GROUP BY unit ORDER BY 2 DESC""")
            by_rule = {FindingRule(r[0]): r[1] for r in c.execute(text("""
                SELECT rule::text, count(*) FROM validation_finding
                WHERE dataset_version_id = :v AND status = 'open' GROUP BY rule"""), {"v": v})}

        total_cells = sum(d.cells for d in docs)
        typed = total_cells - sum(d.untyped for d in docs)
        qr = head.qr or {}
        return DatasetProfile(
            dataset_id=head.dataset_id, dataset_version_id=dataset_version_id, status=DatasetStatus(head.status),
            source_hash=head.source_hash, parser_version=head.parser_version,
            documents=[DocumentReport(document_id=d.document_id, name=d.name, sha256=d.sha256.strip(),
                                      lines=d.line_count, chunks=d.chunks, tables=d.tables,
                                      table_cells=d.cells, facts=d.facts, untyped_cells=d.untyped)
                       for d in docs],
            entities=entities, metrics=metrics, periods=periods, currencies=currencies, units=units,
            table_coverage=(typed / total_cells) if total_cells else 1.0,
            extraction_warnings=list(qr.get("extraction_warnings", [])),
            findings_by_rule=by_rule,
        )

    # ------------------------------------------------- entities & vocabulary --

    def list_entities(self, dataset_version_id: UUID) -> list[EntityRecord]:
        with self.engine.connect() as c:
            rows = c.execute(text("""
                SELECT e.entity_id, e.label, coalesce(array_agg(a.alias) FILTER (WHERE a.alias IS NOT NULL), '{}')
                FROM entity e LEFT JOIN entity_alias a ON a.entity_id = e.entity_id
                WHERE e.dataset_version_id = :v GROUP BY e.entity_id, e.label ORDER BY e.label"""),
                {"v": str(dataset_version_id)}).all()
        return [EntityRecord(r[0], r[1], sorted(r[2])) for r in rows]

    def resolve_entity(self, dataset_version_id: UUID, name: str) -> list[EntityRecord]:
        q = name.strip().lower()
        if not q:
            return []
        with self.engine.connect() as c:
            rows = c.execute(text("""
                WITH cand AS (
                  SELECT e.entity_id, e.label,
                         min(CASE WHEN lower(e.label) = :q OR a.alias = :q THEN 0
                                  WHEN a.alias LIKE :q || '%' OR lower(e.label) LIKE :q || '%' THEN 1
                                  ELSE 2 END) AS rank
                  FROM entity e LEFT JOIN entity_alias a ON a.entity_id = e.entity_id
                  WHERE e.dataset_version_id = :v
                    AND (lower(e.label) = :q OR a.alias = :q OR a.alias LIKE '%' || :q || '%'
                         OR lower(e.label) LIKE '%' || :q || '%')
                  GROUP BY e.entity_id, e.label)
                SELECT entity_id, label FROM cand ORDER BY rank, length(label), label LIMIT 10"""),
                {"v": str(dataset_version_id), "q": q}).all()
        by_id = {e.entity_id: e for e in self.list_entities(dataset_version_id)}
        return [by_id[r[0]] for r in rows]

    def list_metrics(self, dataset_version_id: UUID, entity_id: Optional[UUID] = None) -> list[str]:
        sql = "SELECT DISTINCT metric FROM fact WHERE dataset_version_id = :v"
        params: dict[str, Any] = {"v": str(dataset_version_id)}
        if entity_id:
            sql += " AND entity_id = :e"
            params["e"] = str(entity_id)
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(text(sql + " ORDER BY metric"), params)]

    def list_periods(self, dataset_version_id: UUID, entity_id: Optional[UUID] = None) -> list[str]:
        sql = """SELECT period_label FROM fact WHERE dataset_version_id = :v AND period_label IS NOT NULL"""
        params: dict[str, Any] = {"v": str(dataset_version_id)}
        if entity_id:
            sql += " AND entity_id = :e"
            params["e"] = str(entity_id)
        sql += " GROUP BY period_label ORDER BY min(period_end) NULLS LAST, period_label"
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(text(sql), params)]

    # --------------------------------------------------------- structured --

    def find_facts(self, flt: FactFilter) -> list[Fact]:
        where = ["f.dataset_version_id = :v"]
        p: dict[str, Any] = {"v": str(flt.dataset_version_id), "limit": flt.limit}
        if flt.entity_ids:
            where.append("f.entity_id = ANY(CAST(:entity_ids AS uuid[]))")
            p["entity_ids"] = [str(e) for e in flt.entity_ids]
        if flt.metrics:
            where.append("f.metric = ANY(:metrics)")
            p["metrics"] = list(flt.metrics)
        if flt.metric_text:
            where.append("(f.metric ILIKE :mt OR f.metric_label ILIKE :mt)")
            p["mt"] = f"%{flt.metric_text.replace(' ', '%')}%"
        if flt.roles:
            where.append("f.role::text = ANY(:roles)")
            p["roles"] = [r.value for r in flt.roles]
        if flt.bases:
            where.append("f.basis::text = ANY(:bases)")
            p["bases"] = [b.value for b in flt.bases]
        if flt.period_labels:
            ors = []
            for i, lab in enumerate(flt.period_labels):
                ors.append(f"f.period_label ILIKE :pl{i}")
                p[f"pl{i}"] = f"{lab}%"
            where.append("(" + " OR ".join(ors) + ")")
        if flt.period_from:
            where.append("f.period_end >= :pf")
            p["pf"] = flt.period_from
        if flt.period_to:
            where.append("f.period_end <= :pt")
            p["pt"] = flt.period_to
        if flt.currencies:
            where.append("f.currency = ANY(:cur)")
            p["cur"] = list(flt.currencies)
        if flt.units:
            where.append("f.unit::text = ANY(:units)")
            p["units"] = [u.value for u in flt.units]
        if not flt.include_estimates:
            where.append("NOT f.is_estimate")
        sql = (_FACT_SELECT + " WHERE " + " AND ".join(where)
               + " ORDER BY f.period_end NULLS LAST, d.name, s.line_start, s.char_start LIMIT :limit")
        with self.engine.connect() as c:
            return [_fact(r) for r in c.execute(text(sql), p)]

    def get_fact(self, dataset_version_id: UUID, fact_id: UUID) -> Fact:
        with self.engine.connect() as c:
            r = c.execute(text(_FACT_SELECT + " WHERE f.dataset_version_id = :v AND f.fact_id = :id"),
                          {"v": str(dataset_version_id), "id": str(fact_id)}).one()
        return _fact(r)

    # --------------------------------------------------------------- text --

    def search_lexical(self, dataset_version_id: UUID, query: str, k: int = 20) -> list[EvidenceHit]:
        sql = """
        WITH q AS (
          -- OR together every query term so partial matches still rank (AND semantics missed passages
          -- that lack one query word, e.g. a ticker the prose never uses).
          SELECT to_tsquery('english', coalesce(nullif(array_to_string(ARRAY(
                   SELECT quote_literal(x) FROM unnest(tsvector_to_array(to_tsvector('english', :q))) x), ' | '), ''),
                   'zzqqnomatch')) AS en,
                 to_tsquery('simple', coalesce(nullif(array_to_string(ARRAY(
                   SELECT quote_literal(x) FROM unnest(tsvector_to_array(to_tsvector('simple', :q))) x), ' | '), ''),
                   'zzqqnomatch')) AS si),
        hits AS (
          SELECT 'chunk' AS kind, k.chunk_id AS id, k.text AS body, k.span_id,
                 ts_rank_cd(k.search_tsv, q.en) AS score
          FROM chunk k, q WHERE k.dataset_version_id = :v AND k.search_tsv @@ q.en
          UNION ALL
          SELECT 'table_cell', tc.cell_id, coalesce(tc.row_label || ': ', '') || tc.raw_text, tc.span_id,
                 ts_rank_cd(tc.search_tsv, q.si)
          FROM table_cell tc, q WHERE tc.dataset_version_id = :v AND tc.search_tsv @@ q.si
          UNION ALL
          SELECT 'fact', f.fact_id, coalesce(f.metric_label, f.metric) || ' ' || f.original_value, f.span_id,
                 ts_rank_cd(f.search_tsv, q.si)
          FROM fact f, q WHERE f.dataset_version_id = :v AND f.search_tsv @@ q.si)
        SELECT h.kind, h.id, h.body, h.score, s.span_id, s.line_start, s.line_end, s.char_start, s.char_end,
               s.exact_text, s.heading_path, d.document_id, d.name AS document_name, d.sha256 AS document_hash
        FROM hits h JOIN source_span s ON s.span_id = h.span_id JOIN document d ON d.document_id = s.document_id
        ORDER BY h.score DESC, d.name, s.line_start LIMIT :k"""
        with self.engine.connect() as c:
            rows = c.execute(text(sql), {"v": str(dataset_version_id), "q": query, "k": k}).all()
        return [EvidenceHit(kind=EvidenceKind(r.kind), evidence_id=r.id, dataset_version_id=dataset_version_id,
                            text=r.body, score=float(r.score), score_components={"lexical": float(r.score)},
                            span=_span(r)) for r in rows]

    def search_semantic(self, dataset_version_id: UUID, embedding: list[float], k: int = 20) -> list[EvidenceHit]:
        return []  # enabled after Checkpoint 1, once chunk embeddings exist

    # ------------------------------------------------------------ sources --

    def get_span(self, dataset_version_id: UUID, span_id: UUID) -> SourceSpan:
        with self.engine.connect() as c:
            r = c.execute(text("""
                SELECT s.*, d.name AS document_name, d.sha256 AS document_hash FROM source_span s
                JOIN document d ON d.document_id = s.document_id
                WHERE s.dataset_version_id = :v AND s.span_id = :id"""),
                {"v": str(dataset_version_id), "id": str(span_id)}).one()
        return _span(r)

    def document_text(self, dataset_version_id: UUID, document_name: str) -> tuple[str, str]:
        with self.engine.connect() as c:
            r = c.execute(text("""SELECT canonical_text, sha256 FROM document
                WHERE dataset_version_id = :v AND name = :n"""),
                {"v": str(dataset_version_id), "n": document_name}).one()
        return r.canonical_text, r.sha256.strip()

    def read_lines(self, dataset_version_id: UUID, document_name: str, line_start: int, line_end: int) -> SourceSpan:
        body, sha = self.document_text(dataset_version_id, document_name)
        return span_from_lines(document_name, sha, body, line_start, line_end)

    # --------------------------------------------------------- validation --

    def list_findings(self, dataset_version_id: UUID, *, entity_id: Optional[UUID] = None,
                      fact_ids: Optional[list[UUID]] = None,
                      rules: Optional[list[FindingRule]] = None) -> list[ValidationFinding]:
        where = ["vf.dataset_version_id = :v", "vf.status = 'open'"]
        p: dict[str, Any] = {"v": str(dataset_version_id)}
        if fact_ids:
            where.append("vf.fact_ids && CAST(:fids AS uuid[])")
            p["fids"] = [str(f) for f in fact_ids]
        if rules:
            where.append("vf.rule::text = ANY(:rules)")
            p["rules"] = [r.value for r in rules]
        if entity_id:
            where.append("EXISTS (SELECT 1 FROM fact f WHERE f.fact_id = ANY(vf.fact_ids) AND f.entity_id = :e)")
            p["e"] = str(entity_id)
        with self.engine.connect() as c:
            rows = c.execute(text("SELECT vf.*, vf.rule::text AS rule_s, vf.severity::text AS sev_s, "
                                  "vf.status::text AS status_s FROM validation_finding vf WHERE "
                                  + " AND ".join(where) + " ORDER BY vf.created_at, vf.finding_id"), p).all()
            out = []
            for r in rows:
                spans = c.execute(text("""
                    SELECT s.*, d.name AS document_name, d.sha256 AS document_hash
                    FROM unnest(CAST(:ids AS uuid[])) WITH ORDINALITY AS u(span_id, ord)
                    JOIN source_span s ON s.span_id = u.span_id JOIN document d ON d.document_id = s.document_id
                    ORDER BY u.ord"""), {"ids": [str(x) for x in r.span_ids]}).all()
                out.append(ValidationFinding(
                    finding_id=r.finding_id, dataset_version_id=r.dataset_version_id, rule=FindingRule(r.rule_s),
                    rule_version=r.rule_version, severity=r.sev_s, status=r.status_s, explanation=r.explanation,
                    fact_ids=list(r.fact_ids), spans=[_span(s) for s in spans], expected=r.expected,
                    observed=r.observed))
        return out


def span_from_lines(document_name: str, sha256: str, body: str, line_start: int, line_end: int) -> SourceSpan:
    """SourceSpan covering whole lines [line_start, line_end] of a canonical text."""
    lines = body.split("\n")
    if not (1 <= line_start <= line_end <= len(lines)):
        raise ValueError(f"lines {line_start}-{line_end} out of range for {document_name}")
    start = sum(len(l) + 1 for l in lines[: line_start - 1])
    chunk = "\n".join(lines[line_start - 1: line_end])
    if not chunk:
        chunk_end = start + 1  # zero-length line: widen to include its newline so the span is valid
        return SourceSpan(document_name=document_name, document_hash=sha256, line_start=line_start,
                          line_end=line_end, char_start=start, char_end=chunk_end, exact_text=body[start:chunk_end])
    return SourceSpan(document_name=document_name, document_hash=sha256, line_start=line_start, line_end=line_end,
                      char_start=start, char_end=start + len(chunk), exact_text=chunk)
