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


def test_every_expected_finding_is_produced():
    from tests.ingest.acceptance import findings
    run(corpus="fixture")
    found, missed, _ = findings(corpus="fixture")
    assert len(found) == 5 and not missed, [m["finding_key"] for m in missed]


def test_known_corpus_findings_are_the_documented_contradictions():
    """The three full reports: every finding must be one of the reviewed, genuine contradictions."""
    from tests.ingest.acceptance import findings
    run(corpus="known")
    _, _, produced = findings(corpus="known")
    expected_fragments = [
        "Gross Margin (Latest Q)", "GIB.A is stated as 2/8", "GIB.A is stated as 4/8", "ranking 'Market Cap'",
        "column 'Revenue'", "Payments penetration", "column 'Mkt Cap (USD)'", "Revenue Growth (YoY, Latest Q)",
        "IVN is stated as 3/8", "FCFE",
    ]
    unexplained = [p for p in produced if not any(frag in p for frag in expected_fragments)]
    assert not unexplained, unexplained
    assert all(any(frag in p for p in produced) for frag in expected_fragments)
