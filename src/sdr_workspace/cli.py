from __future__ import annotations

import argparse

import uvicorn

from sdr_workspace.config import get_settings
from sdr_workspace.db import SessionLocal, engine
from sdr_workspace.models import Base
from sdr_workspace.seed import seed
from sdr_workspace.services.dispatch import dispatch_tick, execute_job
from sdr_workspace.services.jobs import claim_jobs
from sdr_workspace.clock import SystemClock
from sdr_workspace.worker import run_forever, run_once


def init_db() -> None:
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        seed(session)
        session.commit()
    finally:
        session.close()


def tick() -> None:
    settings = get_settings()
    clock = SystemClock()
    session = SessionLocal()
    try:
        print(dispatch_tick(session, settings, clock))
        jobs = claim_jobs(session, worker_id="cli-tick", lease_seconds=settings.job_lease_seconds, clock=clock, limit=50)
        for job in jobs:
            execute_job(session, job, settings, clock)
            print(job.job_type, job.status, job.last_error)
        session.commit()
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="sdr-workspace")
    parser.add_argument("command", choices=["init-db", "seed", "serve", "worker", "tick"])
    args = parser.parse_args()
    if args.command in {"init-db", "seed"}:
        init_db()
        print("Database initialized and seeded.")
    elif args.command == "serve":
        init_db()
        uvicorn.run("sdr_workspace.api.app:app", host="127.0.0.1", port=8000, reload=True)
    elif args.command == "worker":
        run_forever()
    elif args.command == "tick":
        tick()


if __name__ == "__main__":
    main()
