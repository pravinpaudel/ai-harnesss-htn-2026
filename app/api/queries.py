"""Query-facing HTTP routes (Developer B). `POST /v1/queries` returns exactly what `htn ask --json` prints."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from contracts.models import AnswerRequest, AnswerResponse, DatasetProfile, ValidationFinding

router = APIRouter(prefix="/v1", tags=["research"])


def get_repo():
    from app.reasoning.factory import build_repo

    return build_repo()


def get_engine():
    from app.reasoning.factory import EngineConfigError, build_engine

    try:
        return build_engine(audit=True)
    except EngineConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _version(repo, dataset: str, version: str):
    try:
        return repo.version_info(dataset, version)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/queries", response_model=AnswerResponse, response_model_exclude_none=True)
def create_query(request: AnswerRequest, dataset: str = Query("latest", description="Dataset name or 'latest'"),
                 engine=Depends(get_engine)) -> AnswerResponse:
    """Answer one question from one dataset version, with verified citations and an audit trail."""
    try:
        return engine.ask(request.question, dataset=dataset, version=request.dataset_version,
                          session_id=request.session_id, budget=request.budget)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/runs/{run_id}")
def get_run(run_id: UUID, repo=Depends(get_repo)) -> dict[str, Any]:
    """The answer and every recorded step (routing, tool calls, LLM turns, policy decisions)."""
    from app.audit.trace import load_trace

    try:
        run, events = load_trace(repo.engine, run_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"run": run, "events": events}


@router.get("/conflicts", response_model=list[ValidationFinding])
def list_conflicts(dataset: str = "latest", version: str = "latest",
                   rule: Optional[str] = None, repo=Depends(get_repo)) -> list[ValidationFinding]:
    """Open contradictions and data-quality findings for one dataset version."""
    from contracts.models import FindingRule

    info = _version(repo, dataset, version)
    try:
        rules = [FindingRule(rule)] if rule else None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"unknown rule {rule!r}") from e
    return repo.list_findings(info.dataset_version_id, rules=rules)


@router.get("/datasets/{dataset}", response_model=DatasetProfile)
def get_dataset(dataset: str, version: str = "latest", repo=Depends(get_repo)) -> DatasetProfile:
    """Dataset profile: documents, entities, metrics, periods, currencies and findings by rule."""
    info = _version(repo, dataset, version)
    return repo.profile(info.dataset_version_id)
