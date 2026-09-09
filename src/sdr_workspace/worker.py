from __future__ import annotations

import time

from sdr_workspace.clock import SystemClock
from sdr_workspace.config import get_settings
from sdr_workspace.db import SessionLocal
from sdr_workspace.services.dispatch import execute_job
from sdr_workspace.services.jobs import claim_jobs


def run_once() -> int:
    settings = get_settings()
    clock = SystemClock()
    session = SessionLocal()
    try:
        jobs = claim_jobs(session, worker_id=settings.worker_id, lease_seconds=settings.job_lease_seconds, clock=clock)
        session.commit()
        for job in jobs:
            execute_job(session, job, settings, clock)
            session.commit()
        return len(jobs)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_forever() -> None:
    while True:
        n = run_once()
        time.sleep(2 if n else 5)


if __name__ == "__main__":
    run_forever()
