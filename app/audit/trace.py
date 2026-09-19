"""Read an answer run and its events back for `htn trace` (read-only)."""

from __future__ import annotations

import json
from uuid import UUID

from rich.console import Console
from rich.table import Table
from sqlalchemy import Engine, text


def load_trace(engine: Engine, run_id: UUID) -> tuple[dict, list[dict]]:
    with engine.connect() as c:
        run = c.execute(text("""SELECT run_id, question, status::text AS status, evidence_status::text AS evidence_status,
                                       model, prompt_version, parser_version, source_hash, input_tokens, output_tokens,
                                       cost_usd, latency_ms, created_at, completed_at, dataset_version_id, response
                                FROM answer_run WHERE run_id = :r"""), {"r": str(run_id)}).mappings().first()
        if run is None:
            raise LookupError(f"no run {run_id}")
        events = c.execute(text("""SELECT seq, kind, name, input, output, span_ids, latency_ms, tokens
                                   FROM tool_event WHERE run_id = :r ORDER BY seq"""),
                           {"r": str(run_id)}).mappings().all()
    return dict(run), [dict(e) for e in events]


def render(console: Console, run: dict, events: list[dict]) -> None:
    console.print(f"[bold]Run[/] {run['run_id']}  [bold]status[/] {run['status']} ({run['evidence_status']})")
    console.print(f"question: {run['question']}")
    console.print(f"[dim]dataset version {run['dataset_version_id']} · source {str(run['source_hash'])[:12]} · "
                  f"parser {run['parser_version']} · prompt {run['prompt_version']} · model {run['model']} · "
                  f"{run['input_tokens']}+{run['output_tokens']} tokens · ${float(run['cost_usd'] or 0):.4f} · "
                  f"{run['latency_ms']} ms[/]")
    t = Table(show_edge=False, header_style="bold")
    for col in ("#", "kind", "name", "input", "output", "ms"):
        t.add_column(col, overflow="fold")
    for e in events:
        t.add_row(str(e["seq"]), e["kind"], e["name"], _short(e["input"]), _short(e["output"]),
                  str(e["latency_ms"]))
    console.print(t)


def _short(v, n: int = 220) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"
