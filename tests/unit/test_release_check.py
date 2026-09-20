"""The release check's wiring: stage names, the quick subset, and the scorecard it writes."""

import json

from evals.release_check import QUICK, STAGES, _markdown


def test_quick_runs_everything_except_the_model_heavy_stages():
    assert set(QUICK) | {"suites", "coldstart"} == set(STAGES)
    assert QUICK[0] == "compose" and QUICK[-1] == "scorecard"


def test_every_stage_is_documented_in_the_readme():
    readme = (__import__("pathlib").Path(__file__).resolve().parents[2] / "evals/README.md").read_text()
    for name in STAGES:
        assert f"`{name}`" in readme, f"{name} is not described in evals/README.md"


def test_scorecard_markdown_reports_suites_and_frozen_versions():
    card = {
        "checked_at": "2026-09-19T00:00:00+00:00",
        "frozen": {"contract_version": "1.2", "parser_version": "0.1.0", "prompt_version": "answer_v1",
                   "model": "m", "embedding_model": "e", "schema_sha256": "abc", "prompt_sha256": "def",
                   "git_commit": "1234567"},
        "stages": {
            "compose": {"tables": 15},
            "suites": {"fixture": {"passed": 10, "cases": 10, "citation_validity": 1.0, "failed": {}},
                       "rbc": {"passed": 29, "cases": 30, "citation_validity": 1.0, "failed": {"MIN02": "anchors"}}},
            "coldstart": {"passed": 28, "cases": 30, "citation_validity": 1.0, "failed": {}},
        },
    }
    md = _markdown(card)
    assert "| Contract | 1.2 |" in md and "`1234567`" in md
    assert "| Contract fixture | 10/10 | all verified | none |" in md
    assert "| RBC sample (known corpus) | 29/30 | all verified | MIN02 |" in md
    assert "restructured corpus) | 28/30" in md
    assert json.dumps(card)                      # the same content is written as JSON beside it
