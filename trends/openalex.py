"""
OpenAlex for the weekly topics: the query format the LLM writes, probes of a query (size, years, sample titles,
whether the anchor paper and its references are in it), and paper lookups.
"""

import datetime as dt
import re
from typing import Dict, List, Optional

from Config import Config
from OpenAlexDataHandler import _api_get, _reconstruct_abstract

# article, review and preprint: preprints carry the newest work in fast fields
WORK_TYPES = "type:article|review|preprint"


def _phrase(term: str) -> str:
    term = re.sub(r'[",|]', ' ', term).strip()
    term = " ".join(term.split())
    return f'"{term}"' if " " in term or "-" in term else term


def build_filter(query: Dict) -> str:
    """
    The OpenAlex filter for a query {"all_of": [[terms], ...], "none_of": [terms], "from_year": 2018}: the terms of
    one group are alternatives (OR, `|`), the groups must all match (AND, a repeated title_and_abstract.search),
    `none_of` terms must not. Multi-word terms are searched as phrases. (A literal OR inside a search does not
    work: OpenAlex then matches far too much or too little.)
    """
    clauses = []
    for group in query.get('all_of') or []:
        terms = [_phrase(t) for t in group if t and t.strip()]
        if terms:
            clauses.append("title_and_abstract.search:" + "|".join(terms))
    for term in query.get('none_of') or []:
        if term and term.strip():
            clauses.append(f"title_and_abstract.search:!{_phrase(term)}")
    if not clauses:
        raise ValueError("query without search terms")
    this_year = dt.date.today().year
    from_year = min(max(int(query.get('from_year') or this_year - 9), this_year - 30), this_year)
    clauses.append(f"publication_year:{from_year}-{this_year}")
    clauses.append(WORK_TYPES)
    return ",".join(clauses)


def _count(filter_str: str) -> int:
    return _api_get("/works", {"filter": filter_str, "per-page": 1, "select": "id"},
                    Config.OPENALEX_API_KEY)["meta"]["count"]


def _titles(filter_str: str, sort: str, n: int) -> List[str]:
    data = _api_get("/works", {"filter": filter_str, "per-page": n, "sort": sort,
                               "select": "title,publication_year"}, Config.OPENALEX_API_KEY)
    return [f"{w.get('publication_year')}: {w.get('title')}" for w in data.get("results", []) if w.get('title')]


def yearly_counts(filter_str: str) -> Dict[str, int]:
    data = _api_get("/works", {"filter": filter_str, "group_by": "publication_year"}, Config.OPENALEX_API_KEY)
    return {g["key"]: g["count"] for g in sorted(data.get("group_by", []), key=lambda g: g["key"])}


def growth(counts: Dict[str, int], today: Optional[dt.date] = None) -> Optional[float]:
    """Works in the last two complete years over the two before (1.0 = flat); None without earlier works."""
    year = (today or dt.date.today()).year
    recent = counts.get(str(year - 1), 0) + counts.get(str(year - 2), 0)
    before = counts.get(str(year - 3), 0) + counts.get(str(year - 4), 0)
    return round(recent / before, 2) if before else None


def probe(query: Dict, anchor: Optional[Dict] = None) -> Dict:
    """What a query would give: size, works per year, the most cited and the newest titles, and for an anchor
    paper whether it is in the set and how many of its references are."""
    filter_str = build_filter(query)
    result = {"filter": filter_str, "count": _count(filter_str)}
    if result["count"]:
        result["counts"] = yearly_counts(filter_str)
        result["most_cited"] = _titles(filter_str, "cited_by_count:desc", 8)
        result["newest"] = _titles(filter_str, "publication_date:desc", 5)
    if anchor:
        short = anchor["id"].rsplit("/", 1)[-1]
        result["anchor_included"] = _count(f"{filter_str},openalex:{short}") > 0
        refs = anchor.get("referenced_works_count") or 0
        result["anchor_references"] = f"{_count(f'{filter_str},cited_by:{short}') if refs else 0} of {refs}"
    return result


WORK_FIELDS = ("id,doi,title,publication_date,publication_year,abstract_inverted_index,keywords,primary_topic,"
               "referenced_works,referenced_works_count,primary_location,cited_by_count")


def _work(raw: Dict) -> Dict:
    source = ((raw.get("primary_location") or {}).get("source") or {}).get("display_name")
    return {
        "id": raw["id"], "doi": raw.get("doi"), "title": raw.get("title"), "journal": source,
        "publication_date": raw.get("publication_date"), "publication_year": raw.get("publication_year"),
        "abstract": _reconstruct_abstract(raw.get("abstract_inverted_index")) or "",
        "keywords": [k["display_name"] for k in raw.get("keywords") or []],
        "topic": (raw.get("primary_topic") or {}).get("display_name"),
        "referenced_works": raw.get("referenced_works") or [],
        "referenced_works_count": raw.get("referenced_works_count") or 0,
    }


def work_by_doi(doi: str) -> Optional[Dict]:
    try:
        return _work(_api_get(f"/works/doi:{doi}", {"select": WORK_FIELDS}, Config.OPENALEX_API_KEY))
    except Exception:
        return None


def work_by_id(work_id: str) -> Optional[Dict]:
    try:
        return _work(_api_get(f"/works/{work_id.rsplit('/', 1)[-1]}", {"select": WORK_FIELDS}, Config.OPENALEX_API_KEY))
    except Exception:
        return None


def work_by_title(title: str) -> Optional[Dict]:
    clean = re.sub(r'[^\w\s-]', ' ', title)
    data = _api_get("/works", {"filter": f"title.search:{clean}", "per-page": 1, "sort": "publication_date:desc",
                               "select": WORK_FIELDS}, Config.OPENALEX_API_KEY)
    results = data.get("results") or []
    if not results:
        return None
    # title.search also returns near misses; only the same title counts
    words = lambda t: set(re.findall(r'\w+', (t or '').lower()))
    found, wanted = words(results[0].get("title")), words(title)
    return _work(results[0]) if wanted and len(found & wanted) / len(found | wanted) > 0.8 else None


def titles_of(work_ids: List[str], n: int = 25) -> List[str]:
    """Titles of the first n works in a list of OpenAlex ids (e.g. an anchor's references)."""
    ids = "|".join(w.rsplit("/", 1)[-1] for w in work_ids[:n])
    if not ids:
        return []
    data = _api_get("/works", {"filter": f"openalex:{ids}", "per-page": n, "select": "title"}, Config.OPENALEX_API_KEY)
    return [w["title"] for w in data.get("results", []) if w.get("title")]
