"""`htn` command line: ingest (Developer A) and research engine (Developer B)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional
from uuid import UUID

import typer
from rich.console import Console

from app.cli import render
from app.ingest.service import get_ingest_service
from app.settings import engine_settings, settings
from contracts.models import IngestRequest, SourceType

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Evidence-first finance research harness.")
dataset_app = typer.Typer(no_args_is_help=True, help="Inspect datasets.")
app.add_typer(dataset_app, name="dataset")
console = Console()
err = Console(stderr=True)


def _repo():
    from app.retrieval.repository import PgEvidenceRepository

    return PgEvidenceRepository(engine_settings().database_url_engine)


def _engine(audit: bool = True):
    from app.audit.recorder import MemoryRecorder, PgRecorder
    from app.llm.client import OpenAIResponsesClient
    from app.reasoning.engine import ResearchEngine

    s = engine_settings()
    if not s.htn_model:
        err.print("[red]HARNESS_OPENAI_MODEL is not set.[/] Set it in the environment or .env.")
        raise typer.Exit(2)
    if not s.openai_api_key:
        err.print("[red]OPENAI_API_KEY is not set.[/]")
        raise typer.Exit(2)
    repo = _repo()
    llm = OpenAIResponsesClient(s.htn_model, api_key=s.openai_api_key.get_secret_value(),
                                timeout=s.htn_llm_timeout_s)
    recorder = PgRecorder(repo.engine) if audit else MemoryRecorder()
    return ResearchEngine(repo, llm, s, recorder)


def _version(dataset: str, version: str):
    try:
        return _repo().version_info(dataset, version)
    except LookupError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1)


def _default_dataset() -> str:
    return settings.mcp_dataset_name


def _refresh() -> None:
    if not settings.mcp_url:
        raise typer.BadParameter("HARNESS_MCP_URL must be configured")
    report = get_ingest_service().run_sync(
        IngestRequest(
            source=SourceType.mcp,
            mcp_tool=settings.mcp_financial_data_tool,
            dataset_name=settings.mcp_dataset_name,
        )
    )
    typer.echo(report.model_dump_json(indent=2))


@app.command(name="version")
def version_cmd() -> None:
    """Print the current platform version."""
    typer.echo("htn 0.1.0")


@app.command()
def ingest(path: str, dataset_name: str = typer.Option(..., "--dataset-name")) -> None:
    """Ingest a local file or directory into a new immutable dataset version."""
    report = get_ingest_service().run_sync(
        IngestRequest(source=SourceType.file, path=path, dataset_name=dataset_name)
    )
    typer.echo(report.model_dump_json(indent=2))


@app.command()
def refresh() -> None:
    """Fetch the complete MCP corpus and create a version only when it changed."""
    _refresh()


@app.command(name="ingest-mcp", hidden=True)
def ingest_mcp() -> None:
    """Deprecated alias for ``htn refresh``."""
    _refresh()


@app.command()
def ask(
    question: str = typer.Argument(..., help="The research question"),
    dataset: str = typer.Option(_default_dataset(), help="Dataset name; defaults to the refreshed MCP corpus"),
    version: str = typer.Option("latest", help="Dataset version id, or 'latest'"),
    session: Optional[str] = typer.Option(None, help="Session id for audit grouping"),
    as_json: bool = typer.Option(False, "--json", help="Print the AnswerResponse JSON only"),
    audit: bool = typer.Option(True, help="Write answer_run/tool_event rows"),
) -> None:
    """Answer a question from one dataset version, with verified citations."""
    try:
        _version(dataset, version)
    except LookupError as e:
        err.print(f"[red]{e}[/]")
        if dataset == settings.mcp_dataset_name:
            err.print("Run [bold]htn refresh[/] to fetch the MCP corpus before asking questions.")
        raise typer.Exit(1)
    eng = _engine(audit)
    resp = eng.ask(question, dataset=dataset, version=version, session_id=session)
    if as_json:
        sys.stdout.write(resp.model_dump_json(exclude_none=True) + "\n")
    else:
        render.answer(console, resp)


@app.command()
def trace(run_id: UUID, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show every step of an answer run: routing, tool calls, LLM turns, policy decisions."""
    from app.audit.trace import load_trace, render as render_trace

    try:
        run, events = load_trace(_repo().engine, run_id)
    except LookupError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    if as_json:
        sys.stdout.write(json.dumps({"run": run, "events": events}, default=str, indent=1) + "\n")
    else:
        render_trace(console, run, events)


@app.command()
def conflicts(
    dataset: str = typer.Option(_default_dataset()),
    version: str = typer.Option("latest"),
    fmt: str = typer.Option("markdown", "--format", help="markdown | json"),
) -> None:
    """List the dataset's open contradictions and data-quality findings."""
    info = _version(dataset, version)
    found = _repo().list_findings(info.dataset_version_id)
    if fmt == "json":
        sys.stdout.write(json.dumps([json.loads(f.model_dump_json()) for f in found], indent=1) + "\n")
    else:
        sys.stdout.write(render.findings_markdown(found) + "\n")


@dataset_app.command("show")
def dataset_show(
    dataset: str = typer.Option(_default_dataset()),
    version: str = typer.Option("latest"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Dataset profile: documents, entities, metrics, periods, currencies, findings."""
    info = _version(dataset, version)
    p = _repo().profile(info.dataset_version_id)
    if as_json:
        sys.stdout.write(p.model_dump_json(indent=1) + "\n")
        return
    console.print(
        f"[bold]{info.dataset_name}[/] version {p.dataset_version_id} ({p.status.value}) · "
        f"source {p.source_hash[:12]} · parser {p.parser_version}"
    )
    for d in p.documents:
        console.print(
            f"  {d.name}: {d.lines} lines, {d.facts} facts, {d.chunks} chunks, "
            f"{d.table_cells} cells ({d.untyped_cells} untyped)"
        )
    console.print(f"entities ({len(p.entities)}): " + ", ".join(e.label for e in p.entities[:30]))
    console.print(f"metrics ({len(p.metrics)}): " + ", ".join(m.label for m in p.metrics[:30]))
    console.print("currencies: " + ", ".join(f"{c.label} ({c.count})" for c in p.currencies))
    console.print("findings: " + (", ".join(f"{k.value} {v}" for k, v in p.findings_by_rule.items()) or "none"))


@app.command(name="eval")
def eval_cmd(
    questions: Path = typer.Option(Path("contracts/fixture/questions.json"), exists=True),
    dataset: str = typer.Option(_default_dataset()),
    version: str = typer.Option("latest"),
    as_json: bool = typer.Option(False, "--json"),
    audit: bool = typer.Option(True),
) -> None:
    """Run an evaluation suite and print the scorecard."""
    from app.eval import runner

    eng = _engine(audit)
    cases = runner.load_cases(questions)

    def progress(res, ans):
        if not as_json:
            mark = "[green]PASS[/]" if res.passed else "[red]FAIL[/]"
            console.print(f"{mark} {res.case_id} {ans.status.value} {res.detail or ''}")

    rep = runner.run(
        cases,
        lambda q: eng.ask(q, dataset=dataset, version=version),
        suite=str(questions),
        on_result=progress,
    )
    if as_json:
        sys.stdout.write(rep.model_dump_json(indent=1) + "\n")
    else:
        render.report(console, rep)


if __name__ == "__main__":
    app()
