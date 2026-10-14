"""Queues the picked topics as OpenAlex docsets for the worker and waits for them."""

import logging
import os
import time
from typing import Dict, List

import Database
import DocsetHash
from Config import Config
from trends import store

logger = logging.getLogger(__name__)


def has_result(docset_hash: str) -> bool:
    folder = os.path.join(store.data_dir(), docset_hash)
    return all(os.path.exists(os.path.join(folder, f"{docset_hash}_{part}.json")) for part in ("docset", "topics"))


def queue(topic: Dict) -> str:
    """
    Queues a pipeline run for the topic's refined query (as /start_openalex does, with the whole query as its
    filter) and returns the docset hash. A docset that is already processed, queued or running is reused, so
    re-running the week does not compute it twice.
    """
    search, raw_filter = None, topic['probe']['filter']
    docset_hash = DocsetHash.hash_query(search=search, raw_filter=raw_filter)
    topic['docset'] = docset_hash
    topic['query'] = {'search': search, 'raw_filter': raw_filter}
    existing = Database.get_docset(docset_hash)
    if existing and existing['status'] in Config.STATUS_FINISHED and has_result(docset_hash):
        logger.info(f"{topic['title']}: docset {docset_hash} already processed")
        return docset_hash
    job = Database.request_run(
        docset_hash, "openalex",
        {"search": search, "raw_filter": raw_filter, "docset_name": topic['title'],
         "min_size": Config.TRENDS_DOCSET_MIN_SIZE, "max_size": Config.TRENDS_DOCSET_MAX_SIZE},
        name=topic['title'], source="openalex",
        query={"search": search, "filters": None, "raw_filter": raw_filter},
    )
    logger.info(f"{topic['title']}: " + (f"queued job {job}" if job else "already queued or running")
                + f" for docset {docset_hash}")
    return docset_hash


def wait(docset_hashes: List[str], hours: float = None, poll_seconds: int = 60) -> Dict[str, str]:
    """Waits until every docset is finished or failed, or the time is up; returns each docset's status."""
    deadline = time.monotonic() + 3600 * (Config.TRENDS_WAIT_HOURS if hours is None else hours)
    while True:
        statuses = {h: (Database.get_docset(h) or {}).get('status', 'missing') for h in docset_hashes}
        open_ = [h for h, s in statuses.items() if s not in Config.STATUS_FINISHED + Config.STATUS_ERROR]
        if not open_ or time.monotonic() >= deadline:
            if open_:
                logger.warning(f"Gave up waiting for {len(open_)} docset(s): {open_}")
            return statuses
        logger.info(f"Waiting for {len(open_)} docset(s): " + ", ".join(f"{h[:8]} {statuses[h]}" for h in open_))
        time.sleep(poll_seconds)
