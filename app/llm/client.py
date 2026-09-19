"""LLM clients: the OpenAI Responses API with function tools, and a scripted fake for tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str


@dataclass
class LLMTurn:
    calls: list[ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0
    text: Optional[str] = None
    response_id: Optional[str] = None


class LLMClient(Protocol):
    model: str

    def start(self, instructions: str, user_input: str, tools: list[dict]) -> LLMTurn: ...

    def next(self, previous: LLMTurn, outputs: list[tuple[str, dict]], tools: list[dict],
             force_tool: Optional[str] = None) -> LLMTurn:
        """Send tool outputs (call_id, result) for the previous turn and get the next turn."""


class OpenAIResponsesClient:
    """Tool loop over the Responses API. Conversation state is kept server-side via previous_response_id."""

    def __init__(self, model: str, api_key: Optional[str] = None, **client_kwargs: Any):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, **client_kwargs)
        self._instructions = ""

    def _turn(self, resp: Any) -> LLMTurn:
        calls, texts = [], []
        for item in resp.output:
            if item.type == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_unparseable": item.arguments}
                calls.append(ToolCall(item.name, args, item.call_id))
            elif item.type == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", "") == "output_text":
                        texts.append(part.text)
        usage = getattr(resp, "usage", None)
        return LLMTurn(calls=calls, input_tokens=getattr(usage, "input_tokens", 0) or 0,
                       output_tokens=getattr(usage, "output_tokens", 0) or 0,
                       text="\n".join(texts) or None, response_id=resp.id)

    def start(self, instructions: str, user_input: str, tools: list[dict]) -> LLMTurn:
        self._instructions = instructions
        resp = self._client.responses.create(model=self.model, instructions=instructions, input=user_input,
                                             tools=tools, tool_choice="required", parallel_tool_calls=True)
        return self._turn(resp)

    def next(self, previous: LLMTurn, outputs: list[tuple[str, dict]], tools: list[dict],
             force_tool: Optional[str] = None) -> LLMTurn:
        items = [{"type": "function_call_output", "call_id": cid, "output": json.dumps(out, default=str)}
                 for cid, out in outputs]
        choice: Any = {"type": "function", "name": force_tool} if force_tool else "required"
        resp = self._client.responses.create(model=self.model, instructions=self._instructions,
                                             previous_response_id=previous.response_id, input=items,
                                             tools=tools, tool_choice=choice, parallel_tool_calls=True)
        return self._turn(resp)


@dataclass
class ScriptedLLM:
    """Deterministic stand-in: replays a list of turns, each a list of (tool_name, arguments).

    An argument value that is a callable receives the tool outputs seen so far and returns the
    real value, so a script can cite handles it has not seen yet, e.g.
        lambda seen: seen["find_facts"][0]["facts"][0]["handle"]
    """

    script: list[list[tuple[str, Any]]]
    model: str = "scripted"
    tokens_per_turn: tuple[int, int] = (1000, 100)
    seen: dict[str, list[dict]] = field(default_factory=dict)
    received: list[list[tuple[str, dict]]] = field(default_factory=list)
    forced: list[Optional[str]] = field(default_factory=list)
    _i: int = 0
    _calls: dict[str, str] = field(default_factory=dict)

    def _resolve(self, v: Any) -> Any:
        if callable(v):
            return v(self.seen)
        if isinstance(v, dict):
            return {k: self._resolve(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._resolve(x) for x in v]
        return v

    def _emit(self) -> LLMTurn:
        if self._i >= len(self.script):
            steps = [("submit_answer", {"status": "declined", "answer": "Script ended.", "citations": [],
                                         "values": [], "calculation_handles": [], "conflict_finding_handles": [],
                                         "limitations": ["script ended"], "decline_reason": "insufficient_evidence"})]
        else:
            steps = self.script[self._i]
        self._i += 1
        calls = []
        for j, (name, args) in enumerate(steps):
            cid = f"call_{self._i}_{j}"
            self._calls[cid] = name
            calls.append(ToolCall(name, self._resolve(args), cid))
        return LLMTurn(calls=calls, input_tokens=self.tokens_per_turn[0], output_tokens=self.tokens_per_turn[1],
                       response_id=f"scripted_{self._i}")

    def start(self, instructions: str, user_input: str, tools: list[dict]) -> LLMTurn:
        self.instructions, self.user_input, self.tools = instructions, user_input, tools
        return self._emit()

    def next(self, previous: LLMTurn, outputs: list[tuple[str, dict]], tools: list[dict],
             force_tool: Optional[str] = None) -> LLMTurn:
        self.received.append(outputs)
        self.forced.append(force_tool)
        for cid, out in outputs:
            self.seen.setdefault(self._calls[cid], []).append(out)
        return self._emit()
