"""Bounded OpenAI Responses API client used by the future research engine."""
from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from app.settings import Settings


@dataclass(frozen=True)
class InferenceResult:
    response_id: str
    model: str
    text: str


class OpenAIInferenceClient:
    """Creates stateless, bounded Responses API calls; it never supplies corpus evidence itself."""

    def __init__(self, settings: Settings) -> None:
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
            raise RuntimeError("OPENAI_API_KEY is required before OpenAI inference can run")
        self.model = settings.openai_model
        self.max_output_tokens = settings.openai_max_output_tokens
        self.reasoning_effort = settings.openai_reasoning_effort
        self._client = OpenAI(api_key=settings.openai_api_key.get_secret_value())

    def respond(self, prompt: str, *, instructions: str | None = None) -> InferenceResult:
        response = self._client.responses.create(
            model=self.model,
            input=prompt,
            instructions=instructions,
            max_output_tokens=self.max_output_tokens,
            reasoning={"effort": self.reasoning_effort},
            store=False,
        )
        return InferenceResult(response_id=response.id, model=response.model, text=response.output_text)
