"""
Queue and worker tests: job claiming, stale-job recovery, and run_job with a fake
processor (the real pipeline and the SPECTER2 model are never touched).
"""
import datetime as dt

import pytest

import worker


class FakeProcessor:
    def __init__(self, fail=None, interrupt=False):
        self.fail, self.interrupt = fail, interrupt
        self.docset_name = None
        self.calls = []

    def process_from_openalex_query(self, status_callback, **kwargs):
        self.calls.append(kwargs)
        status_callback("computing_embeddings")
        if self.interrupt:
            try:
                raise worker.Shutdown("SIGTERM")
            except Exception:  # like the pipeline's own error handling, which must not swallow it
                pass
        if self.fail:
            raise RuntimeError(self.fail)
        self.docset_name = "Name from OpenAlex"


def _queue(db, docset_hash="a" * 32):
    params = {"search": "graphs", "raw_filter": None, "docset_name": None, "min_size": 10, "max_size": 20}
    return db.request_run(docset_hash, "openalex", params, name="graphs", source="openalex")


def _job(db, job_id):
    with db.get_engine().connect() as conn:
        return dict(conn.execute(db.jobs.select().where(db.jobs.c.id == job_id)).mappings().one())


def test_claim_takes_oldest_queued_job_once(db):
    first, second = _queue(db, "a" * 32), _queue(db, "b" * 32)
    assert db.claim_job("w1")["id"] == first
    job = db.claim_job("w2")
    assert (job["id"], job["attempts"]) == (second, 1)
    assert db.claim_job("w3") is None
    assert (_job(db, first)["status"], _job(db, first)["worker"]) == ("running", "w1")
    assert db.count_running_jobs() == 2


def test_run_job_success(db):
    _queue(db)
    job = db.claim_job("w")
    processor = FakeProcessor()
    worker.run_job(processor, job)
    assert processor.calls[0]["min_size"] == 10
    assert _job(db, job["id"])["status"] == "done"
    docset = db.get_docset("a" * 32)
    assert (docset["status"], docset["name"], docset["error"]) == ("finished", "Name from OpenAlex", None)


def test_run_job_failure_records_error(db):
    _queue(db)
    job = db.claim_job("w")
    worker.run_job(FakeProcessor(fail="no papers"), job)
    assert (_job(db, job["id"])["status"], _job(db, job["id"])["error"]) == ("failed", "no papers")
    assert (db.get_docset("a" * 32)["status"], db.get_docset("a" * 32)["error"]) == ("error", "no papers")


def test_shutdown_puts_job_back_into_queue(db):
    _queue(db)
    job = db.claim_job("w")
    with pytest.raises(worker.Shutdown):
        worker.run_job(FakeProcessor(interrupt=True), job)
    assert _job(db, job["id"])["status"] == "queued"
    assert db.get_docset("a" * 32)["status"] == "pending"
    assert db.claim_job("w2")["attempts"] == 2


def test_stale_jobs_are_requeued_then_failed(db):
    _queue(db)
    stale_after = dt.timedelta(minutes=3)
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)

    def claim_and_let_heartbeat_go_stale():
        job = db.claim_job("w")
        with db.get_engine().begin() as conn:
            conn.execute(db.jobs.update().where(db.jobs.c.id == job["id"]).values(heartbeat_at=old))
        return job

    job = claim_and_let_heartbeat_go_stale()
    assert db.recover_stale_jobs(stale_after, max_attempts=2) == 1
    assert _job(db, job["id"])["status"] == "queued"
    assert db.get_docset("a" * 32)["status"] == "pending"

    claim_and_let_heartbeat_go_stale()
    assert db.recover_stale_jobs(stale_after, max_attempts=2) == 1
    assert _job(db, job["id"])["status"] == "failed"
    assert db.get_docset("a" * 32)["status"] == "error"

    # a fresh heartbeat is left alone
    _queue(db, "b" * 32)
    db.claim_job("w")
    assert db.recover_stale_jobs(stale_after, max_attempts=2) == 0


def test_import_file_metadata(db, tmp_path):
    import json
    for name, meta in {"x" * 32: {"name": "Done", "status": "finished", "source": "openalex"},
                       "y" * 32: {"name": "Cut off", "status": "clustering"}}.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "metadata.json").write_text(json.dumps(meta))
    assert db.import_file_metadata(str(tmp_path)) == 2
    assert db.import_file_metadata(str(tmp_path)) == 0  # idempotent
    assert db.get_docset("x" * 32)["status"] == "finished"
    assert db.get_docset("y" * 32)["status"] == "error"
