"""Query-facing HTTP routes (Developer B). `POST /v1/queries` returns exactly what `htn ask --json` prints."""

from __future__ import annotations

import threading
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.settings import settings
from contracts.models import AnswerRequest, AnswerResponse, DatasetProfile, ValidationFinding

router = APIRouter(prefix="/v1", tags=["research"])

# Answering is slow and CPU-cheap but connection- and API-bound; admit a bounded number at a time and
# tell the rest to retry, rather than letting every request sit on a worker thread.
_slots = threading.BoundedSemaphore(settings.api_max_concurrent_queries)


def _take_slot() -> None:
    """Reserve one of the concurrent-answer slots, or fail the request with 503."""
    if not _slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="busy answering other questions; retry shortly",
                            headers={"Retry-After": "5"})


class _Slot:
    def __enter__(self):
        _take_slot()
        return self

    def __exit__(self, *exc) -> None:
        _slots.release()


def get_repo():
    from app.reasoning.factory import build_repo

    return build_repo()                      # shared pool, not one per request


def get_engine():
    from app.reasoning.factory import EngineConfigError, shared_engine

    try:
        return shared_engine(audit=True)     # shared model client and pool
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
    with _Slot():
        try:
            return engine.ask(request.question, dataset=dataset, version=request.dataset_version,
                              session_id=request.session_id, budget=request.budget)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/queries/stream")
async def stream_query(request: AnswerRequest, dataset: str = Query("latest", description="Dataset name or 'latest'"),
                       engine=Depends(get_engine)) -> StreamingResponse:
    """The same answer as POST /v1/queries, as server-sent events: each step first, then the answer."""
    from app.api.streaming import answer_stream, watched

    _take_slot()                      # before the response starts, so a busy server can still answer 503
    engine_, sink = watched(engine)
    steps = answer_stream(engine_, sink, request.question, dataset=dataset, version=request.dataset_version,
                          session_id=request.session_id, budget=request.budget)

    async def released():
        try:
            async for chunk in steps:
                yield chunk
        finally:
            _slots.release()

    return StreamingResponse(released(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
