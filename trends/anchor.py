"""
Finding the paper behind a topic's coverage (the anchor): the DOI in a journal's own URL, and a search of this
year's papers by the terms they must contain. The research agent (query.py) uses these through
`python -m trends paper`.
"""

import datetime as dt
import logging
import re
from typing import Dict, List, Optional

from Config import Config
from OpenAlexDataHandler import _api_get
from trends import openalex

logger = logging.getLogger(__name__)

# journals' own article URLs carry the DOI (nature.com/articles/s41586-..., science.org/doi/10....)
URL_DOI = [(re.compile(r'nature\.com/articles/(s\d{5}-\d{3}-\d{5}-\w)'), "10.1038/{}"),
           (re.compile(r'science\.org/doi/(10\.\d{4,9}/[^\s?#]+)'), "{}")]
DAYS_BACK = 365


def doi_from_url(url: str) -> Optional[str]:
    for pattern, template in URL_DOI:
        match = pattern.search(url)
        if match:
            return template.format(match.group(1))
    return None


def search_recent(must: List[List[str]], n: int = 25) -> List[Dict]:
    """This year's articles and preprints containing a term of every group, newest first."""
    since = dt.date.today() - dt.timedelta(days=DAYS_BACK)
    clauses = [openalex.build_filter({"all_of": must}).split(",publication_year:")[0],
               f"from_publication_date:{since}", "type:article|preprint"]
    data = _api_get("/works", {"filter": ",".join(clauses), "per-page": n, "sort": "publication_date:desc",
                               "select": openalex.WORK_FIELDS}, Config.OPENALEX_API_KEY)
    return [openalex._work(w) for w in data.get("results", [])]


def from_urls(signals: List[Dict]) -> List[Dict]:
    """Papers whose DOI is in the URL of a news item (journals' own pages)."""
    works = []
    for s in signals:
        doi = doi_from_url(s['url'])
        work = openalex.work_by_doi(doi) if doi else None
        if work is None and s['outlet'] == 'Nature' and '/s4' in s['url']:
            work = openalex.work_by_title(s['title'])  # a Nature paper OpenAlex has not linked to its DOI yet
        if work:
            works.append(work)
    return works
