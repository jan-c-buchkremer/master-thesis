"""
Docset identifiers: MD5 of the IRI, or of a canonicalized OpenAlex query.

Kept separate from DocumentSetProcessor so the web app can compute them
without importing torch and the model stack.
"""

import hashlib
import json
from typing import Dict, Optional


def hash_iri(iri: str) -> str:
    """Generates an MD5 hash from the IRI."""
    return hashlib.md5(iri.encode('utf-8')).hexdigest()


def hash_query(
        search: Optional[str] = None,
        filters: Optional[Dict[str, str]] = None,
        raw_filter: Optional[str] = None
) -> str:
    """Generates an MD5 hash from a canonicalized OpenAlex query, mirroring hash_iri."""
    canonical = json.dumps(
        {"search": search, "filters": filters or {}, "raw_filter": raw_filter},
        sort_keys=True
    )
    return hashlib.md5(canonical.encode('utf-8')).hexdigest()
