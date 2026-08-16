"""
OpenAlex Data Handler Module

Fetches a docset from the OpenAlex API instead of the Fraunhofer SPARQL store.
A docset here is defined by a search query plus optional filters (mirroring
"search + filter" in the OpenAlex UI), not a pre-curated IRI.

Search matches against title+abstract only (OpenAlex's `title_and_abstract.search`
filter), not full-text - matches what the OpenAlex website itself does, and avoids
the huge relevance dilution full-text matching causes for short, common-word queries.

Output shape matches DataHandler.fetch_docset_metadata: a DataFrame with
'paper', 'title', 'abstract', 'fromYear', 'hasTimesCited', 'PublicationRef'
(internal citation edges only) plus Author/Organization/Journal, so the rest
of the pipeline (Analysis.py, TopicModeling.py) needs no changes.

Docset sizing targets the INTERNALLY-CONNECTED core, not the raw candidate pool:
a paper with zero internal citation links gets dropped by Analysis.filter_connected_component
downstream regardless, so sizing decisions here are made against the connected subset.
Below DOCSET_MIN_SIZE connected papers, more are pulled in via a one-hop citation
snowball. Above DOCSET_MAX_SIZE, papers are trimmed via iterative connectivity
peeling (see _trim_by_connectivity).
"""

import os
import logging
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

OPENALEX_BASE_URL = "https://api.openalex.org"
DEFAULT_MIN_SIZE = 500
DEFAULT_MAX_SIZE = 5000
CANDIDATE_POOL_CAP = 10000  # OpenAlex's own cursor-pagination practical ceiling for a single query


def fetch_docset_by_query(
        search: Optional[str],
        filters: Optional[Dict[str, str]] = None,
        raw_filter: Optional[str] = None,
        api_key: Optional[str] = None,
        min_size: int = DEFAULT_MIN_SIZE,
        max_size: int = DEFAULT_MAX_SIZE,
        candidate_cap: int = CANDIDATE_POOL_CAP,
) -> Tuple[pd.DataFrame, str]:
    """
    Fetches and sizes a docset from OpenAlex.

    `search` is a free-text query, matched against title+abstract only (see module
    docstring). `filters` is a dict of OpenAlex filter fields to values (e.g.
    {"publication_year": "2020-2024"}), joined the same way the OpenAlex UI's "API"
    link would. `raw_filter` lets you pass an already-built OpenAlex filter string
    directly (e.g. copied from the OpenAlex website); it's combined with `search`
    rather than replacing it.

    Returns (DataFrame, docset_name).
    """
    api_key = api_key or os.getenv("OPENALEX_API_KEY")
    if not api_key:
        raise ValueError(
            "An OpenAlex API key is required (OpenAlex requires a key for all "
            "non-trivial usage as of Feb 2026). Set OPENALEX_API_KEY in .env."
        )

    filter_str = _build_combined_filter(search, filters, raw_filter)
    if not filter_str:
        raise ValueError("At least one of `search`, `filters`, or `raw_filter` is required.")

    logger.info(f"Querying OpenAlex: filter={filter_str!r}")

    try:
        works, total_available = _fetch_candidates(filter_str, api_key, hard_cap=candidate_cap)
    except requests.RequestException as e:
        detail = _extract_error_detail(e)
        logger.error(f"OpenAlex query failed: {detail}", exc_info=True)
        raise RuntimeError(f"OpenAlex rejected this query: {detail}") from e

    if total_available and total_available > candidate_cap:
        logger.warning(
            f"Query matched {total_available} papers; only the top {candidate_cap} by relevance "
            f"were fetched as the candidate pool. Narrow `filters`/`search` if this cap is a concern."
        )

    if not works:
        logger.warning("OpenAlex query returned no results.")
        return pd.DataFrame(), search or filter_str or "OpenAlex Docset"

    df = pd.DataFrame([_work_to_row(w) for w in works]).drop_duplicates(subset="paper").reset_index(drop=True)
    logger.info(f"Fetched {len(df)} candidate papers from OpenAlex.")

    # NOTE: PublicationRef stays RAW (unrestricted external references) through pad/trim -
    # _pad_with_snowball needs the external references to find snowball candidates, and
    # _connected_ids()/_trim_by_connectivity() both check membership inline, so neither
    # needs the column pre-filtered. Only restrict once, right before returning.
    connected_count = len(_connected_ids(df))
    logger.info(f"{connected_count}/{len(df)} candidates share an internal citation edge with another candidate.")

    if connected_count < min_size:
        df = _pad_with_snowball(df, min_size - connected_count, api_key)
        # Padding targets connectivity, not raw size - if the original pool was already
        # large-but-sparse, adding papers can push the raw total past max_size. Trimming
        # afterward is safe: peeling favors well-connected papers, which the newly padded
        # ones guaranteed-are, so it preferentially cuts back into the sparse original pool.
        if len(df) > max_size:
            df = _trim_by_connectivity(df, max_size)
    elif len(df) > max_size:
        df = _trim_by_connectivity(df, max_size)

    # Hand back only the internally-connected core: Analysis.filter_connected_component
    # would drop the rest downstream anyway, and this keeps the size we report accurate.
    df = _restrict_to_internal_citations(df)
    final_connected = _connected_ids(df)
    dropped_disconnected = len(df) - len(final_connected)
    if dropped_disconnected:
        logger.info(f"Dropping {dropped_disconnected} candidates with no internal citation link.")
    df = df[df["paper"].isin(final_connected)].reset_index(drop=True)

    if len(df) < min_size:
        logger.warning(
            f"Final connected docset has only {len(df)} papers, below the target minimum of "
            f"{min_size}. This query's citation neighborhood is smaller than requested."
        )
    logger.info(f"Final docset size: {len(df)} papers.")

    docset_name = search or filter_str or "OpenAlex Docset"
    return df, docset_name


# --- Fetching helpers ---

def _api_get(path: str, params: dict, api_key: str, timeout: int = 30) -> dict:
    params = dict(params)
    params["api_key"] = api_key
    response = requests.get(f"{OPENALEX_BASE_URL}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _extract_error_detail(e: requests.RequestException) -> str:
    """Pulls OpenAlex's own {"error", "message"} body out of a failed request, if present."""
    response = getattr(e, "response", None)
    if response is not None:
        try:
            body = response.json()
            message = body.get("message")
            if message:
                return message
        except ValueError:
            pass
    return str(e)


def _fetch_candidates(
        filter_str: str,
        api_key: str,
        per_page: int = 200,
        hard_cap: int = CANDIDATE_POOL_CAP,
) -> Tuple[List[dict], Optional[int]]:
    """Cursor-paginates /works until `hard_cap` results are collected or results run out."""
    cursor = "*"
    works: List[dict] = []
    total_count = None

    while cursor and len(works) < hard_cap:
        params = {"per-page": per_page, "cursor": cursor, "filter": filter_str}
        data = _api_get("/works", params, api_key)
        if total_count is None:
            total_count = (data.get("meta") or {}).get("count")

        results = data.get("results", [])
        if not results:
            break

        works.extend(results[: hard_cap - len(works)])
        cursor = (data.get("meta") or {}).get("next_cursor")
        if len(results) < per_page:
            break

    return works, total_count


def _fetch_works_by_ids(ids: List[str], api_key: str, batch_size: int = 50) -> List[dict]:
    """Batch-fetches full work objects for a list of OpenAlex work IDs."""
    results: List[dict] = []
    short_ids = [_short_id(i) for i in ids if i]

    for i in range(0, len(short_ids), batch_size):
        chunk = short_ids[i:i + batch_size]
        filter_str = "openalex_id:" + "|".join(chunk)
        try:
            data = _api_get("/works", {"filter": filter_str, "per-page": batch_size}, api_key)
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch a batch of {len(chunk)} referenced works: {e}")
            continue
        results.extend(data.get("results", []))

    return results


# --- Sizing heuristics ---

def _pad_with_snowball(df: pd.DataFrame, needed: int, api_key: str) -> pd.DataFrame:
    """
    Pulls in `needed` more papers, chosen as those most frequently referenced BY
    the current candidate set (one-hop citation snowball), ranked by how many of
    the current papers cite them. Keeps additions topically relevant (unlike
    globally-most-cited padding) and - crucially - each addition is guaranteed to
    have at least one internal citation edge to an existing paper, so this directly
    grows the connected core rather than just the raw candidate count.
    """
    if needed <= 0:
        return df

    existing_ids = set(df["paper"])
    reference_frequency = Counter()
    for refs in df["PublicationRef"]:
        for ref_id in (refs or []):
            if ref_id not in existing_ids:
                reference_frequency[ref_id] += 1

    if not reference_frequency:
        logger.warning(
            f"Need {needed} more connected papers but found no outbound references to "
            f"snowball from. Returning the current set as-is."
        )
        return df

    ranked_candidate_ids = [pid for pid, _ in reference_frequency.most_common()]
    # Fetch some extra headroom in case a few candidates are retracted/unfetchable.
    fetch_ids = ranked_candidate_ids[:needed * 2]
    fetched_works = _fetch_works_by_ids(fetch_ids, api_key)

    pad_rows = [_work_to_row(w) for w in fetched_works if w.get("id") not in existing_ids]
    if not pad_rows:
        logger.warning("Snowball padding found candidate IDs but could not fetch their metadata.")
        return df

    pad_df = pd.DataFrame(pad_rows).drop_duplicates(subset="paper")
    rank_of = {pid: rank for rank, pid in enumerate(ranked_candidate_ids)}
    pad_df["_rank"] = pad_df["paper"].map(rank_of)
    pad_df = pad_df.sort_values("_rank").head(needed).drop(columns="_rank")

    combined = pd.concat([df, pad_df], ignore_index=True)
    if len(pad_df) < needed:
        logger.warning(
            f"Could only find {len(pad_df)}/{needed} additional connected papers via snowball; "
            f"the citation neighborhood around this query is smaller than requested."
        )
    else:
        logger.info(f"Padded docset from {len(df)} to {len(combined)} papers via one-hop citation snowball.")
    return combined


def _trim_by_connectivity(
        df: pd.DataFrame,
        max_size: int,
        internal_weight: float = 0.6,
        citation_weight: float = 0.4,
        peel_batch_fraction: float = 0.05,
) -> pd.DataFrame:
    """
    Above `max_size`: iteratively peel away the worst-connected/least-cited papers,
    RE-SCORING after each round against only the papers still remaining (k-core-style
    peeling). A one-shot "score once, cut once" doesn't work here: a paper's score
    depends on neighbors that might themselves get cut in the same pass, so a static
    cut can leave the survivors far sparser than their pre-cut scores implied - e.g. a
    node scored highly because 5 other candidates cite it, all 5 of which also get cut,
    leaves it isolated post-cut despite its "good" score. Peeling in batches (instead of
    one node at a time) keeps this fast on pools up to CANDIDATE_POOL_CAP.

    Degree here is undirected (citing OR cited counts, like _connected_ids/
    Analysis.filter_connected_component), computed from the raw, unrestricted
    PublicationRef column - see the note in fetch_docset_by_query.
    """
    original_size = len(df)
    if original_size <= max_size:
        return df

    citation_rank = pd.to_numeric(
        df.set_index("paper")["hasTimesCited"], errors="coerce"
    ).fillna(0).rank(pct=True).to_dict()

    adjacency = defaultdict(set)
    for paper, refs in zip(df["paper"], df["PublicationRef"]):
        for ref_id in (refs or []):
            if ref_id != paper and ref_id in citation_rank:
                adjacency[paper].add(ref_id)
                adjacency[ref_id].add(paper)

    remaining = set(df["paper"])
    while len(remaining) > max_size:
        degrees = {p: len(adjacency[p] & remaining) for p in remaining}
        max_degree = max(degrees.values(), default=0)
        scores = {
            p: internal_weight * (degrees[p] / max_degree if max_degree else 0.0)
               + citation_weight * citation_rank.get(p, 0.0)
            for p in remaining
        }
        overshoot = len(remaining) - max_size
        batch_size = max(1, min(overshoot, int(len(remaining) * peel_batch_fraction)))
        worst = sorted(remaining, key=lambda p: scores[p])[:batch_size]
        remaining.difference_update(worst)

    kept = df[df["paper"].isin(remaining)].reset_index(drop=True)
    logger.info(
        f"Trimmed docset from {original_size} to {len(kept)} papers via iterative connectivity "
        f"peeling (weights: internal_degree={internal_weight}, citations={citation_weight})."
    )
    return kept


def _restrict_to_internal_citations(df: pd.DataFrame) -> pd.DataFrame:
    """Keeps only citation edges pointing at papers that are actually in the final docset."""
    final_ids = set(df["paper"])
    df = df.copy()
    df["PublicationRef"] = df["PublicationRef"].apply(
        lambda refs: [r for r in (refs or []) if r in final_ids]
    )
    return df


def _connected_ids(df: pd.DataFrame) -> set:
    """
    IDs of papers with at least one citation edge (citing or cited, either direction)
    to another paper in `df` - mirrors Analysis.filter_connected_component's definition
    exactly, so this can be used to preview what that downstream step will keep.
    Works against either raw or already-internal-restricted PublicationRef, since
    membership is checked inline against `df`'s own paper IDs either way.
    """
    ids = set(df["paper"])
    connected = set()
    for paper, refs in zip(df["paper"], df["PublicationRef"]):
        for ref_id in (refs or []):
            if ref_id in ids and ref_id != paper:
                connected.add(paper)
                connected.add(ref_id)
    return connected


# --- Field mapping helpers ---

def _work_to_row(work: dict) -> dict:
    authorships = work.get("authorships") or []
    authors = [
        a["author"]["display_name"]
        for a in authorships
        if a.get("author") and a["author"].get("display_name")
    ]
    organizations = list(dict.fromkeys(
        inst["display_name"]
        for a in authorships
        for inst in (a.get("institutions") or [])
        if inst.get("display_name")
    ))
    source = (work.get("primary_location") or {}).get("source") or {}
    referenced_works = work.get("referenced_works") or []

    return {
        "paper": work.get("id"),
        "title": work.get("title") or work.get("display_name"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "doi": work.get("doi"),
        "fromYear": work.get("publication_year"),
        "hasTimesCited": work.get("cited_by_count", 0),
        "referenceCount": len(referenced_works),
        "authorCount": len(authors),
        "open_access": (work.get("open_access") or {}).get("is_oa"),
        "url": work.get("id"),
        "Author": authors,
        "Organization": organizations,
        "Journal": [source["display_name"]] if source.get("display_name") else [],
        "PublicationRef": referenced_works,
    }


def _reconstruct_abstract(inverted_index: Optional[dict]) -> Optional[str]:
    """OpenAlex ships abstracts as {word: [positions]}; rebuild the plain text."""
    if not inverted_index:
        return None
    positions = {}
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions[idx] = word
    if not positions:
        return None
    return " ".join(positions[i] for i in sorted(positions))


def _build_filter_string(filters: Optional[Dict[str, str]]) -> Optional[str]:
    if not filters:
        return None
    return ",".join(f"{key}:{value}" for key, value in filters.items())


def _build_combined_filter(
        search: Optional[str],
        filters: Optional[Dict[str, str]],
        raw_filter: Optional[str],
) -> Optional[str]:
    """
    Folds `search` into the filter string as a title_and_abstract.search clause
    (matches title+abstract only, NOT full-text - full-text matching against short,
    common-word queries dilutes relevance enormously; e.g. "Science Mapping
    Visualization Tool" matches ~718k works via full-text vs ~2.1k via title+abstract,
    the latter matching what openalex.org's own search shows). `raw_filter`/`filters`
    are appended as additional AND-ed clauses, not a replacement for `search`.
    """
    clauses = []
    if search:
        # OpenAlex rejects '*'/'?' wildcards against the stemmed `.search` field with a
        # hard 400 error - the unstemmed `.search.exact` field is required for wildcards
        # to work at all (loses stemming for the whole clause, not just the wildcarded term).
        field = "title_and_abstract.search.exact" if _has_wildcard(search) else "title_and_abstract.search"
        clauses.append(f"{field}:{search}")
    if raw_filter:
        clauses.append(raw_filter)
    else:
        built = _build_filter_string(filters)
        if built:
            clauses.append(built)
    return ",".join(clauses) if clauses else None


def _has_wildcard(search: str) -> bool:
    return "*" in search or "?" in search


def _short_id(full_id: str) -> str:
    """OpenAlex IDs are full URLs (https://openalex.org/W123); filters want the short form."""
    return full_id.rsplit("/", 1)[-1] if full_id else full_id
