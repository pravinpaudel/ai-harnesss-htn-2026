"""Tool schemas are strict-mode compatible; contract examples render and validate."""

import json
from pathlib import Path

import jsonschema
from rich.console import Console

from contracts.models import AnswerResponse
from app.cli import render
from app.tools.registry import TOOLS, tool_specs

ROOT = Path(__file__).resolve().parents[2]


def _walk(node, path="$"):
    if isinstance(node, dict):
        if "properties" in node:
            assert node.get("additionalProperties") is False, path
            assert set(node["required"]) == set(node["properties"]), path
        assert "default" not in node, path
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    yield path


def test_every_tool_schema_is_strict():
    specs = tool_specs()
    assert {s["name"] for s in specs} == {t.name for t in TOOLS}
    for s in specs:
        assert s["strict"] is True
        list(_walk(s["parameters"]))
        jsonschema.Draft202012Validator.check_schema(s["parameters"])


def test_examples_validate_and_render():
    schema = json.loads((ROOT / "contracts/json-schema/AnswerResponse.json").read_text())
    console = Console(record=True, width=120)
    for p in sorted((ROOT / "contracts/examples").glob("*.json")):
        data = json.loads(p.read_text())
        jsonschema.validate(data, schema)
        render.answer(console, AnswerResponse.model_validate(data))
    out = console.export_text()
    assert "CONFLICT" in out and "DECLINED" in out and "ANSWERED" in out
