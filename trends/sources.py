"""
Signals of public attention for science topics, collected daily (`python -m trends collect`) into the week's
signals.json, plus Wikipedia pageviews as a check for single candidates.

Every source is optional: a failing source is logged and skipped, and the week goes ahead with the others.
Only headlines, links, outlets and counts are stored, never user names or post texts (GDPR); headlines stay
unchanged and are always shown with their outlet and link.

Left out on purpose (2026-10): Reddit (free API and RSS end in 2026), YouTube (no science trending chart, search
quota of 100 calls a day), Google Trends (no public API), Altmetric (licence excludes public products), Crossref
Event Data (shut down), ScienceDaily (terms limit how many items may be stored), Bluesky (search needs a login).
"""

import calendar
import datetime as dt
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import quote

import feedparser
import requests

logger = logging.getLogger(__name__)

USER_AGENT = "thesis-trends/0.1 (https://github.com/jan-c-buchkremer/master-thesis)"

# science news: editorial selection, so every item counts; (outlet, feed URL)
FEEDS = [
    ("Phys.org", "https://phys.org/rss-feed/"),
    ("Nature", "https://www.nature.com/nature.rss"),
    ("Science", "https://www.science.org/rss/news_current.xml"),
    ("New Scientist", "https://www.newscientist.com/feed/home/"),
    ("Quanta Magazine", "https://www.quantamagazine.org/feed/"),
]
HN_MIN_POINTS = 100
MASTODON_INSTANCES = ["mastodon.social"]


def _get(url: str, **params) -> requests.Response:
    response = requests.get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response


def _signal(source: str, outlet: str, title: str, url: str, published: Optional[str] = None,
            summary: str = "", engagement: Optional[int] = None) -> Dict:
    return {"source": source, "outlet": outlet, "title": " ".join(title.split()), "url": url,
            "published": published or dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            "summary": " ".join((summary or "").split())[:500], "engagement": engagement}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def news_feeds() -> List[Dict]:
    signals = []
    for outlet, url in FEEDS:
        try:
            feed = feedparser.parse(_get(url).content)
        except Exception as e:
            logger.warning(f"Feed {outlet} failed: {e}")
            continue
        for entry in feed.entries:
            if not entry.get("title") or not entry.get("link"):
                continue
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            published = (dt.datetime.fromtimestamp(calendar.timegm(parsed), dt.timezone.utc).isoformat(timespec='seconds')
                         if parsed else None)
            signals.append(_signal("news", outlet, entry.title, entry.link, published,
                                   _strip_html(entry.get("summary", ""))))
    return signals


def hacker_news(days: int = 7) -> List[Dict]:
    """Stories of the last days with at least HN_MIN_POINTS points; most are not science, the LLM sorts them out."""
    since = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).timestamp())
    try:
        hits = _get("https://hn.algolia.com/api/v1/search", tags="story", hitsPerPage=60,
                    numericFilters=f"points>{HN_MIN_POINTS},created_at_i>{since}").json()["hits"]
    except Exception as e:
        logger.warning(f"Hacker News failed: {e}")
        return []
    return [_signal("social", "Hacker News", h["title"],
                    h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                    h.get("created_at"), engagement=h.get("points"))
            for h in hits if h.get("title")]


def mastodon_trends() -> List[Dict]:
    """Links trending on Mastodon instances; engagement is the number of accounts sharing them in the last days."""
    signals = []
    for instance in MASTODON_INSTANCES:
        try:
            links = _get(f"https://{instance}/api/v1/trends/links", limit=20).json()
        except Exception as e:
            logger.warning(f"Mastodon {instance} failed: {e}")
            continue
        for link in links:
            if link.get("title") and link.get("url"):
                accounts = sum(int(h.get("accounts", 0)) for h in link.get("history", []))
                signals.append(_signal("social", f"Mastodon ({link.get('provider_name') or instance})",
                                       link["title"], link["url"], summary=link.get("description", ""),
                                       engagement=accounts))
    return signals


def collect() -> List[Dict]:
    signals = news_feeds() + hacker_news() + mastodon_trends()
    logger.info(f"Collected {len(signals)} signals")
    return signals


def merge(existing: List[Dict], new: List[Dict]) -> List[Dict]:
    """Adds new signals to the week's list; a link seen again keeps its first entry and the higher engagement."""
    by_url = {s["url"]: s for s in existing}
    for s in new:
        old = by_url.get(s["url"])
        if old is None:
            by_url[s["url"]] = s
        elif (s.get("engagement") or 0) > (old.get("engagement") or 0):
            old["engagement"] = s["engagement"]
    return list(by_url.values())


def wikipedia_attention(article: str, today: Optional[dt.date] = None) -> Optional[Dict]:
    """
    Daily views of an English Wikipedia article: the last 7 days against the 8 weeks before. None if the
    article does not exist. A ratio well above 1 means the public is looking the topic up right now.
    """
    today = today or dt.date.today()
    end, start = today - dt.timedelta(days=1), today - dt.timedelta(days=63)
    title = quote(article.strip().replace(" ", "_"), safe="")
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{title}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    try:
        items = _get(url).json().get("items", [])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise
    views = [i["views"] for i in items]
    if not views:
        return None
    week, before = views[-7:], views[:-7]
    baseline = sum(before) / len(before) if before else 0
    per_day = sum(week) / len(week)
    return {"article": article, "url": f"https://en.wikipedia.org/wiki/{title}", "views_per_day": round(per_day),
            "ratio": round(per_day / baseline, 2) if baseline else None}
