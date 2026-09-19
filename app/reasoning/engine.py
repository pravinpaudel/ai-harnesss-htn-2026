"""ResearchEngine.ask: router -> bounded tool loop -> policy gate -> audited AnswerResponse."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional
from uuid import UUID

from pydantic import ValidationError

from contracts.models import AnswerResponse
from app.audit.recorder import MemoryRecorder, Recorder
from app.llm.client import LLMClient, LLMTurn
from app.reasoning import policy
from app.reasoning.context import EvidenceSource, RunContext
from app.reasoning.router import route
from app.settings import EngineSettings
from app.tools.registry import SUBMIT, SubmitAnswerArgs, run_tool, tool_specs

PROMPTS = Path(__file__).parent / "prompts"


class ResearchEngine:
    def __init__(self, repo: EvidenceSource, llm: LLMClient, settings: EngineSettings,
                 recorder: Optional[Recorder] = None):
        self.repo, self.llm, self.settings = repo, llm, settings
        self.recorder = recorder or MemoryRecorder()
        self.instructions = (PROMPTS / f"{settings.htn_prompt_version}.md").read_text(encoding="utf-8")

    # ------------------------------------------------------------------------
    def ask(self, question: str, dataset: str | UUID = "latest", version: str | UUID = "latest",
            session_id: Optional[str] = None) -> AnswerResponse:
        info = self.repo.version_info(dataset, version)
        ctx = RunContext(repo=self.repo, dataset_id=info.dataset_id, dataset_version_id=info.dataset_version_id,
                         source_hash=info.source_hash, parser_version=info.parser_version, question=question)
        s = self.settings
        rec = self.recorder
        rec.start(ctx.run_id, ctx.dataset_version_id, question, session_id, source_hash=ctx.source_hash,
                  parser_version=ctx.parser_version, prompt_version=s.htn_prompt_version, model=self.llm.model)

        t0 = time.monotonic()
        plan = route(ctx)
        rec.event(ctx.run_id, "policy", "route", input={"question": question},
                  output={"kind": plan.kind, "entity_matches": plan.entity_matches},
                  latency_ms=int((time.monotonic() - t0) * 1000))

        tools = tool_specs()
        submit_only = tool_specs({SUBMIT})
        user = f"{plan.as_prompt()}\n\nQuestion: {question}"
        turn = self._llm_call(ctx, "start", lambda: self.llm.start(self.instructions, user, tools))

        while True:
            stop = self._over_budget(ctx)
            submit = next((c for c in turn.calls if c.name == SUBMIT), None)
            if submit is not None:
                return self._finish(ctx, submit.arguments)
            if stop:
                return self._exhausted(ctx, stop)
            if not turn.calls:
                return self._exhausted(ctx, "model returned no tool call")
            ctx.usage.tool_rounds += 1
            outputs = []
            for call in turn.calls:
                t = time.monotonic()
                out = run_tool(ctx, call.name, call.arguments)
                outputs.append((call.call_id, out))
                rec.event(ctx.run_id, "tool_call", call.name, input=call.arguments, output=out,
                          latency_ms=int((time.monotonic() - t) * 1000))
            last_round = ctx.usage.tool_rounds >= s.htn_max_tool_rounds
            turn = self._llm_call(ctx, "next", lambda: self.llm.next(
                turn, outputs, submit_only if last_round else tools, force_tool=SUBMIT if last_round else None))

    # ------------------------------------------------------------------------
    def _llm_call(self, ctx: RunContext, name: str, fn) -> LLMTurn:
        t = time.monotonic()
        turn = fn()
        ctx.usage.input_tokens += turn.input_tokens
        ctx.usage.output_tokens += turn.output_tokens
        ctx.usage.cost_usd += self.settings.cost_usd(self.llm.model, turn.input_tokens, turn.output_tokens)
        self.recorder.event(ctx.run_id, "llm", name, output={"calls": [c.name for c in turn.calls],
                                                             "text": turn.text},
                            latency_ms=int((time.monotonic() - t) * 1000),
                            tokens=turn.input_tokens + turn.output_tokens)
        return turn

    def _over_budget(self, ctx: RunContext) -> Optional[str]:
        s = self.settings
        if ctx.usage.input_tokens + ctx.usage.output_tokens > s.htn_max_tokens:
            return f"token budget of {s.htn_max_tokens} exceeded"
        if ctx.usage.cost_usd > s.htn_max_cost_usd:
            return f"cost budget of ${s.htn_max_cost_usd:.2f} exceeded"
        if ctx.elapsed_ms() > s.htn_max_latency_ms:
            return f"latency budget of {s.htn_max_latency_ms} ms exceeded"
        if ctx.usage.tool_rounds >= s.htn_max_tool_rounds:
            return f"tool-round limit of {s.htn_max_tool_rounds} reached"
        return None

    def _kw(self) -> dict:
        return dict(model=self.llm.model, prompt_version=self.settings.htn_prompt_version,
                    embedding_model=self.settings.htn_embedding_model)

    def _finish(self, ctx: RunContext, arguments: dict) -> AnswerResponse:
        try:
            sub = SubmitAnswerArgs.model_validate(arguments)
        except ValidationError as e:
            return self._exhausted(ctx, f"invalid submit_answer: {e.errors()[0]['msg']}")
        resp, notes = policy.finalize(ctx, sub, **self._kw())
        return self._record(ctx, resp, notes, submitted=arguments)

    def _exhausted(self, ctx: RunContext, reason: str) -> AnswerResponse:
        resp, notes = policy.budget_exhausted(ctx, reason, **self._kw())
        return self._record(ctx, resp, notes, submitted={"stopped": reason})

    def _record(self, ctx: RunContext, resp: AnswerResponse, notes: policy.PolicyNotes, submitted: dict) -> AnswerResponse:
        self.recorder.event(ctx.run_id, "verify", "policy_gate", input=submitted, output=notes.as_dict(),
                            span_ids=[c.span.span_id for c in resp.citations if c.span.span_id])
        self.recorder.finish(resp)
        return resp
