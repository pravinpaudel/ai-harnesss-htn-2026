from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .models import Job


class JobRepository:
    """PostgreSQL queue with explicit state transitions and SKIP LOCKED claiming."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(self, job_type: str, payload: dict, idempotency_key: str | None = None) -> Job:
        if idempotency_key:
            existing = self.session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
            if existing:
                return existing
        job = Job(job_type=job_type, payload=payload, idempotency_key=idempotency_key, state="queued")
        self.session.add(job)
        self.session.flush()
        return job

    def claim_next(self, job_type: str) -> Job | None:
        statement: Select[tuple[Job]] = (select(Job).where(Job.job_type == job_type, Job.state.in_(("queued", "retrying")))
            .order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        job = self.session.scalar(statement)
        if job is None:
            return None
        job.state, job.claimed_by, job.attempts = "running", "worker", job.attempts + 1
        self.session.flush()
        return job

    def succeed(self, job: Job) -> None:
        job.state, job.error = "succeeded", None

    def fail(self, job: Job, error: str, max_attempts: int) -> None:
        job.error = error
        job.state = "retrying" if job.attempts < max_attempts else "failed"
