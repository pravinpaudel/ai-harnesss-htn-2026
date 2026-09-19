"""Ingest must reproduce every hand-checked expected fact (contracts/README.md acceptance test)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from tests.engine.conftest import db_url
from tests.ingest.acceptance import run

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def _db():
    try:
        create_engine(db_url()).connect().close()
    except OperationalError:
        pytest.skip("test database not running")


@pytest.mark.parametrize("corpus,expected", [("fixture", 143), ("known", 73)])
def test_every_expected_fact_is_extracted(corpus, expected):
    found, missed, _ = run(corpus=corpus)
    assert len(found) + len(missed) == expected
    assert not missed, [m["fact_key"] for m in missed]
