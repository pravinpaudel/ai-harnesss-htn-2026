"""Checkpoint 4: the release-candidate check, from a clean install to a signed scorecard.

    uv run python -m evals.release_check              # every stage (~45 min, calls the model)
    uv run python -m evals.release_check --quick      # everything but the two full suites and cold start
    uv run python -m evals.release_check --stages compose,api,scorecard

Runs a clean Docker Compose install in its own project (`htn-release`), so it has its own volumes and
ports and never touches a working stack. Each stage records what it measured; the last one freezes
the versions in play and writes docs/scorecard.md next to release-scorecard.json.

Stages: compose, db_init, mcp, corpus, suites, coldstart, api, trace, scorecard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "htn-release"


def _docker() -> str:
    """Docker Desktop is often not on PATH in a non-login shell."""
    from shutil import which

    found = which("docker") or next((p for p in ("/usr/local/bin/docker",
                                                 str(Path.home() / ".docker/bin/docker"),
                                                 "/Applications/Docker.app/Contents/Resources/bin/docker")
                                     if Path(p).exists()), None)
    if not found:
        raise Failed("docker is not installed or not on PATH")
    return found


COMPOSE = [_docker(), "compose", "-p", PROJECT, "-f", "docker-compose.yml", "-f", "docker-compose.release.yml"]
OWNER_URL = "postgresql+psycopg://harness:harness@localhost:55496/harness"
ENGINE_URL = "postgresql+psycopg://htn_engine:htn_engine@localhost:55496/harness"
API = "http://localhost:8010"
CORPUS = ("canadian-financials-research.md", "canadian-mining-research.md", "canadian-technology-research.md")


class Failed(RuntimeError):
    """A stage did not meet its bar; the check stops and the scorecard is not written."""


def sh(*args: str, env: dict | None = None, timeout: int = 1800, check: bool = True) -> str:
    # docker's credential helper sits next to the binary, which a non-login shell may not have on PATH
    path = os.pathsep.join([str(Path(COMPOSE[0]).parent), os.environ.get("PATH", "")])
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "PATH": path, **(env or {})}, cwd=ROOT)
    if check and out.returncode:
        raise Failed(f"{' '.join(args[:3])}… exited {out.returncode}\n{out.stderr[-1500:]}")
    return out.stdout


def htn(*args: str, **kw) -> str:
    return sh("uv", "run", "htn", *args,
              env={"HARNESS_DATABASE_URL": OWNER_URL, "DATABASE_URL_ENGINE": ENGINE_URL}, **kw)


def get(path: str, timeout: int = 30) -> Any:
    with urllib.request.urlopen(f"{API}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def post(path: str, body: dict, timeout: int = 180) -> Any:
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for(what: str, probe: Callable[[], bool], seconds: int = 240) -> None:
    for _ in range(seconds):
        try:
            if probe():
                return
        except Exception:  # noqa: BLE001 - still starting
            pass
        time.sleep(1)
    raise Failed(f"{what} did not come up within {seconds}s")


# ------------------------------------------------------------------------------------------ stages --

def stage_compose(state: dict) -> dict:
    """A clean install: fresh volumes, schema and the read-only engine role applied by compose itself."""
    sh(*COMPOSE, "down", "-v", "--remove-orphans", check=False, timeout=300)
    sh(*COMPOSE, "up", "-d", "--build", "postgres", "api", timeout=1800)
    from sqlalchemy import create_engine, text

    wait_for("postgres", lambda: create_engine(OWNER_URL).connect().close() is None)
    with create_engine(OWNER_URL).connect() as c:
        tables = c.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")).scalar()
    with create_engine(ENGINE_URL).connect() as c:
        reads = c.execute(text("SELECT has_table_privilege('htn_engine','fact','SELECT')")).scalar()
        writes = c.execute(text("SELECT has_table_privilege('htn_engine','fact','INSERT')")).scalar()
        audits = c.execute(text("SELECT has_table_privilege('htn_engine','answer_run','INSERT')")).scalar()
    if tables < 15 or not reads or writes or not audits:
        raise Failed(f"clean install wrong: tables={tables} read={reads} write={writes} audit_write={audits}")
    return {"tables": tables, "engine_role": "reads evidence, writes only its own audit rows"}


def stage_db_init(state: dict) -> dict:
    """`htn db init` is safe on a database compose already prepared, and applies the grants again."""
    first = json.loads(htn("db", "init"))
    second = json.loads(htn("db", "init"))
    if first["schema_created"] or not (first["grants_applied"] and second["grants_applied"]):
        raise Failed(f"db init not idempotent on a prepared database: {first} then {second}")
    return {"schema_created": first["schema_created"], "idempotent": True}


def stage_mcp(state: dict) -> dict:
    """The real MCP corpus ingests, records what the server offered, and re-runs without a new version."""
    from app.settings import settings                      # the URL usually comes from .env, not the shell

    if not settings.mcp_url:
        return {"skipped": "HARNESS_MCP_URL is not configured"}
    first = json.loads(htn("refresh", timeout=900))
    again = json.loads(htn("refresh", timeout=900))
    caps = first.get("mcp_capabilities") or {}
    if first["reused"] or not again["reused"]:
        raise Failed(f"refresh should create then reuse: {first['reused']} then {again['reused']}")
    if not first["documents"] or not caps.get("tools"):
        raise Failed("MCP ingest recorded no documents or no capabilities")
    state["mcp_dataset_version"] = first["dataset_version_id"]
    return {"documents": len(first["documents"]), "findings": first["findings"],
            "tools": [t.get("name") for t in caps["tools"]], "tool_called": caps.get("tool_called"),
            "arguments": caps.get("arguments"), "second_run_reused_version": again["reused"]}


def stage_corpus(state: dict) -> dict:
    """The known corpus ingests, and every hand-checked expected fact is found in the store."""
    import csv

    from sqlalchemy import create_engine, text

    src = ROOT / ".release-corpus"
    src.mkdir(exist_ok=True)
    for name in CORPUS:
        (src / name).write_bytes((ROOT / name).read_bytes())
    report = json.loads(htn("ingest", str(src), "--dataset-name", "known-corpus", timeout=1800))
    version = report["dataset_version_id"]
    state["known_corpus_version"] = version
    expected = list(csv.DictReader(open(ROOT / "contracts/known-corpus/expected_facts.csv", encoding="utf-8")))
    with create_engine(OWNER_URL).connect() as c:
        got = c.execute(text("""SELECT d.name, s.line_start, coalesce(e.label,'') AS label, f.role::text AS role,
                   f.value, f.value_low, f.value_high, f.value_text FROM fact f
            JOIN source_span s ON s.span_id = f.span_id JOIN document d ON d.document_id = s.document_id
            LEFT JOIN entity e ON e.entity_id = f.entity_id WHERE f.dataset_version_id = :v"""), {"v": version}).all()
    from tests.ingest.acceptance import _close, _f

    found = sum(1 for e in expected if any(
        g.name == e["document_name"] and g.line_start == int(e["line_start"]) and g.label == e["entity_label"]
        and g.role == e["role"] and _close(g.value, _f(e["value"])) and _close(g.value_low, _f(e["value_low"]))
        and _close(g.value_high, _f(e["value_high"]))
        and (not e["value_text"] or e["value"] != "" or (g.value_text or "") == e["value_text"]) for g in got))
    if found != len(expected):
        raise Failed(f"known-corpus acceptance: {found}/{len(expected)} expected facts")
    return {"documents": len(report["documents"]), "facts": len(got), "findings": report["findings"],
            "expected_facts": f"{found}/{len(expected)}"}


def _suite(questions: str, dataset: str, label: str) -> dict:
    out = htn("eval", "--questions", questions, "--dataset", dataset, "--json", timeout=3600)
    rep = json.loads(out)
    cases = rep["cases"]
    passed = sum(c["passed"] for c in cases)
    return {"suite": label, "passed": passed, "cases": len(cases),
            "citation_validity": rep["citation_validity"],
            "failed": {c["case_id"]: (c.get("detail") or "")[:120] for c in cases if not c["passed"]},
            "run_ids": [c["run_id"] for c in cases if c["run_id"]]}


def stage_suites(state: dict) -> dict:
    """Both evaluation suites on the freshly installed stack."""
    fixture_report = json.loads(htn("ingest", "contracts/fixture/raw", "--dataset-name", "fixture", timeout=900))
    fixture = _suite("contracts/fixture/questions.json", "fixture", "contract fixture")
    rbc = _suite("evals/rbc_sample.json", "known-corpus", "rbc sample")
    state["trace_run_id"] = rbc["run_ids"][0]
    if fixture["citation_validity"] < 1.0 or rbc["citation_validity"] < 1.0:
        raise Failed("a displayed citation did not verify against the source snapshot")
    return {"fixture": {k: v for k, v in fixture.items() if k != "run_ids"},
            "rbc": {k: v for k, v in rbc.items() if k != "run_ids"},
            "fixture_documents": len(fixture_report["documents"])}


def stage_coldstart(state: dict) -> dict:
    """Phase 2 rehearsal in miniature: the combined variant, ingested and answered with no code change."""
    from evals.variants import build

    out = ROOT / ".release-variants"
    info = build("combined", out)
    htn("ingest", str(out / "combined" / "docs"), "--dataset-name", "coldstart", timeout=1800)
    suite = _suite(str(out / "combined" / "rbc_sample.json"), "coldstart", "rbc sample on a restructured corpus")
    return {"variant": info["steps"], **{k: v for k, v in suite.items() if k != "run_ids"}}


def stage_api(state: dict) -> dict:
    """The HTTP surface: health, a query answered through the read-only engine role, and the audit routes."""
    wait_for("api", lambda: get("/healthz")["status"] == "ok")
    question = "What was Ivanhoe Mines' revenue in Q2 2026?"
    answer = post("/v1/queries?dataset=known-corpus", {"question": question})
    if not answer["citations"] or not all(c["verified"] for c in answer["citations"]):
        raise Failed("API answer came back without verified citations")
    run = get(f"/v1/runs/{answer['run_id']}")
    conflicts = get("/v1/conflicts?dataset=known-corpus")
    profile = get("/v1/datasets/known-corpus")
    state["api_run_id"] = answer["run_id"]
    return {"healthz": "ok", "status": answer["status"], "citations": len(answer["citations"]),
            "tool_events_recorded": len(run["events"]),
            "conflicts": len(conflicts), "entities": len(profile["entities"]),
            "table_coverage": round(profile["table_coverage"], 3)}


def stage_trace(state: dict) -> dict:
    """Every answer can be replayed from the audit trail, by run id."""
    run_id = state.get("trace_run_id") or state.get("api_run_id")
    if not run_id:
        return {"skipped": "no run to replay (suites and api stages were not run)"}
    text_out = htn("trace", run_id)
    events = json.loads(htn("trace", run_id, "--json"))["events"]
    if not events or "policy" not in text_out:
        raise Failed(f"trace for {run_id} came back without steps")
    return {"run_id": run_id, "steps": len(events),
            "kinds": sorted({e["kind"] for e in events})}


def stage_scorecard(state: dict) -> dict:
    """Freeze the versions this release ran with, and write the scorecard."""
    from app.settings import engine_settings, settings

    s = engine_settings()
    schema = (ROOT / "contracts/schema.sql").read_bytes()
    prompt = (ROOT / f"app/reasoning/prompts/{s.htn_prompt_version}.md").read_bytes()
    frozen = {
        "contract_version": json.loads((ROOT / "evals/rbc_sample.json").read_text())["contract_version"],
        "parser_version": settings.parser_version,
        "prompt_version": s.htn_prompt_version,
        "model": s.htn_model,
        "embedding_model": s.htn_embedding_model,
        "schema_sha256": hashlib.sha256(schema).hexdigest()[:16],
        "prompt_sha256": hashlib.sha256(prompt).hexdigest()[:16],
        "git_commit": sh("git", "rev-parse", "--short", "HEAD").strip(),
    }
    card = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "frozen": frozen,
            "stages": state["results"]}
    (ROOT / "release-scorecard.json").write_text(json.dumps(card, indent=2) + "\n")
    (ROOT / "docs/scorecard.md").write_text(_markdown(card))
    return frozen


def _markdown(card: dict) -> str:
    r = card["stages"]
    f = card["frozen"]
    rows = []

    def suite_row(name: str, got: dict | None) -> None:
        if got:
            rows.append(f"| {name} | {got['passed']}/{got['cases']} | "
                        f"{'all verified' if got['citation_validity'] >= 1 else 'UNVERIFIED CITATIONS'} | "
                        f"{', '.join(got['failed']) or 'none'} |")

    suite_row("Contract fixture", (r.get("suites") or {}).get("fixture"))
    suite_row("RBC sample (known corpus)", (r.get("suites") or {}).get("rbc"))
    cold = r.get("coldstart")
    if cold and "passed" in cold:
        rows.append(f"| RBC sample (restructured corpus) | {cold['passed']}/{cold['cases']} | "
                    f"{'all verified' if cold['citation_validity'] >= 1 else 'UNVERIFIED CITATIONS'} | "
                    f"{', '.join(cold['failed']) or 'none'} |")
    lines = [
        "# Release scorecard", "",
        f"Checked {card['checked_at']} from a clean Docker Compose install (`evals/release_check.py`).", "",
        "## Frozen for this release", "",
        "| What | Version |", "|---|---|",
        f"| Contract | {f['contract_version']} |", f"| Parser | {f['parser_version']} |",
        f"| Prompt | {f['prompt_version']} (`{f['prompt_sha256']}`) |",
        f"| Schema | `{f['schema_sha256']}` |", f"| Model | {f['model']} |",
        f"| Embeddings | {f['embedding_model']} |", f"| Commit | `{f['git_commit']}` |", "",
        "## Evaluation", "",
        "| Suite | Passed | Citations | Failures |", "|---|---|---|---|", *rows, "",
        "## Install and interfaces", "",
    ]
    for stage, label in (("compose", "Clean install"), ("db_init", "`htn db init`"), ("mcp", "MCP ingest"),
                         ("corpus", "Known corpus"), ("api", "HTTP API"), ("trace", "Trace replay")):
        got = r.get(stage)
        if got:
            lines.append(f"- **{label}:** " + ", ".join(f"{k} {v}" for k, v in got.items()) + ".")
    return "\n".join(lines) + "\n"


STAGES: dict[str, Callable[[dict], dict]] = {
    "compose": stage_compose, "db_init": stage_db_init, "mcp": stage_mcp, "corpus": stage_corpus,
    "suites": stage_suites, "coldstart": stage_coldstart, "api": stage_api, "trace": stage_trace,
    "scorecard": stage_scorecard,
}
QUICK = [s for s in STAGES if s not in ("suites", "coldstart")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", help="comma-separated subset, in order")
    ap.add_argument("--quick", action="store_true", help="skip the two full suites and the cold start")
    ap.add_argument("--keep", action="store_true", help="leave the release stack running afterwards")
    a = ap.parse_args()
    names = a.stages.split(",") if a.stages else (QUICK if a.quick else list(STAGES))
    unknown = [n for n in names if n not in STAGES]
    if unknown:
        raise SystemExit(f"unknown stage(s): {', '.join(unknown)}; choose from {', '.join(STAGES)}")

    state: dict = {"results": {}}
    try:
        for name in names:
            started = time.monotonic()
            print(f"--- {name}", flush=True)
            result = STAGES[name](state)
            result["seconds"] = round(time.monotonic() - started)
            state["results"][name] = result
            print(json.dumps(result, indent=1), flush=True)
    except Failed as exc:
        print(f"\nFAILED at {name}: {exc}")
        return 1
    finally:
        if not a.keep and "compose" in names:
            sh(*COMPOSE, "down", "-v", "--remove-orphans", check=False, timeout=300)
    print("\nrelease check passed" + (" (quick)" if a.quick else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
