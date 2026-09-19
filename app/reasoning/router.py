"""Deterministic pre-step: classify the question and seed the dataset profile and entity matches."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.reasoning.context import RunContext
from app.tools.registry import InspectDatasetArgs, ResolveEntityArgs, inspect_dataset, resolve_entity

_QUANT = re.compile(r"\b(how (much|many)|what (was|is|were) the|percent|percentage|%|change|grow|growth|rank|"
                    r"compare|higher|lower|larger|bigger|smaller|faster|total|sum|average|median|ratio|"
                    r"difference|beat|miss|estimate|revenue|eps|margin|market cap|q[1-4]|fy\d{2,4}|\d)",
                    re.IGNORECASE)
_NARR = re.compile(r"\b(why|how did|what drove|explain|reason|because|commentary|outlook|risk)", re.IGNORECASE)
_CANDIDATE = re.compile(r"\b([A-Z][A-Za-z&.\-]*(?:\s+[A-Z][A-Za-z&.\-]*){0,3}|[A-Z]{2,6}(?:\.[A-Z])?)\b")
_STOP = {"What", "Which", "Why", "How", "Did", "Does", "Do", "Is", "Was", "Were", "Rank", "Compare", "The", "In",
         "Of", "By", "For", "And", "Or", "EPS", "Q1", "Q2", "Q3", "Q4", "AISC", "CAD", "USD", "YoY", "FY", "GAAP",
         "I", "A", "An"}


@dataclass
class RoutePlan:
    kind: str                                   # quantitative | narrative | mixed
    entity_matches: dict[str, list[str]] = field(default_factory=dict)
    profile: dict = field(default_factory=dict)

    def as_prompt(self) -> str:
        hint = {"quantitative": "Start with find_facts; use calculate for any arithmetic.",
                "narrative": "Start with search_evidence; cite the passages that explain the answer.",
                "mixed": "Use find_facts for numbers and search_evidence for explanations."}[self.kind]
        return ("Question type: " + self.kind + ". " + hint + "\n"
                + "Dataset profile: " + json.dumps(self.profile, default=str) + "\n"
                + "Entity matches: " + json.dumps(self.entity_matches))


def candidate_names(question: str) -> list[str]:
    out = []
    for m in _CANDIDATE.finditer(question):
        words = [re.sub(r"['’]s?$", "", w) for w in m.group(1).split()]
        name = " ".join(w for w in words if w not in _STOP).strip(" .?")
        if name and name not in _STOP and name not in out:
            out.append(name)
    return out


def route(ctx: RunContext) -> RoutePlan:
    q = ctx.question
    quant, narr = bool(_QUANT.search(q)), bool(_NARR.search(q))
    kind = "mixed" if quant and narr else "narrative" if narr else "quantitative"
    profile = inspect_dataset(ctx, InspectDatasetArgs())
    matches = {}
    for name in candidate_names(q):
        res = resolve_entity(ctx, ResolveEntityArgs(name=name))
        matches[name] = [c["label"] for c in res["candidates"]]
    return RoutePlan(kind=kind, entity_matches=matches, profile=profile)
