"""Audit trail: one answer_run per question, one tool_event per step (contracts/schema.sql)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from uuid import UUID

from sqlalchemy import Engine, create_engine, text

from contracts.models import AnswerResponse


class Recorder(Protocol):
    def start(self, run_id: UUID, dataset_version_id: UUID, question: str, session_id: Optional[str], *,
              source_hash: str, parser_version: str, prompt_version: str, model: str) -> None: ...
    def event(self, run_id: UUID, kind: str, name: str, input: Any = None, output: Any = None,
              span_ids: list[UUID] = (), latency_ms: int = 0, tokens: Optional[int] = None) -> None: ...
    def finish(self, response: AnswerResponse) -> None: ...


def _json(v: Any) -> Optional[str]:
    return None if v is None else json.dumps(v, default=str)


class PgRecorder:
    """Writes answer_run/tool_event with the engine role (its only write grants)."""

    def __init__(self, engine: Engine | str):
        self.engine = create_engine(engine) if isinstance(engine, str) else engine
        self._seq: dict[UUID, int] = {}

    def start(self, run_id, dataset_version_id, question, session_id, *, source_hash, parser_version,
              prompt_version, model) -> None:
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO answer_run (run_id, dataset_version_id, session_id, question,
                                source_hash, parser_version, prompt_version, model)
                              VALUES (:r, :v, :s, :q, :h, :p, :pv, :m)"""),
                      {"r": str(run_id), "v": str(dataset_version_id), "s": session_id, "q": question,
                       "h": source_hash or "unknown", "p": parser_version, "pv": prompt_version, "m": model})
        self._seq[run_id] = 0

    def event(self, run_id, kind, name, input=None, output=None, span_ids=(), latency_ms=0, tokens=None) -> None:
        self._seq[run_id] = self._seq.get(run_id, 0) + 1
        with self.engine.begin() as c:
            c.execute(text("""INSERT INTO tool_event (run_id, seq, kind, name, input, output, span_ids, latency_ms, tokens)
                              VALUES (:r, :seq, :k, :n, CAST(:i AS jsonb), CAST(:o AS jsonb),
                                      CAST(:sp AS uuid[]), :ms, :t)"""),
                      {"r": str(run_id), "seq": self._seq[run_id], "k": kind, "n": name, "i": _json(input),
                       "o": _json(output), "sp": [str(s) for s in span_ids], "ms": latency_ms, "t": tokens})

    def finish(self, response: AnswerResponse) -> None:
        u = response.provenance.usage
        with self.engine.begin() as c:
            c.execute(text("""UPDATE answer_run SET status = CAST(:st AS answer_status),
                                evidence_status = CAST(:es AS evidence_status), response = CAST(:resp AS jsonb),
                                completed_at = now(), input_tokens = :it, output_tokens = :ot, cost_usd = :cost,
                                latency_ms = :ms WHERE run_id = :r"""),
                      {"st": response.status.value, "es": response.evidence_status.value,
                       "resp": response.model_dump_json(), "it": u.input_tokens, "ot": u.output_tokens,
                       "cost": u.cost_usd, "ms": u.latency_ms, "r": str(response.run_id)})


@dataclass
class MemoryRecorder:
    """In-process recorder for tests and --no-audit runs."""

    runs: dict[UUID, dict] = field(default_factory=dict)
    events: dict[UUID, list[dict]] = field(default_factory=dict)

    def start(self, run_id, dataset_version_id, question, session_id, **meta) -> None:
        self.runs[run_id] = {"dataset_version_id": dataset_version_id, "question": question, **meta,
                             "started": time.time()}
        self.events[run_id] = []

    def event(self, run_id, kind, name, input=None, output=None, span_ids=(), latency_ms=0, tokens=None) -> None:
        self.events.setdefault(run_id, []).append({"seq": len(self.events.get(run_id, [])) + 1, "kind": kind,
                                                   "name": name, "input": input, "output": output,
                                                   "span_ids": list(span_ids), "latency_ms": latency_ms,
                                                   "tokens": tokens})

    def finish(self, response: AnswerResponse) -> None:
        self.runs[response.run_id]["response"] = response
