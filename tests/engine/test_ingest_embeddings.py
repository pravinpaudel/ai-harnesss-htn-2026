"""Ingest writes chunk embeddings after raw evidence commits; embedding failures never block ingest."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from contracts.models import DatasetStatus, IngestRequest, SourceType
from app.ingest.embeddings import NoopEmbeddingProvider
from app.ingest.service import FileIngestService
from app.ingest.storage import ImmutableRawStorage
from app.llm.embeddings import HashingEmbedder
from tests.engine.conftest import db_url
from tests.engine.seed_fixture import reset_schema

pytestmark = pytest.mark.db


class HashingProvider:
    model = "hashing-test"

    def embed(self, texts):
        h = HashingEmbedder()
        return [h.embed_query(t) for t in texts]


class BrokenProvider:
    model = "broken"

    def embed(self, texts):
        raise RuntimeError("provider down")


@pytest.fixture(scope="module")
def admin():
    url = db_url()
    try:
        create_engine(url).connect().close()
    except OperationalError:
        pytest.skip("test database not running")
    reset_schema(url)
    return create_engine(url)


def _ingest(admin, tmp_path, name, embedder):
    svc = FileIngestService(admin, ImmutableRawStorage(tmp_path / "raw"), "test", embedder=embedder)
    svc.embed_batch = 16   # exercise batching
    return svc.run_sync(IngestRequest(source=SourceType.file, path="contracts/fixture/raw", dataset_name=name))


def _counts(admin, version):
    with admin.connect() as c:
        return c.execute(text("SELECT count(*), count(embedding) FROM chunk WHERE dataset_version_id = :v"),
                         {"v": str(version)}).one()


def test_chunks_are_embedded(admin, tmp_path):
    rep = _ingest(admin, tmp_path, "emb-ok", HashingProvider())
    total, embedded = _counts(admin, rep.dataset_version_id)
    assert rep.status == DatasetStatus.ready and total > 16 and embedded == total and not rep.warnings


def test_provider_failure_keeps_evidence_and_warns(admin, tmp_path):
    rep = _ingest(admin, tmp_path, "emb-broken", BrokenProvider())
    total, embedded = _counts(admin, rep.dataset_version_id)
    assert rep.status == DatasetStatus.ready and total > 0 and embedded == 0
    assert "provider down" in rep.warnings[0]


def test_noop_provider_says_semantic_search_is_off(admin, tmp_path):
    rep = _ingest(admin, tmp_path, "emb-none", NoopEmbeddingProvider())
    assert "semantic search disabled" in rep.warnings[0]


def test_spans_carry_heading_path_and_tables_chunk_per_row(admin, tmp_path):
    rep = _ingest(admin, tmp_path, "structure", NoopEmbeddingProvider())
    v = str(rep.dataset_version_id)
    with admin.connect() as c:
        rows = c.execute(text("""SELECT s.line_start, s.heading_path, k.text, s.exact_text FROM chunk k
            JOIN source_span s ON s.span_id = k.span_id JOIN document d ON d.document_id = s.document_id
            WHERE k.dataset_version_id = :v AND d.name = 'mining-excerpt.md'"""), {"v": v}).all()
        empty_paths = c.execute(text("SELECT count(*) FROM source_span WHERE dataset_version_id = :v "
                                     "AND cardinality(heading_path) = 0 AND line_start > 3"), {"v": v}).scalar()
    by_line = {r.line_start: r for r in rows}
    headline = by_line[97]
    assert headline.heading_path[-3:] == ["IVN — Ivanhoe Mines Ltd", "Q2 2026", "1. Headline Results"]
    row = by_line[132]                                   # screening row: its own chunk, header kept in the text
    assert row.exact_text.startswith("| **EPS Beat Rate (8Q)** |") and row.text.startswith("| Metric |")
    assert empty_paths == 0                              # every span below the title has a heading path
