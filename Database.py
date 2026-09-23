"""
Database

Docset status, task (uuid) mappings and the job queue, via SQLAlchemy Core.

Postgres in production (DATABASE_URL), SQLite by default for local development
and tests. The schema is managed by Alembic (migrations/); `create_schema()` is
only a shortcut for tests.

The queue is a plain `jobs` table: the web app inserts a row, the worker
(worker.py) claims the oldest queued one with SELECT ... FOR UPDATE SKIP LOCKED,
so several workers never take the same job. A running job's heartbeat is renewed
while it runs; a job whose heartbeat went stale (worker killed) is queued again.
"""

import datetime as dt
import json
import logging
from typing import Dict, List, Optional

from sqlalchemy import (
    JSON, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text,
    create_engine, func, insert, select, text, update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from Config import Config

logger = logging.getLogger(__name__)

metadata = MetaData()

docsets = Table(
    "docsets", metadata,
    Column("hash", String(32), primary_key=True),
    Column("name", Text),
    Column("source", String(20), nullable=False),  # "fraunhofer" | "openalex"
    Column("iri", Text),
    Column("query", JSON),
    # Pipeline status as reported by /status (see Config: pending ... finished / error)
    Column("status", String(40), nullable=False),
    Column("error", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

tasks = Table(
    "tasks", metadata,
    Column("uuid", String(36), primary_key=True),
    Column("docset_hash", String(32), ForeignKey("docsets.hash", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

JOB_QUEUED, JOB_RUNNING, JOB_DONE, JOB_FAILED = "queued", "running", "done", "failed"

jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("docset_hash", String(32), ForeignKey("docsets.hash", ondelete="CASCADE"), nullable=False),
    Column("kind", String(20), nullable=False),  # "iri" | "openalex"
    Column("params", JSON, nullable=False),
    Column("status", String(10), nullable=False, server_default=JOB_QUEUED),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("error", Text),
    Column("worker", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)
# The worker's claim query
Index("ix_jobs_status_id", jobs.c.status, jobs.c.id)
# At most one queued/running job per docset, so two identical requests cannot race into two runs
Index(
    "uq_jobs_active_docset", jobs.c.docset_hash, unique=True,
    postgresql_where=jobs.c.status.in_([JOB_QUEUED, JOB_RUNNING]),
    sqlite_where=jobs.c.status.in_([JOB_QUEUED, JOB_RUNNING]),
)

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """Engine for Config.DATABASE_URL, created on first use."""
    global _engine
    if _engine is None:
        url = Config.DATABASE_URL
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # the web app's threads share the engine
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
    return _engine


def reset_engine():
    """Drops the cached engine (tests switch DATABASE_URL between cases)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def create_schema():
    """Creates all tables directly; production uses `alembic upgrade head` instead."""
    metadata.create_all(get_engine())


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database not reachable: {e}")
        return False


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- Docsets ---

def get_docset(docset_hash: str) -> Optional[Dict]:
    with get_engine().connect() as conn:
        row = conn.execute(select(docsets).where(docsets.c.hash == docset_hash)).mappings().first()
    return dict(row) if row else None


def list_docsets() -> List[Dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(docsets).order_by(docsets.c.created_at)).mappings().all()
    return [dict(r) for r in rows]


def set_docset_status(docset_hash: str, status: str, error: Optional[str] = None):
    """Sets the pipeline status; the error is cleared unless one is given."""
    with get_engine().begin() as conn:
        conn.execute(
            update(docsets).where(docsets.c.hash == docset_hash)
            .values(status=status, error=error, updated_at=_now())
        )


def set_docset_name(docset_hash: str, name: str):
    with get_engine().begin() as conn:
        conn.execute(update(docsets).where(docsets.c.hash == docset_hash).values(name=name, updated_at=_now()))


# --- Tasks (uuid -> docset) ---

def save_task(task_uuid: str, docset_hash: str):
    with get_engine().begin() as conn:
        conn.execute(insert(tasks).values(uuid=task_uuid, docset_hash=docset_hash))


def get_task_docset_hash(task_uuid: str) -> Optional[str]:
    with get_engine().connect() as conn:
        return conn.execute(select(tasks.c.docset_hash).where(tasks.c.uuid == task_uuid)).scalar()


# --- Job queue ---

def request_run(docset_hash: str, kind: str, params: Dict, name: Optional[str], source: str,
                iri: Optional[str] = None, query: Optional[Dict] = None) -> Optional[int]:
    """
    Resets the docset to pending and queues a job for it, in one transaction.
    Returns the job id, or None if the docset already has a queued or running job
    (the docset row is then left as it is).
    """
    values = dict(name=name, source=source, iri=iri, query=query,
                  status=Config.STATUS_PENDING, error=None, updated_at=_now())
    try:
        with get_engine().begin() as conn:
            active = conn.execute(
                select(jobs.c.id).where(jobs.c.docset_hash == docset_hash,
                                        jobs.c.status.in_([JOB_QUEUED, JOB_RUNNING]))
            ).first()
            if active:
                return None
            if conn.execute(update(docsets).where(docsets.c.hash == docset_hash).values(**values)).rowcount == 0:
                conn.execute(insert(docsets).values(hash=docset_hash, **values))
            return conn.execute(
                insert(jobs).values(docset_hash=docset_hash, kind=kind, params=params).returning(jobs.c.id)
            ).scalar()
    except IntegrityError:
        # a concurrent request queued a job for the same docset first (uq_jobs_active_docset)
        return None


def claim_job(worker: str) -> Optional[Dict]:
    """Marks the oldest queued job as running and returns it, or None if the queue is empty."""
    with get_engine().begin() as conn:
        job = conn.execute(
            select(jobs).where(jobs.c.status == JOB_QUEUED).order_by(jobs.c.id)
            .limit(1).with_for_update(skip_locked=True)
        ).mappings().first()
        if job is None:
            return None
        now = _now()
        conn.execute(
            update(jobs).where(jobs.c.id == job["id"])
            .values(status=JOB_RUNNING, worker=worker, attempts=jobs.c.attempts + 1,
                    started_at=now, heartbeat_at=now, error=None)
        )
    job = dict(job)
    job["attempts"] += 1
    return job


def heartbeat(job_id: int):
    with get_engine().begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job_id).values(heartbeat_at=_now()))


def finish_job(job_id: int, error: Optional[str] = None):
    """Marks a job done, or failed if an error is given."""
    with get_engine().begin() as conn:
        conn.execute(
            update(jobs).where(jobs.c.id == job_id)
            .values(status=JOB_FAILED if error else JOB_DONE, error=error, finished_at=_now())
        )


def requeue_job(job_id: int):
    """Puts a job the worker gave up on (e.g. on shutdown) back into the queue."""
    with get_engine().begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job_id).values(status=JOB_QUEUED, worker=None))


def recover_stale_jobs(stale_after: dt.timedelta, max_attempts: int) -> int:
    """
    Requeues running jobs whose heartbeat is older than `stale_after` (their worker died);
    jobs that already used `max_attempts` fail instead, together with their docset.
    Returns the number of jobs touched.
    """
    cutoff = _now() - stale_after
    with get_engine().begin() as conn:
        stale = conn.execute(
            select(jobs.c.id, jobs.c.docset_hash, jobs.c.attempts)
            .where(jobs.c.status == JOB_RUNNING, jobs.c.heartbeat_at < cutoff)
            .with_for_update(skip_locked=True)
        ).all()
        for job_id, docset_hash, attempts in stale:
            if attempts >= max_attempts:
                error = f"Worker stopped responding {attempts} times while processing this docset."
                conn.execute(update(jobs).where(jobs.c.id == job_id)
                             .values(status=JOB_FAILED, error=error, finished_at=_now()))
                conn.execute(update(docsets).where(docsets.c.hash == docset_hash)
                             .values(status=Config.STATUS_ERROR[0], error=error, updated_at=_now()))
            else:
                conn.execute(update(jobs).where(jobs.c.id == job_id).values(status=JOB_QUEUED, worker=None))
                conn.execute(update(docsets).where(docsets.c.hash == docset_hash)
                             .values(status=Config.STATUS_PENDING, error=None, updated_at=_now()))
            logger.warning(f"Recovered stale job {job_id} (docset {docset_hash}, attempt {attempts})")
    return len(stale)


def count_running_jobs() -> int:
    with get_engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(jobs).where(jobs.c.status == JOB_RUNNING)).scalar()


# --- One-off import of the file-based state that preceded the database ---

def import_file_metadata(data_root: str) -> int:
    """
    Creates docset rows from data/<hash>/metadata.json for docsets not yet in the database,
    so results computed before the switch are reused instead of recomputed. A run that was
    still in progress belonged to a thread that no longer exists, so it is imported as failed.
    Returns the count.
    """
    import os
    imported = 0
    for name in sorted(os.listdir(data_root)):
        path = os.path.join(data_root, name, "metadata.json")
        if not os.path.isfile(path) or get_docset(name):
            continue
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        status, error = meta.get("status", Config.STATUS_FINISHED[0]), meta.get("error")
        if status not in Config.STATUS_FINISHED and status not in Config.STATUS_ERROR:
            status, error = Config.STATUS_ERROR[0], "Interrupted before the switch to the job queue."
        with get_engine().begin() as conn:
            conn.execute(insert(docsets).values(
                hash=name, name=meta.get("name"), source=meta.get("source", "fraunhofer"),
                iri=meta.get("iri"), query=meta.get("query"), status=status, error=error,
            ))
        imported += 1
    return imported
