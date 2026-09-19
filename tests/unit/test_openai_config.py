import pytest

from app.llm.openai_client import OpenAIInferenceClient
from app.settings import Settings


def test_openai_client_requires_an_api_key_before_network_use():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIInferenceClient(Settings(openai_api_key=None))


def test_openai_client_accepts_configured_model_without_making_a_request():
    client = OpenAIInferenceClient(Settings(openai_api_key="test-key"))
    assert (client.model, client.reasoning_effort) == ("gpt-5.6-luna", "medium")
