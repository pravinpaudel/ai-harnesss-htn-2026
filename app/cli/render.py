"""Rich rendering for CLI output. `--json` bypasses this entirely."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contracts.models import AnswerResponse, AnswerStatus, EvaluationReport, ValidationFinding

STATUS_STYLE = {
    AnswerStatus.answered: ("ANSWERED", "bold white on green"),
    AnswerStatus.conflict: ("CONFLICT", "bold black on yellow"),
    AnswerStatus.partial: ("PARTIAL", "bold white on blue"),
    AnswerStatus.declined: ("DECLINED", "bold white on grey42"),
}


def answer(console: Console, a: AnswerResponse) -> None:
    label, style = STATUS_STYLE[a.status]
    head = Text.assemble((f" {label} ", style), "  ", (a.evidence_status.value.replace("_", " "), "dim"))
    if a.decline_reason:
        head.append(f"  reason: {a.decline_reason.value.replace('_', ' ')}", style="italic")
    body = Text(a.answer)
    console.print(Panel(body, title=head, title_align="left", border_style=style.split()[-1]))

    if a.values:
        t = Table(title="Values", show_edge=False, header_style="bold")
        for col in ("label", "value", "unit", "currency", "cites"):
            t.add_column(col)
        for v in a.values:
            val = v.value_text or (f"{v.value:,.4g}" if v.value is not None else "")
            t.add_row(v.label, val, v.unit.value, v.currency or "", " ".join(f"[{n}]" for n in v.citation_ids))
        console.print(t)
    for c in a.calculation_trace:
        verdict = f"REJECTED: {c.rejection_reason}" if c.rejected else f"= {c.result}{'%' if c.unit.value == 'pct' else ''}"
        console.print(f"[bold]Calculation[/] {c.operation.value}: {c.formula} [green]{verdict}[/]")
    for cf in a.conflicts:
        console.print(f"[bold yellow]Contradiction[/] ({cf.rule.value}): {cf.explanation}")
        for cl in cf.claims:
            console.print(f"   • {cl.text} " + " ".join(f"[{n}]" for n in cl.citation_ids))
    if a.citations:
        t = Table(title="Sources (verified verbatim)", show_edge=False, header_style="bold")
        t.add_column("#")
        t.add_column("document:lines")
        t.add_column("quote", overflow="fold")
        for c in a.citations:
            s = c.span
            lines = f"{s.line_start}" if s.line_start == s.line_end else f"{s.line_start}-{s.line_end}"
            q = s.exact_text if len(s.exact_text) <= 160 else s.exact_text[:157] + "…"
            t.add_row(str(c.citation_id), f"{s.document_name}:{lines}", q)
        console.print(t)
    for lim in a.limitations:
        console.print(f"[dim]limitation:[/] {lim}")
    u = a.provenance.usage
    console.print(f"[dim]run {a.run_id} · {a.provenance.model} · {u.tool_rounds} tool rounds · "
                  f"{u.input_tokens + u.output_tokens:,} tokens · ${u.cost_usd:.4f} · {u.latency_ms} ms[/]")


def findings_markdown(findings: list[ValidationFinding]) -> str:
    out = [f"# Contradictions and data-quality findings ({len(findings)})", ""]
    for i, f in enumerate(findings, 1):
        out.append(f"## {i}. {f.rule.value.replace('_', ' ')} ({f.severity.value})")
        out.append("")
        out.append(f.explanation)
        if f.observed or f.expected:
            out.append("")
            out.append(f"- Stated: {f.observed or '—'}")
            out.append(f"- Recomputed / other evidence: {f.expected or '—'}")
        out.append("")
        for s in f.spans[:8]:
            out.append(f"  - `{s.document_name}:{s.line_start}` — \"{s.exact_text}\"")
        out.append("")
    return "\n".join(out)


def report(console: Console, r: EvaluationReport) -> None:
    t = Table(title=f"Evaluation: {r.suite}", header_style="bold")
    for col in ("case", "category", "result", "detail"):
        t.add_column(col, overflow="fold")
    for c in r.cases:
        t.add_row(c.case_id, c.category.value, "[green]PASS[/]" if c.passed else "[red]FAIL[/]",
                  c.detail or "")
    console.print(t)
    passed = sum(c.passed for c in r.cases)
    console.print(f"[bold]{passed}/{len(r.cases)} passed[/] · citation validity {r.citation_validity:.0%} · "
                  f"conflict recall {r.conflict_recall:.0%} · unsupported-answer rate {r.unsupported_answer_rate:.0%}"
                  f" · mean {r.mean_latency_ms:.0f} ms · p95 {r.p95_latency_ms:.0f} ms · "
                  f"mean ${r.mean_cost_usd:.4f}")
