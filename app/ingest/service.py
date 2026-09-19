"""Contract-backed synchronous file ingestion for the first evidence vertical slice."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from app.db import create_engine
from app.settings import settings
from contracts.models import DatasetProfile, DatasetStatus, DocumentReport, IngestReport, IngestRequest, ProfileEntry, SourceType

from .adapters import FileSourceAdapter, McpSourceAdapter, SnapshotDocument
from .embeddings import EmbeddingProvider, NoopEmbeddingProvider, default_embedding_provider
from .canonical import CanonicalDocument, canonicalize_text
from .normalize import normalize_number
from .parse import _DIVIDER as _TABLE_DIVIDER, ParsedTable, parse_csv_table, parse_markdown_tables
from app.markdown import Outline, block_label, period_of
from app.retrieval.context import entity_from_path
from .entities import build_catalog
from .validators import run_all as run_validators
from .extract import Value, cell_values, find_period, labelled_values, leading_period, trend_values
from contracts.models import Unit
from .storage import ImmutableRawStorage


def get_ingest_service() -> "FileIngestService":
    """Factory used by the CLI and any future HTTP/worker entrypoints."""
    return FileIngestService(
        create_engine(settings.database_url),
        ImmutableRawStorage(settings.raw_storage_path),
        settings.parser_version,
    )


class FileIngestService:
    """Writes raw, citable evidence only to a new immutable dataset version."""

    embed_batch = 128

    def __init__(self, engine: Engine, storage: ImmutableRawStorage, parser_version: str,
                 mcp_adapter: McpSourceAdapter | None = None, embedder: EmbeddingProvider | None = None) -> None:
        self.engine, self.storage, self.parser_version = engine, storage, parser_version
        self.adapter = FileSourceAdapter()
        self.mcp_adapter = mcp_adapter
        self.embedder = embedder if embedder is not None else default_embedding_provider()

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
        capabilities = None
        if request.source == SourceType.file:
            documents = self.adapter.snapshot(request)
        elif self.mcp_adapter:
            documents = self.mcp_adapter.snapshot(request)
            capabilities = getattr(self.mcp_adapter, "last_capabilities", None)
        else:
            raise RuntimeError("MCP ingestion requires a configured McpSourceAdapter")
        source_hash = hashlib.sha256("".join(sorted(hashlib.sha256(d.content).hexdigest() for d in documents)).encode()).hexdigest()
        with self.engine.begin() as conn:
            dataset_id = self._dataset(conn, request)
            version_id = self._version(conn, dataset_id, source_hash)
            reports = [self._ingest_document(conn, version_id, raw) for raw in documents]
            findings = self._validate_duplicates(conn, version_id) + run_validators(conn, version_id)
            conn.execute(text("UPDATE dataset_version SET status = 'validating', mcp_capabilities = CAST(:caps AS jsonb) "
                              "WHERE dataset_version_id = :id"),
                         {"id": version_id, "caps": json.dumps(capabilities) if capabilities else None})
        parsed_ms = round((time.monotonic() - started) * 1000)
        # Raw evidence is committed; embeddings come second and can never undo it.
        embedded, warnings = self._embed_chunks(version_id)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE dataset_version SET status = 'ready', ready_at = now() WHERE dataset_version_id = :id"), {"id": version_id})
        elapsed = round((time.monotonic() - started) * 1000)
        return IngestReport(job_id=uuid4(), dataset_id=dataset_id, dataset_version_id=version_id,
                            status=DatasetStatus.ready, source=request.source, source_hash=source_hash,
                            parser_version=self.parser_version, mcp_capabilities=capabilities,
                            documents=reports, findings=findings,
                            warnings=warnings,
                            timings_ms={"parse": parsed_ms, "embed": elapsed - parsed_ms, "total": elapsed})

    def _embed_chunks(self, version: UUID) -> tuple[int, list[str]]:
        """Embed every chunk of a version in batches. Returns (count embedded, warnings)."""
        if isinstance(self.embedder, NoopEmbeddingProvider):
            return 0, ["chunk embeddings skipped: no embedding provider configured (semantic search disabled)"]
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT chunk_id, text FROM chunk WHERE dataset_version_id = :v "
                                     "AND embedding IS NULL ORDER BY chunk_id"), {"v": version}).all()
        done = 0
        for i in range(0, len(rows), self.embed_batch):
            batch = rows[i:i + self.embed_batch]
            try:
                vectors = self.embedder.embed([r.text for r in batch])
            except Exception as exc:  # noqa: BLE001 - provider/API failure must not block ingest
                return done, [f"chunk embeddings stopped after {done}/{len(rows)}: {type(exc).__name__}: {exc}"[:300]]
            if len(vectors) != len(batch):
                return done, [f"embedding provider returned {len(vectors)} vectors for {len(batch)} chunks"]
            with self.engine.begin() as conn:
                conn.execute(text("UPDATE chunk SET embedding = CAST(:e AS vector) WHERE chunk_id = :id"),
                             [{"id": r.chunk_id, "e": "[" + ",".join(f"{x:.7g}" for x in vec) + "]"}
                              for r, vec in zip(batch, vectors)])
            done += len(batch)
        return done, []

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
              char_start: int | None = None, char_end: int | None = None, label: str | None = None) -> UUID:
        if char_start is None or char_end is None:
            char_start, char_end, exact = canonical.span_for_lines(line_start, line_end)
        else:
            exact = canonical.text[char_start:char_end]
        path = self._heading_path(line_start) + ([label] if label else [])
        return conn.execute(text("""INSERT INTO source_span (dataset_version_id, document_id, line_start, line_end, char_start, char_end, exact_text, heading_path)
            VALUES (:version, :document, :start_line, :end_line, :start, :end, :exact, :path) RETURNING span_id"""),
                            {"version": version, "document": document, "start_line": line_start, "end_line": line_end,
                             "start": char_start, "end": char_end, "exact": exact, "path": path}).scalar_one()

    def _heading_path(self, line: int) -> list[str]:
        """Section headings in effect at a line, plus the period of an enclosing <details> block."""
        outline = getattr(self, "_outline", None)
        if outline is None:
            return []
        path = outline.path(line)
        period = period_of(outline.block(line))
        return path + [period] if period else path

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
        self._outline = Outline.parse(canonical.text)
        tables = parse_markdown_tables(canonical) if raw.name.lower().endswith((".md", ".markdown", ".txt")) else []
        if raw.name.lower().endswith(".csv"):
            table = parse_csv_table(canonical)
            tables = [table] if table else []
        self._catalog = build_catalog(self._outline, tables)
        self._entity_ids = {label: self._entity(conn, version, label, aliases)
                            for label, aliases in self._catalog.aliases.items()}
        chunks = self._chunks(conn, version, document_id, canonical)
        cells, facts = 0, 0
        for table in tables:
            table_cells, table_facts = self._table(conn, version, document_id, canonical, table)
            cells += table_cells
            facts += table_facts
        facts += self._line_facts(conn, version, document_id, canonical)
        return DocumentReport(document_id=document_id, name=raw.name, sha256=digest, lines=len(canonical.lines),
                              chunks=chunks, tables=len(tables), table_cells=cells, facts=facts,
                              untyped_cells=cells - facts)

    def _chunks(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument) -> int:
        """Citable text chunks. Prose: one chunk per blank-line block (sub-sections such as
        '**1. Headline Results**' are their own blocks). Tables: one chunk per data row, whose
        searchable text carries the header row but whose span is the row itself, so a hit on one
        row of a multi-entity table is attributed to that row only."""
        lines = [line.text for line in canonical.lines]
        count, i, n = 0, 0, len(lines)

        def emit(start: int, end: int, body: str | None = None) -> None:
            nonlocal count
            label = block_label(lines[start - 1])
            span = self._span(conn, version, document, canonical, start, end, label=label)
            body = body if body is not None else canonical.span_for_lines(start, end)[2]
            conn.execute(text("INSERT INTO chunk (dataset_version_id, span_id, text, token_count) VALUES (:version, :span, :body, :tokens)"),
                         {"version": version, "span": span, "body": body, "tokens": len(body.split())})
            count += 1

        while i < n:
            if not lines[i].strip():
                i += 1
                continue
            is_table = "|" in lines[i] and i + 1 < n and _TABLE_DIVIDER.match(lines[i + 1])
            if is_table:
                header, j = lines[i].strip(), i + 2
                while j < n and lines[j].strip() and "|" in lines[j]:
                    emit(j + 1, j + 1, f"{header}\n{lines[j].strip()}")
                    j += 1
                i = j
                continue
            j = i
            while j < n and lines[j].strip() and not ("|" in lines[j] and j + 1 < n and _TABLE_DIVIDER.match(lines[j + 1])):
                j += 1
            emit(i + 1, j)
            i = j
        return count

    def _table(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument, table: ParsedTable) -> tuple[int, int]:
        span = self._span(conn, version, document, canonical, table.start_line, table.end_line)
        table_id = conn.execute(text("""INSERT INTO source_table (dataset_version_id, span_id, header_rows, n_rows, n_cols)
            VALUES (:version, :span, CAST(:headers AS jsonb), :rows, :cols) RETURNING table_id"""),
            {"version": version, "span": span, "headers": json.dumps([table.headers]), "rows": len(table.rows), "cols": len(table.headers)}).scalar_one()
        headers = [h.strip() for h in table.headers]
        rank_cols = {i: int(re.sub(r"\D", "", h)) for i, h in enumerate(headers) if re.fullmatch(r"#?\s*\d{1,3}", h)}
        is_kv = len(headers) == 2 and not rank_cols
        lowered = [h.lower().strip("* ") for h in headers]
        lc = label_column(table, self._catalog, set(rank_cols))
        date_col = date_column(table)
        event_cols = event_columns(table, lowered, date_col, lc)
        is_calendar = bool(event_cols) and date_col is not None
        caption = self._caption_period(canonical, table.start_line)
        facts = 0
        for row_index, (row, line_number) in enumerate(zip(table.rows, table.row_lines)):
            row_label = row[lc] or None
            owners = [self._catalog.owner_of(c) for c in row]
            named = [o for o in owners if o]
            # a row about one entity (comparison / screening tables), else the enclosing entity section
            row_entity = named[0] if len(set(named)) == 1 else None
            section_entity = entity_from_path(self._outline.path(line_number), self._catalog.labels())
            period = leading_period(row[lc]) if row[lc] else None
            if period is None:
                period = next((p for p in (find_period(c) for i, c in enumerate(row) if i != lc) if p), None) or caption
            line = canonical.lines[line_number - 1]
            cursor = 0
            for col_index, cell in enumerate(row):
                column = line.text.find(cell, cursor)
                column = column if column >= 0 else cursor
                cursor = column + len(cell)
                start, end, _ = canonical.span_for_text(line_number, column, column + len(cell))
                cell_span = self._span(conn, version, document, canonical, line_number, line_number, start, end)
                cell_id = conn.execute(text("""INSERT INTO table_cell (dataset_version_id, table_id, span_id, row_idx, col_idx, row_label, header_path, raw_text)
                   VALUES (:version, :table, :span, :row, :col, :label, :headers, :raw) RETURNING cell_id"""),
                    {"version": version, "table": table_id, "span": cell_span, "row": row_index, "col": col_index,
                     "label": row_label, "headers": list(table.headers[:col_index + 1]), "raw": cell}).scalar_one()
                if not cell or (col_index == lc and lowered[lc] not in ("rank", "#", "position")):
                    continue
                if is_calendar and col_index in event_cols:
                    owner = owners[col_index] or row_entity
                    ent = self._entity_ids.get(owner) if owner else None
                    d = row[date_col]
                    when = (d.strip(), find_period(d) and find_period(d)[1] or _parse_date(d), "point")
                    written = self._write_values(conn, version, document, canonical, line_number, column,
                                                 [Value(0, len(cell), cell, text=cell, unit=Unit.text, role="event")],
                                                 ent, headers[col_index], when, cell_id)
                elif col_index in rank_cols:
                    # "| **Market Cap** | AEM ($102B) | ..." : rank + the value in parentheses, owned by the cell's entity
                    owner = owners[col_index]
                    metric_label = row[lc].strip("* ")
                    ent = self._entity_ids.get(owner) if owner else None
                    rank_v = Value(0, len(cell), cell, value=float(rank_cols[col_index]), text=cell, unit=Unit.rank,
                                   role="rank")
                    inner = re.search(r"\(([^)]*)\)", cell)
                    vals = [rank_v]
                    if inner:
                        for v in cell_values(inner.group(1)):
                            v.start += inner.start(1)
                            v.end += inner.start(1)
                            vals.append(v)
                    written = self._write_values(conn, version, document, canonical, line_number, column, vals,
                                                 ent, metric_label, None, cell_id)
                else:
                    owner = owners[col_index] if len(set(named)) > 1 else None   # multi-entity row: the cell's own
                    label_for_fact = owner or row_entity or section_entity
                    ent = self._entity_ids.get(label_for_fact) if label_for_fact else None
                    metric_label = row[lc].strip("* ") if is_kv else headers[col_index]
                    vals = cell_values(cell, headers[col_index])
                    if owner and vals and all(v.role == "attribute" for v in vals):
                        continue    # an entity's own name/label cell is not a fact about it
                    written = self._write_values(conn, version, document, canonical, line_number, column, vals,
                                                 ent, metric_label, period, cell_id)
                if written:
                    facts += written
                    conn.execute(text("UPDATE table_cell SET typed = true WHERE span_id = :span"), {"span": cell_span})
        return len(table.rows) * len(table.headers), facts

    def _caption_period(self, canonical: CanonicalDocument, header_line: int):
        """Period stated in a short caption right above a table, e.g. '*(Q2 2026 data — most recent quarter)*'."""
        for n in range(header_line - 1, max(0, header_line - 3), -1):
            t = canonical.lines[n - 1].text.strip()
            if t:
                return find_period(t) if len(t) < 120 and not t.startswith("|") else None
        return None

    def _write_values(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument, line_number: int,
                      column: int, values: list, entity_id, metric_label: str, period, cell_id=None) -> int:
        """Insert facts for values found at `column` of a line; each fact's span is the value itself."""
        n = 0
        for v in values:
            start, end, _ = canonical.span_for_text(line_number, column + v.start, column + v.end)
            original = canonical.text[start:end]
            if not original.strip():
                continue
            span = self._span(conn, version, document, canonical, line_number, line_number, start, end)
            label = v.label or metric_label
            if v.role in ("actual", "estimate"):
                # "EPS (Act vs Est)" -> "EPS": actual and estimate share the metric; the role tells them apart
                label = re.sub(r"\s*\([^)]*\bvs\.?\b[^)]*\)", "", label or "").strip() or label
            p_label, p_end, p_type = period if period else (None, None, "unspecified")
            if v.role in ("trend", "count") and not period:
                p_type = "trailing" if v.role == "trend" else "unspecified"
            conn.execute(text("""INSERT INTO fact (dataset_version_id, entity_id, cell_id, span_id, metric, metric_label, value,
                    value_low, value_high, value_text, original_value, unit, unit_label, currency, scale, is_estimate,
                    basis, role, period_label, period_end, period_type, extraction_confidence)
                VALUES (:version, :entity, :cell, :span, :metric, :label, :value, :low, :high, :vtext, :original,
                        CAST(:unit AS value_unit), :ulabel, :currency, :scale, :est, CAST(:basis AS fact_basis),
                        CAST(:role AS fact_role), :plabel, :pend, CAST(:ptype AS period_type), :conf)"""),
                {"version": version, "entity": entity_id, "span": span, "cell": cell_id,
                 "metric": _metric_key(label), "label": label, "value": v.value, "low": v.low, "high": v.high,
                 "vtext": v.text, "original": original.strip(), "unit": v.unit.value,
                 "ulabel": v.extra.get("unit_label"), "currency": v.currency,
                 "scale": v.scale, "est": v.is_estimate, "basis": v.basis, "role": v.role, "plabel": p_label,
                 "pend": p_end, "ptype": p_type, "conf": 0.9 if v.role in ("actual", "estimate", "rank") else 0.8})
            n += 1
        return n

    def _line_facts(self, conn, version: UUID, document: UUID, canonical: CanonicalDocument) -> int:
        """Facts from structured non-table lines: <summary> lines ('Revenue $152.6M; EPS $0.03 vs $0.06 est (Miss)')
        and trend statements ('Payments penetration ↑ (62% → 68%)', 'from 58% to 68%', 'Beat/Met 6/8')."""
        facts = 0
        for line in canonical.lines:
            raw = line.text
            if raw.lstrip().startswith("|") or not raw.strip():
                continue
            section_entity = entity_from_path(self._outline.path(line.number), self._catalog.labels())
            # a period's summary line: '<summary>Q3 2024 — Revenue $3,368M; EPS …</summary>', or the same
            # text as a heading ('#### 3Q24 — Revenue …') in reports that don't use <details>
            if "<summary>" in raw or (raw.startswith("#") and leading_period(raw.lstrip("#"))):
                inner = re.sub(r"<[^>]+>|^#+", lambda m: " " * len(m.group(0)), raw)   # keep offsets
                period = find_period(inner)
                dash = re.search(r"\s[—–]\s", inner)
                if period and dash:
                    body_off = dash.end()
                    vals = labelled_values(inner[body_off:])
                    for v in vals:
                        v.start += body_off
                        v.end += body_off
                    ent = self._entity_ids.get(section_entity) if section_entity else None
                    facts += self._write_values(conn, version, document, canonical, line.number, 0, vals, ent,
                                                "", period)
                continue
            if not re.search(r"→|->|\bfrom\b.+\bto\b", raw):
                continue
            vals = trend_values(raw)
            if not re.search(r"↑|↓|→", raw):
                vals = [v for v in vals if v.role != "count"]
            for v in vals:
                owner = section_entity or self._entity_before(raw, v.start)
                ent = self._entity_ids.get(owner) if owner else None
                facts += self._write_values(conn, version, document, canonical, line.number, 0, [v], ent, "", None)
        return facts

    def _entity_before(self, text_: str, pos: int):
        """The last entity named before `pos` in a line that discusses several ('SHOP — ... | DSG — ...')."""
        best, best_at = None, -1
        for label, aliases in self._catalog.aliases.items():
            for term in {label, *aliases}:
                exact = term.isupper() or term == label
                for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text_[:pos],
                                     0 if exact else re.IGNORECASE):
                    if m.start() > best_at and (len(term) >= 4 or term == label):
                        best, best_at = label, m.start()
        return best

    def _entity(self, conn, version: UUID, label: str, aliases: set[str] = frozenset()) -> UUID:
        entity_id = conn.execute(text("""INSERT INTO entity (dataset_version_id, label) VALUES (:version, :label)
            ON CONFLICT (dataset_version_id, label) DO UPDATE SET label = EXCLUDED.label RETURNING entity_id"""),
                                 {"version": version, "label": label}).scalar_one()
        for alias in sorted(aliases):
            conn.execute(text("""INSERT INTO entity_alias (dataset_version_id, entity_id, alias, origin)
                VALUES (:version, :entity, :alias, 'document') ON CONFLICT DO NOTHING"""),
                         {"version": version, "entity": entity_id, "alias": alias})
        return entity_id

    def _validate_duplicates(self, conn, version: UUID) -> int:
        """duplicate_claim: the same entity, metric, stated period and basis with values that disagree
        beyond rounding. Facts without a stated period are never compared (eight quarters of revenue
        are not conflicting claims)."""
        groups = conn.execute(text("""SELECT f.entity_id, f.metric, f.period_label, f.basis,
                   array_agg(f.fact_id ORDER BY f.fact_id) ids, array_agg(f.span_id ORDER BY f.fact_id) spans,
                   array_agg(f.value ORDER BY f.fact_id) vals, array_agg(f.original_value ORDER BY f.fact_id) raws,
                   array_agg(f.scale ORDER BY f.fact_id) scales
            FROM fact f JOIN source_span s ON s.span_id = f.span_id
            WHERE f.dataset_version_id = :version AND f.period_label IS NOT NULL AND f.value IS NOT NULL
              AND f.role = 'actual' AND f.entity_id IS NOT NULL
            GROUP BY f.entity_id, f.metric, f.period_label, f.basis
            HAVING COUNT(DISTINCT f.value) > 1 AND COUNT(DISTINCT (s.document_id, s.line_start)) > 1"""),
                              {"version": version}).mappings()
        count = 0
        for g in groups:
            tolerance = max(_precision_step(raw, scale) for raw, scale in zip(g["raws"], g["scales"])) / 2
            if max(g["vals"]) - min(g["vals"]) <= tolerance + 1e-9:
                continue   # same number written at different precisions ($153M vs $152.6M)
            conn.execute(text("""INSERT INTO validation_finding (dataset_version_id, rule, rule_version, severity, explanation,
                                     fact_ids, span_ids, expected, observed)
              VALUES (:version, 'duplicate_claim', '0.2.0', 'medium', :why, :facts, :spans, :expected, :observed)"""),
                         {"version": version, "facts": g["ids"], "spans": g["spans"],
                          "why": f"{g['metric']} for {g['period_label']} is stated with different values: "
                                 + ", ".join(g["raws"]),
                          "expected": "one value per entity, metric, period and basis",
                          "observed": " vs ".join(g["raws"])})
            count += 1
        return count


_LABEL_HEADERS = {"ticker", "symbol", "company", "name", "issuer", "entity", "field", "item", "metric", "measure",
                  "driver", "category", "segment", "line item", "period", "quarter", "fiscal quarter", "year",
                  "date", "rank", "#", "position"}


def label_column(table: ParsedTable, catalog, exclude: set[int] = frozenset()) -> int:
    """The column that names what each row is about. Usually the first, but not when columns are
    reordered: prefer a column of entity labels, then periods, then short distinct text labels;
    a generic label header ('Ticker', 'Field', 'Metric', 'Date') breaks ties."""
    best, best_score = 0, -1.0
    n = max(1, len(table.rows))
    for i, header in enumerate(table.headers):
        if i in exclude:
            continue
        cells = [r[i].strip().strip("*").strip() for r in table.rows]
        exact = sum(1 for c in cells if c in catalog.aliases) / n
        owners = sum(1 for c in cells if catalog.owner_of(c)) / n
        periods = sum(1 for c in cells if leading_period(c)) / n
        texty = sum(1 for c in cells if c and len(c) <= 40 and not re.match(r"[~≈+\-−]?\s*(?:C\$|US\$|\$)?\d", c)) / n
        distinct = len(set(cells)) / n
        score = (3 * exact + 2 * owners + 2 * periods + texty) * distinct \
            + (0.5 if header.strip("* ").lower() in _LABEL_HEADERS else 0) + (0.25 if i == 0 else 0)
        if score > best_score:
            best, best_score = i, score
    return best


_EVENT_HEADERS = ("event", "catalyst", "milestone", "description")


def date_column(table: ParsedTable) -> Optional[int]:
    """The column that dates each row: most of its cells are a date or a period, however the header
    is worded ('Date', 'Expected When', 'Timing')."""
    for i in range(len(table.headers)):
        dated = sum(1 for r in table.rows if _parse_date(r[i]) or find_period(r[i]) or _vague_date(r[i]))
        if dated >= max(2, 0.6 * len(table.rows)):
            return i
    return None


_MONTH_WORD = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{0,2},?\s*20\d{2}\b",
                         re.IGNORECASE)


def _vague_date(cell: str) -> bool:
    """'Late Nov 2026', 'Mid-2027', 'H2 2026 (targeted)': dated enough to head a row."""
    return bool(_MONTH_WORD.search(cell or ""))


def event_columns(table: ParsedTable, lowered: list[str], date_col: Optional[int], label_col: int) -> set[int]:
    """In a dated table (news, catalysts), the columns that say what happened: those named as such,
    else every phrase column, so renamed headers ('Headline', 'Effect') are still read as events.
    A dated table that reports values (insider transactions) is left to the normal table path."""
    named = {i for i, h in enumerate(lowered) if h in _EVENT_HEADERS}
    if named or date_col is None:
        return named
    others = [i for i in range(len(lowered)) if i not in (date_col, label_col)]
    if not others or any(_numeric_column(table, i) for i in others):
        return set()
    return {i for i in others if sum(len(r[i].split()) for r in table.rows) >= 3 * len(table.rows)}


def _numeric_column(table: ParsedTable, i: int) -> bool:
    """Most of the column's cells lead with a number or amount ('$4.2M', '12,500', '+3.1%')."""
    hits = sum(1 for r in table.rows if re.match(r"[~≈+\-−(]?\s*(?:C\$|US\$|\$)?\d", r[i].strip()))
    return hits >= max(2, 0.6 * len(table.rows))


def _precision_step(original: str, scale: float) -> float:
    """Smallest increment the written value can express: '$152.6M' -> 0.1 * 1e6."""
    import re as _re
    m = _re.search(r"\d[\d,]*(?:\.(\d+))?", original or "")
    decimals = len(m.group(1) or "") if m else 0
    return (10 ** -decimals) * (scale or 1.0)


def _metric_key(label: str) -> str:
    """Dataset-scoped metric key from a label as written: 'Rev YoY%' -> 'rev_yoy_pct'."""
    key = (label or "value").strip("* ").lower().replace("%", " pct").replace("&", " and ")
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return key or "value"


def _parse_date(text_: str):
    """'Nov 5, 2026' -> date(2026, 11, 5); None for vague dates ('Late Nov 2026', 'Q4 2026')."""
    from datetime import datetime as _dt
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return _dt.strptime(text_.strip(), fmt).date()
        except ValueError:
            continue
    return None
