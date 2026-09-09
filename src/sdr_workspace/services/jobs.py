from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from sdr_workspace.clock import Clock
from sdr_workspace.enums import JobStatus
from sdr_workspace.models import Job


def enqueue(
    session: Session,
    *,
    job_type: str,
    idempotency_key: str,
    payload: dict,
    due_at: datetime,
    delivery_mode: str,
) -> Job:
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing:
        return existing
    job = Job(
        id=uuid4(),
        job_type=job_type,
        idempotency_key=idempotency_key,
        payload=payload,
        due_at=due_at,
        delivery_mode=delivery_mode,
        status=JobStatus.PENDING.value,
    )
    session.add(job)
    session.flush()
    return job


def claim_jobs(session: Session, *, worker_id: str, lease_seconds: int, clock: Clock, limit: int = 10) -> list[Job]:
    now = clock.now()
    expiry = now + timedelta(seconds=lease_seconds)
    stmt = (
        select(Job)
        .where(
            Job.due_at <= now,
            or_(
                Job.status == JobStatus.PENDING.value,
                and_(Job.status == JobStatus.LEASED.value, Job.lease_expires_at <= now),
            ),
        )
        .order_by(Job.due_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list(session.scalars(stmt))
    for job in jobs:
        job.status = JobStatus.LEASED.value
        job.lease_owner = worker_id
        job.lease_expires_at = expiry
        job.attempt_count += 1
        job.fence_token += 1
    session.flush()
    return jobs


def complete(session: Session, job: Job, status: str, error: str = "") -> None:
    job.status = status
    job.last_error = error
    job.lease_owner = ""
    job.lease_expires_at = None
