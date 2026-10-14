"""
Command line of the weekly run.

    python -m trends collect            add today's signals to the week (daily timer)
    python -m trends propose            candidate topics from the week's signals, ranked by attention
    python -m trends build              for the best candidates: read the coverage, find the paper, refine the query
    python -m trends queue              queue the built topics as docsets for the worker
    python -m trends write              wait for the worker, write the texts, publish the week
    python -m trends weekly             collect, propose, build, queue and write (weekly timer)

--week 2026-W42 works on another week than the current one. `probe` and `paper` are the research agent's tools
(tools.py).
"""

import argparse
import logging
import os
import sys

from Config import Config
from trends import query, runs, select, sources, store, write

logger = logging.getLogger("trends")


def collect(week: str):
    path = os.path.join(store.week_dir(week), 'signals.json')
    signals = sources.merge(store.load_json(path, []), sources.collect())
    store.save_json(path, signals)
    logger.info(f"{week}: {len(signals)} signals")


def propose(week: str):
    signals = store.load_json(os.path.join(store.week_dir(week), 'signals.json'), [])
    if not signals:
        sys.exit(f"{week}: no signals, run collect first")
    candidates = select.rank(select.propose(signals, store.recent_featured(week)))
    store.save_json(os.path.join(store.week_dir(week), 'candidates.json'), candidates)
    for c in candidates:
        logger.info(f"  {c['score']:>5}  {c['title']} ({len(c['signals'])} signals from "
                    f"{len({s['outlet'] for s in c['signals']})} outlets)")


def build(week: str, tries: int = 6):
    """Builds the best candidates' queries until TRENDS_TOPICS of them fit; a candidate without one is skipped."""
    candidates = store.load_json(os.path.join(store.week_dir(week), 'candidates.json'))
    if candidates is None:
        sys.exit(f"{week}: no candidates, run propose first")
    built = []
    for candidate in candidates[:tries]:
        logger.info(f"Building {candidate['title']!r}")
        topic = query.build(candidate)
        if topic:
            built.append(topic)
            logger.info(f"{topic['title']}: {topic['probe']['count']} works, paper "
                        f"{(topic.get('anchor') or {}).get('title')!r}")
            if len(built) == Config.TRENDS_TOPICS:
                break
    if not built:
        sys.exit(f"{week}: no candidate got a fitting query")
    store.save_json(os.path.join(store.week_dir(week), 'picked.json'), built)


def queue(week: str):
    picked = store.load_json(os.path.join(store.week_dir(week), 'picked.json'))
    if picked is None:
        sys.exit(f"{week}: nothing built, run build first")
    for topic in picked:
        runs.queue(topic)
    store.save_json(os.path.join(store.week_dir(week), 'picked.json'), picked)


def write_week(week: str, wait_hours: float = None):
    picked = store.load_json(os.path.join(store.week_dir(week), 'picked.json'))
    if picked is None:
        sys.exit(f"{week}: nothing queued, run queue first")
    statuses = runs.wait([t['docset'] for t in picked], wait_hours)
    topics = []
    for topic in picked:
        if statuses[topic['docset']] not in Config.STATUS_FINISHED:
            logger.warning(f"{topic['title']}: docset {topic['docset']} is {statuses[topic['docset']]}, left out")
            continue
        page_topic = write.write_topic(topic)
        if page_topic:
            topics.append(page_topic)
    if not topics:
        sys.exit(f"{week}: no topic finished, the landing page keeps the previous week")
    logger.info(f"{week}: published {len(topics)} topic(s) to {write.publish(week, topics)}")


def main():
    if sys.argv[1:2] in (["probe"], ["paper"]):
        from trends import tools
        return tools.main(sys.argv[1:])
    parser = argparse.ArgumentParser(prog="python -m trends", description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["collect", "propose", "build", "queue", "write", "weekly"])
    parser.add_argument("--week", default=store.iso_week(), help="ISO week, e.g. 2026-W42 (default: this week)")
    parser.add_argument("--wait-hours", type=float, help="how long write waits for the worker (default TRENDS_WAIT_HOURS)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    if args.step in ("collect", "weekly"):
        collect(args.week)
    if args.step in ("propose", "weekly"):
        propose(args.week)
    if args.step in ("build", "weekly"):
        build(args.week)
    if args.step in ("queue", "weekly"):
        queue(args.week)
    if args.step in ("write", "weekly"):
        write_week(args.week, args.wait_hours)


if __name__ == "__main__":
    main()
