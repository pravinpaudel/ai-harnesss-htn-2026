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
