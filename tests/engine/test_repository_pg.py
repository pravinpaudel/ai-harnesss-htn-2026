"""PgEvidenceRepository against the contract schema (needs tests/engine/compose.test.yml running).

Two fixture versions are seeded; v2 is newer, so 'latest' must resolve to it and v1 rows must
never leak into v2 queries. The engine role must not be able to write evidence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from contracts.models import FactFilter, FindingRule, Role
from app.audit.recorder import PgRecorder
from app.llm.client import ScriptedLLM
from app.reasoning.engine import ResearchEngine
from app.reasoning.verifier import verify_span
from app.retrieval.repository import PgEvidenceRepository
from tests.engine.conftest import db_url, pick, submit
from tests.engine.fixture_data import load
from tests.engine.seed_fixture import reset_schema, seed

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def pg():
    url = db_url()
    try:
        create_engine(url).connect().close()
    except OperationalError:
        pytest.skip("test database not running: docker compose -f tests/engine/compose.test.yml up -d")
    reset_schema(url)
    v1, v2 = load("v1"), load("v2")
    seed(url, v1, 1)
    seed(url, v2, 2)
    engine_url = url.replace("postgres:postgres@", "htn_engine:htn_engine@")
    return PgEvidenceRepository(engine_url), v1, v2


def test_latest_is_newest_ready_version(pg):
    repo, v1, v2 = pg
    assert repo.resolve_version("latest") == v2.dataset_version_id
    assert repo.resolve_version("fixture", str(v1.dataset_version_id)) == v1.dataset_version_id


def test_every_fixture_fact_is_retrievable_with_exact_span(pg):
    repo, _, v2 = pg
    facts = repo.find_facts(FactFilter(dataset_version_id=v2.dataset_version_id, limit=500))
    assert len(facts) == len(v2.facts)
    by_id = {f.fact_id: f for f in facts}
    for rec in v2.facts:
        got = by_id[rec.fact_id]
        assert got.span.exact_text == rec.original_value
        assert got.value.value == rec.value and got.role.value == rec.role
        assert verify_span(repo, v2.dataset_version_id, got.span)[0]


def test_version_isolation(pg):
    repo, v1, v2 = pg
    facts = repo.find_facts(FactFilter(dataset_version_id=v2.dataset_version_id, limit=500))
    assert all(f.dataset_version_id == v2.dataset_version_id for f in facts)
    assert not {f.fact_id for f in facts} & {f.fact_id for f in v1.facts}


def test_filters_and_aliases(pg):
    repo, _, v2 = pg
    v = v2.dataset_version_id
    ivn = repo.resolve_entity(v, "Ivanhoe")[0]
    assert ivn.label == "IVN" and "ivanhoe mines" in ivn.aliases
    got = repo.find_facts(FactFilter(dataset_version_id=v, entity_ids=[ivn.entity_id], metric_text="revenue",
                                     roles=[Role.actual], period_labels=["Q2 2026"]))
    assert {f.value.original_value for f in got} >= {"$152.6M"}
    assert repo.resolve_entity(v, "Cenovus") == []


def test_lexical_search_and_findings(pg):
    repo, _, v2 = pg
    v = v2.dataset_version_id
    hits = repo.search_lexical(v, "payable sales logistical constraints")
    assert hits and any("logistical" in h.text for h in hits)
    fnd = repo.list_findings(v, rules=[FindingRule.count_claim])
    assert len(fnd) == 1 and fnd[0].observed == "3/8" and len(fnd[0].spans) == 9
    prof = repo.profile(v)
    assert prof.findings_by_rule[FindingRule.unit_currency_mix] == 2 and len(prof.entities) == 12


def test_engine_role_cannot_write_evidence(pg):
    repo, _, v2 = pg
    with pytest.raises((ProgrammingError, DBAPIError)):
        with repo.engine.begin() as c:
            c.execute(text("UPDATE fact SET value = 0 WHERE dataset_version_id = :v"),
                      {"v": str(v2.dataset_version_id)})


def test_engine_run_is_audited_in_postgres(pg, settings):
    repo, _, v2 = pg
    llm = ScriptedLLM([
        [("find_facts", {"entity": "IVN", "metric": "revenue", "period": "Q2 2026", "role": "actual"})],
        [submit("answered", "Revenue was $152.6M [1].", [(1, pick("find_facts", source="mining-excerpt.md:95"))])],
    ])
    ans = ResearchEngine(repo, llm, settings, PgRecorder(repo.engine)).ask("What was Ivanhoe's revenue in Q2 2026?")
    assert ans.status.value == "answered"
    with repo.engine.connect() as c:
        status = c.execute(text("SELECT status::text FROM answer_run WHERE run_id = :r"), {"r": str(ans.run_id)}).scalar()
        n = c.execute(text("SELECT count(*) FROM tool_event WHERE run_id = :r"), {"r": str(ans.run_id)}).scalar()
    assert status == "answered" and n >= 4
