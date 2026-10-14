"""
The research agent's tools (query.py), as commands it may run:

    python -m trends probe '<query JSON>' [--anchor W123]   what a query finds in OpenAlex
    python -m trends paper --doi 10.1038/...                a paper by DOI
    python -m trends paper --title "..."                    a paper by its exact title
    python -m trends paper --must "a|b" --must "c|d"        this year's papers containing a term of every group

Each prints JSON for the agent to read.
"""

import argparse
import json
import sys

from trends import anchor, openalex


def _paper(w):
    return {"id": w["id"].rsplit("/", 1)[-1], "doi": w.get("doi"), "title": w["title"], "journal": w.get("journal"),
            "date": w.get("publication_date"), "keywords": w.get("keywords"), "topic": w.get("topic"),
            "references": w.get("referenced_works_count"), "abstract": w["abstract"][:1200]}


def main(argv):
    parser = argparse.ArgumentParser(prog="python -m trends")
    sub = parser.add_subparsers(dest="tool", required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("query", help='{"all_of": [["term", "synonym"], ["term"]], "none_of": [], "from_year": 2018}')
    probe.add_argument("--anchor", help="OpenAlex id of the paper that must be in the set")
    paper = sub.add_parser("paper")
    paper.add_argument("--doi")
    paper.add_argument("--title")
    paper.add_argument("--must", action="append", help="terms separated by |; repeat for every group")
    args = parser.parse_args(argv)

    if args.tool == "probe":
        work = openalex.work_by_id(args.anchor) if args.anchor else None
        if args.anchor and not work:
            sys.exit(f"unknown OpenAlex id {args.anchor}")
        result = openalex.probe(json.loads(args.query), work)
        result.pop("filter", None)
    elif args.doi:
        work = openalex.work_by_doi(args.doi.removeprefix("https://doi.org/"))
        result = _paper(work) if work else "not found"
    elif args.title:
        work = openalex.work_by_title(args.title)
        result = _paper(work) if work else "not found"
    elif args.must:
        works = anchor.search_recent([group.split("|") for group in args.must])
        result = [{k: v for k, v in _paper(w).items() if k in ("id", "title", "journal", "date")}
                  | {"abstract": w["abstract"][:300]} for w in works] or "nothing found"
    else:
        parser.error("paper needs --doi, --title or --must")
    print(json.dumps(result, ensure_ascii=False, indent=1))
