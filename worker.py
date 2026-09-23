"""
Pipeline worker: takes queued jobs from the database (see Database.py) and runs
them through DocumentSetProcessor, one at a time. Start several workers to
process several docsets in parallel; each loads its own copy of the model.

    python worker.py

On SIGTERM/SIGINT a running job is put back into the queue, so a restart only
costs the progress of that run. If the worker is killed outright, its job's
heartbeat goes stale and another (or the restarted) worker picks it up again.
"""

import datetime as dt
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path

from Config import Config
import Database
from DocumentSetProcessor import DocumentSetProcessor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("worker")

BASE_MODEL_DIR = "models/specter2_base_model"
ADAPTER_DIR = "models/specter2_adapter"
# Touched while the worker is alive; the container healthcheck looks at its age
ALIVE_FILE = Path(os.getenv("WORKER_ALIVE_FILE", "/tmp/worker-alive"))


class Shutdown(BaseException):
    """
    Raised in the main thread by the signal handler. A BaseException (like KeyboardInterrupt),
    so the pipeline's own `except Exception` handlers do not swallow it.
    """


def _on_signal(signum, _frame):
    raise Shutdown(signal.Signals(signum).name)


def ensure_models():
    """Downloads the SPECTER2 base model and adapter on first start (see SaveModel.py)."""
    if os.path.isfile(f"{BASE_MODEL_DIR}/config.json") and os.path.isfile(f"{ADAPTER_DIR}/adapter_config.json"):
        return
    from huggingface_hub import snapshot_download
    logger.info("SPECTER2 models not found, downloading...")
    snapshot_download(repo_id="allenai/specter2_base", local_dir=BASE_MODEL_DIR)
    snapshot_download(repo_id="allenai/specter2", local_dir=ADAPTER_DIR)


def touch_alive():
    ALIVE_FILE.touch()


def _heartbeat_loop(job_id, stop):
    """Renews the job's heartbeat until `stop` is set."""
    while not stop.wait(Config.WORKER_HEARTBEAT_SECONDS):
        try:
            Database.heartbeat(job_id)
            touch_alive()
        except Exception as e:
            logger.warning(f"Heartbeat for job {job_id} failed: {e}")


def run_job(processor, job):
    docset_hash, params = job["docset_hash"], job["params"]
    logger.info(f"Job {job['id']} (attempt {job['attempts']}): {job['kind']} run for docset {docset_hash}")

    def update_status(new_status):
        Database.set_docset_status(docset_hash, new_status)
        logger.info(f"Docset {docset_hash} status updated to: {new_status}")

    stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(job["id"], stop), daemon=True).start()
    try:
        update_status(Config.STATUS_RUNNING)
        if job["kind"] == "iri":
            processor.process_from_iri(
                docset_iri=params["docset_iri"],
                docset_name=params["docset_iri"],
                status_callback=update_status,
            )
        elif job["kind"] == "openalex":
            processor.process_from_openalex_query(
                search=params.get("search"),
                raw_filter=params.get("raw_filter"),
                docset_name=params.get("docset_name"),
                status_callback=update_status,
                min_size=params["min_size"],
                max_size=params["max_size"],
            )
        else:
            raise ValueError(f"Unknown job kind: {job['kind']}")
        # the pipeline may have found a better name (SPARQL docset name, OpenAlex query)
        if processor.docset_name:
            Database.set_docset_name(docset_hash, processor.docset_name)
        update_status(Config.STATUS_FINISHED[0])
        Database.finish_job(job["id"])
        logger.info(f"Job {job['id']}: docset {docset_hash} finished.")
    except Shutdown:
        logger.warning(f"Job {job['id']}: shutting down, putting it back into the queue.")
        Database.requeue_job(job["id"])
        Database.set_docset_status(docset_hash, Config.STATUS_PENDING)
        raise
    except Exception as e:
        logger.error(f"Job {job['id']}: docset {docset_hash} failed: {e}", exc_info=True)
        Database.set_docset_status(docset_hash, Config.STATUS_ERROR[0], str(e))
        Database.finish_job(job["id"], error=str(e))
    finally:
        stop.set()


def main():
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    try:
        ensure_models()
        processor = DocumentSetProcessor(base_model_dir=BASE_MODEL_DIR, adapter_dir=ADAPTER_DIR)
        if processor.model is None:
            raise SystemExit("SPECTER2 model could not be loaded")
        logger.info(f"Worker {worker_id} ready, waiting for jobs.")
        stale_after = dt.timedelta(seconds=Config.WORKER_STALE_SECONDS)
        while True:
            touch_alive()
            Database.recover_stale_jobs(stale_after, Config.WORKER_MAX_ATTEMPTS)
            job = Database.claim_job(worker_id)
            if job is None:
                time.sleep(Config.WORKER_POLL_SECONDS)
                continue
            run_job(processor, job)
    except Shutdown as s:
        logger.info(f"Worker {worker_id} stopped ({s}).")


if __name__ == "__main__":
    main()
