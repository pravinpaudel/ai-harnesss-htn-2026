"""Server-sent events for one answer: the steps the engine takes, then the answer itself.

The engine already records every step (routing, tool calls, model turns, the policy gate) through a
Recorder. `TeeRecorder` forwards those to the normal audit recorder and also onto a queue, so a
browser can watch a 10-20 second answer being built instead of a spinner.

Frames, in order:

    event: run      {"run_id": "…", "dataset_version": "…"}
    event: step     {"seq": 1, "kind": "tool_call", "name": "find_facts", "detail": "IVN revenue", …}
    event: answer   the AnswerResponse, exactly as POST /v1/queries returns it
    event: done     {"run_id": "…"}

An error arrives as `event: error {"detail": "…"}`. A `: keep-alive` comment goes out every 15
seconds of quiet so proxies do not close an idle connection.
"""

from __future__ import annotations

import asyncio
import json
import queue
from typing import Any, AsyncIterator, Optional
from uuid import UUID

from contracts.models import AnswerResponse

HEARTBEAT_S = 15.0
_DONE = object()
_QUIET = object()


class TeeRecorder:
    """Records as usual, and publishes each step to a queue for a listening client."""

    def __init__(self, inner, sink: "queue.Queue[Any]") -> None:
        self.inner, self.sink, self._seq = inner, sink, 0

    def start(self, run_id: UUID, dataset_version_id: UUID, question: str, session_id: Optional[str],
              **meta: Any) -> None:
        self.inner.start(run_id, dataset_version_id, question, session_id, **meta)
        self.sink.put(("run", {"run_id": str(run_id), "dataset_version": str(dataset_version_id)}))

    def event(self, run_id: UUID, kind: str, name: str, input: Any = None, output: Any = None,
              span_ids: list[UUID] = (), latency_ms: int = 0, tokens: Optional[int] = None) -> None:
        self.inner.event(run_id, kind, name, input=input, output=output, span_ids=span_ids,
                         latency_ms=latency_ms, tokens=tokens)
        self._seq += 1
        self.sink.put(("step", {"seq": self._seq, "kind": kind, "name": name,
                                "detail": _detail(kind, name, input, output), "latency_ms": latency_ms}))

    def finish(self, response: AnswerResponse) -> None:
        self.inner.finish(response)


def _detail(kind: str, name: str, input: Any, output: Any) -> Optional[str]:
    """A short, human-readable line for the step, without leaking whole tool payloads."""
    if kind == "policy" and name == "route":
        kinds = (output or {}).get("kind") if isinstance(output, dict) else None
        return f"routed as {kinds}" if kinds else None
    if kind == "tool_call" and isinstance(input, dict):
        for key in ("query", "question", "entity", "metric", "clues", "handle"):
            value = input.get(key)
            if value:
                text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                return text[:120]
        return None
    if kind == "llm":
        calls = (output or {}).get("calls") if isinstance(output, dict) else None
        return ", ".join(calls) if calls else None
    if kind == "verify" and isinstance(output, dict):
        checks = [k for k, v in output.items() if v]
        return ", ".join(checks[:4]) or None
    return None


def frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def watched(engine) -> tuple[Any, "queue.Queue[Any]"]:
    """The same engine, recording as usual but also publishing each step to a fresh queue."""
    from app.reasoning.engine import ResearchEngine

    sink: "queue.Queue[Any]" = queue.Queue()
    return (ResearchEngine(engine.repo, engine.llm, engine.settings, TeeRecorder(engine.recorder, sink),
                           embedder=engine.embedder), sink)


async def answer_stream(engine, sink: "queue.Queue[Any]", question: str, *, dataset: str, version: Any,
                        session_id: Optional[str], budget: Any) -> AsyncIterator[str]:
    """Run one question in a worker thread, yielding its steps as they happen, then the answer."""
    loop = asyncio.get_running_loop()

    def run() -> Any:
        try:
            return engine.ask(question, dataset=dataset, version=version, session_id=session_id, budget=budget)
        finally:
            sink.put(_DONE)

    def next_step() -> Any:
        # a timed get, not a cancelled wait: a cancelled getter would still be holding the queue and
        # would swallow the step that arrived while the heartbeat went out
        try:
            return sink.get(timeout=HEARTBEAT_S)
        except queue.Empty:
            return _QUIET

    task = loop.run_in_executor(None, run)
    while True:
        item = await loop.run_in_executor(None, next_step)
        if item is _QUIET:
            yield ": keep-alive\n\n"
            continue
        if item is _DONE:
            break
        event, data = item
        yield frame(event, data)

    try:
        answer = await task
    except Exception as exc:  # noqa: BLE001 - the client gets the failure as an SSE frame, not a dropped socket
        yield frame("error", {"detail": f"{type(exc).__name__}: {exc}"})
        return
    yield frame("answer", json.loads(answer.model_dump_json(exclude_none=True)))
    yield frame("done", {"run_id": str(answer.run_id)})
