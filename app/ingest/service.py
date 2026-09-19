"""Contract-backed synchronous file ingestion for the first evidence vertical slice."""
from __future__ import annotations

import hashlib
import time
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from contracts.models import DatasetProfile, DatasetStatus, DocumentReport, IngestReport, IngestRequest, ProfileEntry, SourceType

from .adapters import FileSourceAdapter, McpSourceAdapter, SnapshotDocument
from .canonical import CanonicalDocument, canonicalize_text
from .normalize import normalize_number
from .parse import ParsedTable, parse_csv_table, parse_markdown_tables
from .storage import ImmutableRawStorage


class FileIngestService:
    """Writes raw, citable evidence only to a new immutable dataset version."""

    def __init__(self, engine: Engine, storage: ImmutableRawStorage, parser_version: str,
                 mcp_adapter: McpSourceAdapter | None = None) -> None:
        self.engine, self.storage, self.parser_version = engine, storage, parser_version
        self.adapter = FileSourceAdapter()
        self.mcp_adapter = mcp_adapter

    def submit(self, request: IngestRequest) -> UUID:
        with self.engine.begin() as conn:
            if request.idempotency_key:
                existing = conn.scalar(text("SELECT job_id FROM job WHERE idempotency_key = :key"), {"key": request.idempotency_key})
                if existing:
                    return existing
            job_id = uuid4()
            conn.execute(text("""INSERT INTO job (job_id, type, idempotency_key, payload, state)
                              VALUES (:id, 'ingest', :key, CAST(:payload AS jsonb), 'queued')"""),
                         {"id": job_id, "key": request.idempotency_key, "payload": request.model_dump_json()})
            return job_id

    def report(self, job_id: UUID) -> IngestReport:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT result FROM job WHERE job_id = :id"), {"id": job_id}).scalar_one_or_none()
        if row is None:
            with self.engine.connect() as conn:
                exists = conn.execute(text("SELECT 1 FROM job WHERE job_id = :id"), {"id": job_id}).scalar_one_or_none()
            if exists is None:
                raise KeyError(f"unknown job {job_id}")
            raise RuntimeError("job has not produced a report")
        return IngestReport.model_validate(row)

    def run_job(self, job_id: UUID) -> IngestReport:
        """Execute an already-claimed queue item and persist its contract report."""
        with self.engine.connect() as conn:
            payload = conn.execute(text("SELECT payload FROM job WHERE job_id = :id"), {"id": job_id}).scalar_one()
        request = IngestRequest.model_validate(payload)
        try:
            report = self.run_sync(request).model_copy(update={"job_id": job_id})
        except Exception:
            raise
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE job SET state = 'succeeded', result = CAST(:result AS jsonb) WHERE job_id = :id"),
                         {"id": job_id, "result": report.model_dump_json()})
        return report

    def profile(self, dataset_version_id: UUID) -> DatasetProfile:
        """Return an evidence-store profile without touching data from another version."""
        with self.engine.connect() as conn:
            version = conn.execute(text("""SELECT d.dataset_id, v.status, v.source_hash, v.parser_version
                FROM dataset_version v JOIN dataset d USING (dataset_id) WHERE v.dataset_version_id = :id"""),
                {"id": dataset_version_id}).mappings().one()
            documents = [DocumentReport(document_id=row["document_id"], name=row["name"], sha256=row["sha256"], lines=row["line_count"],
                         chunks=row["chunks"], tables=row["tables"], table_cells=row["cells"], facts=row["facts"], untyped_cells=row["cells"] - row["facts"])
                         for row in conn.execute(text("""SELECT d.document_id, d.name, d.sha256, d.line_count,
                         (SELECT count(*) FROM chunk c JOIN source_span s ON s.span_id = c.span_id WHERE s.document_id = d.document_id) chunks,
                         (SELECT count(*) FROM source_table t JOIN source_span s ON s.span_id = t.span_id WHERE s.document_id = d.document_id) tables,
                         (SELECT count(*) FROM table_cell c JOIN source_span s ON s.span_id = c.span_id WHERE s.document_id = d.document_id) cells,
                         (SELECT count(*) FROM fact f JOIN source_span s ON s.span_id = f.span_id WHERE s.document_id = d.document_id) facts
                         FROM document d WHERE d.dataset_version_id = :id"""), {"id": dataset_version_id}).mappings()]
            def entries(sql: str) -> list[ProfileEntry]:
                return [ProfileEntry(label=r["label"] or "not stated", count=r["count"])
                        for r in conn.execute(text(sql), {"id": dataset_version_id}).mappings()]
            entities = entries("SELECT label, count(*) FROM entity WHERE dataset_version_id = :id GROUP BY label ORDER BY label")
            metrics = entries("SELECT metric AS label, count(*) FROM fact WHERE dataset_version_id = :id GROUP BY metric ORDER BY metric")
            periods = entries("SELECT coalesce(period_label, 'not stated') AS label, count(*) FROM fact WHERE dataset_version_id = :id GROUP BY period_label")
            currencies = entries("SELECT coalesce(currency, 'not stated') AS label, count(*) FROM fact WHERE dataset_version_id = :id GROUP BY currency")
            units = entries("SELECT unit::text AS label, count(*) FROM fact WHERE dataset_version_id = :id GROUP BY unit")
            finding_rows = conn.execute(text("SELECT rule::text AS rule, count(*) FROM validation_finding WHERE dataset_version_id = :id GROUP BY rule"), {"id": dataset_version_id}).mappings()
            findings = {row["rule"]: row["count"] for row in finding_rows}
            cells = conn.scalar(text("SELECT count(*) FROM table_cell WHERE dataset_version_id = :id"), {"id": dataset_version_id}) or 0
            typed = conn.scalar(text("SELECT count(*) FROM table_cell WHERE dataset_version_id = :id AND typed"), {"id": dataset_version_id}) or 0
        from contracts.models import FindingRule
        return DatasetProfile(dataset_id=version["dataset_id"], dataset_version_id=dataset_version_id, status=version["status"],
                              source_hash=version["source_hash"], parser_version=version["parser_version"], documents=documents,
                              entities=entities, metrics=metrics, periods=periods, currencies=currencies, units=units,
                              table_coverage=typed / cells if cells else 0, extraction_warnings=[],
                              findings_by_rule={FindingRule(rule): count for rule, count in findings.items()})

    def run_sync(self, request: IngestRequest) -> IngestReport:
        started = time.monotonic()
        if request.source == SourceType.file:
            documents = self.adapter.snapshot(request)
        elif self.mcp_adapter:
            documents = self.mcp_adapter.snapshot(request)
        else:
            raise RuntimeError("MCP ingestion requires a configured McpSourceAdapter")
        source_hash = hashlib.sha256("".join(sorted(hashlib.sha256(d.content).hexdigest() for d in documents)).encode()).hexdigest()
        with self.engine.begin() as conn:
            dataset_id = self._dataset(conn, request)
            version_id = self._version(conn, dataset_id, source_hash)
            reports = [self._ingest_document(conn, version_id, raw) for raw in documents]
            findings = self._validate_duplicates(conn, version_id)
            conn.execute(text("UPDATE dataset_version SET status = 'ready', ready_at = now() WHERE dataset_version_id = :id"), {"id": version_id})
        elapsed = round((time.monotonic() - started) * 1000)
        return IngestReport(job_id=uuid4(), dataset_id=dataset_id, dataset_version_id=version_id,
                            status=DatasetStatus.ready, source=request.source, source_hash=source_hash,
                            parser_version=self.parser_version, documents=reports, findings=findings,
                            timings_ms={"total": elapsed})

    def _dataset(self, conn, request: IngestRequest) -> UUID:
        row = conn.execute(text("""INSERT INTO dataset (name, source_type, source_uri)
            VALUES (:name, :source, :uri) ON CONFLICT (name) DO UPDATE SET source_uri = EXCLUDED.source_uri
            RETURNING dataset_id"""), {"name": request.dataset_name, "source": request.source.value, "uri": request.path or ""}).scalar_one()
        return row

    def _version(self, conn, dataset_id: UUID, source_hash: str) -> UUID:
        version_no = conn.execute(text("SELECT COALESCE(MAX(version_no), 0) + 1 FROM dataset_version WHERE dataset_id = :id"), {"id": dataset_id}).scalar_one()
        return conn.execute(text("""INSERT INTO dataset_version (dataset_id, version_no, status, source_hash, parser_version)
              VALUES (:dataset, :number, 'ingesting', :hash, :parser) RETURNING dataset_version_id"""),
                            {"dataset": dataset_id, "number": version_no, "hash": source_hash, "parser": self.parser_version}).scalar_one()

    def _span(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument, line_start: int, line_end: int,
              char_start: int | None = None, char_end: int | None = None) -> UUID:
        if char_start is None or char_end is None:
            char_start, char_end, exact = canonical.span_for_lines(line_start, line_end)
        else:
            exact = canonical.text[char_start:char_end]
        return conn.execute(text("""INSERT INTO source_span (dataset_version_id, document_id, line_start, line_end, char_start, char_end, exact_text)
            VALUES (:version, :document, :start_line, :end_line, :start, :end, :exact) RETURNING span_id"""),
                            {"version": version, "document": document, "start_line": line_start, "end_line": line_end,
                             "start": char_start, "end": char_end, "exact": exact}).scalar_one()

    def _ingest_document(self, conn, version: UUID, raw: SnapshotDocument) -> DocumentReport:
        key, digest = self.storage.put(raw.content)
        try:
            canonical = canonicalize_text(raw.content)
        except UnicodeDecodeError as exc:
            raise ValueError(f"{raw.name}: binary canonicalization is not implemented") from exc
        document_id = conn.execute(text("""INSERT INTO document
            (dataset_version_id, name, media_type, sha256, raw_object_key, canonical_text, line_count, metadata)
            VALUES (:version, :name, :media, :hash, :key, :body, :lines, CAST(:metadata AS jsonb)) RETURNING document_id"""),
            {"version": version, "name": raw.name, "media": raw.media_type, "hash": digest, "key": key,
             "body": canonical.text, "lines": len(canonical.lines), "metadata": "{}"}).scalar_one()
        chunks = self._chunks(conn, version, document_id, canonical)
        tables = parse_markdown_tables(canonical) if raw.name.lower().endswith((".md", ".markdown", ".txt")) else []
        if raw.name.lower().endswith(".csv"):
            table = parse_csv_table(canonical)
            tables = [table] if table else []
        cells, facts = 0, 0
        for table in tables:
            table_cells, table_facts = self._table(conn, version, document_id, canonical, table)
            cells += table_cells
            facts += table_facts
        return DocumentReport(document_id=document_id, name=raw.name, sha256=digest, lines=len(canonical.lines),
                              chunks=chunks, tables=len(tables), table_cells=cells, facts=facts,
                              untyped_cells=cells - facts)

    def _chunks(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument) -> int:
        count, start = 0, None
        for index, line in enumerate(canonical.lines, start=1):
            if line.text.strip() and start is None:
                start = index
            if start is not None and (not line.text.strip() or index == len(canonical.lines)):
                end = index - 1 if not line.text.strip() else index
                span = self._span(conn, version, document, canonical, start, end)
                body = canonical.span_for_lines(start, end)[2]
                conn.execute(text("INSERT INTO chunk (dataset_version_id, span_id, text, token_count) VALUES (:version, :span, :body, :tokens)"),
                             {"version": version, "span": span, "body": body, "tokens": len(body.split())})
                count, start = count + 1, None
        return count

    def _table(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument, table: ParsedTable) -> tuple[int, int]:
        span = self._span(conn, version, document, canonical, table.start_line, table.end_line)
        table_id = conn.execute(text("""INSERT INTO source_table (dataset_version_id, span_id, header_rows, n_rows, n_cols)
            VALUES (:version, :span, CAST(:headers AS jsonb), :rows, :cols) RETURNING table_id"""),
            {"version": version, "span": span, "headers": __import__("json").dumps([table.headers]), "rows": len(table.rows), "cols": len(table.headers)}).scalar_one()
        facts = 0
        for row_index, (row, line_number) in enumerate(zip(table.rows, table.row_lines)):
            entity_label = row[0] or None
            entity_id = self._entity(conn, version, entity_label) if entity_label else None
            cursor = 0
            for col_index, cell in enumerate(row):
                line = canonical.lines[line_number - 1]
                column = line.text.find(cell, cursor)
                column = column if column >= 0 else cursor
                cursor = column + len(cell)
                start, end, _ = canonical.span_for_text(line_number, column, column + len(cell))
                cell_span = self._span(conn, version, document, canonical, line_number, line_number, start, end)
                conn.execute(text("""INSERT INTO table_cell (dataset_version_id, table_id, span_id, row_idx, col_idx, row_label, header_path, raw_text)
                   VALUES (:version, :table, :span, :row, :col, :label, :headers, :raw)"""),
                    {"version": version, "table": table_id, "span": cell_span, "row": row_index, "col": col_index,
                     "label": entity_label, "headers": list(table.headers[:col_index + 1]), "raw": cell})
                if col_index == 0 or not cell:
                    continue
                normalized = normalize_number(cell, table.headers[col_index])
                if normalized is None:
                    continue
                conn.execute(text("""INSERT INTO fact (dataset_version_id, entity_id, span_id, metric, metric_label, value, original_value,
                    unit, currency, scale, role, period_type, extraction_confidence)
                    VALUES (:version, :entity, :span, :metric, :label, :value, :original, :unit, :currency, :scale, 'actual', 'unspecified', 0.85)"""),
                    {"version": version, "entity": entity_id, "span": cell_span,
                     "metric": "_".join(table.headers[col_index].lower().split()), "label": table.headers[col_index],
                     "value": normalized.value, "original": cell, "unit": normalized.unit.value,
                     "currency": normalized.currency, "scale": normalized.scale})
                facts += 1
                conn.execute(text("UPDATE table_cell SET typed = true WHERE span_id = :span"), {"span": cell_span})
        return len(table.rows) * len(table.headers), facts

    def _entity(self, conn, version: UUID, label: str) -> UUID:
        return conn.execute(text("""INSERT INTO entity (dataset_version_id, label) VALUES (:version, :label)
            ON CONFLICT (dataset_version_id, label) DO UPDATE SET label = EXCLUDED.label RETURNING entity_id"""),
                            {"version": version, "label": label}).scalar_one()

    def _validate_duplicates(self, conn, version: UUID) -> int:
        """Persist duplicate claims with incompatible numeric values; other rules follow the same pattern."""
        groups = conn.execute(text("""SELECT entity_id, metric, period_label, basis, array_agg(fact_id) ids, array_agg(span_id) spans
            FROM fact WHERE dataset_version_id = :version GROUP BY entity_id, metric, period_label, basis
            HAVING COUNT(DISTINCT value) > 1"""), {"version": version}).mappings()
        count = 0
        for group in groups:
            conn.execute(text("""INSERT INTO validation_finding (dataset_version_id, rule, rule_version, severity, explanation, fact_ids, span_ids)
              VALUES (:version, 'duplicate_claim', '0.1.0', 'medium', 'Conflicting values share the same entity, metric, period, and basis.', :facts, :spans)"""),
                         {"version": version, "facts": group["ids"], "spans": group["spans"]})
            count += 1
        return count
