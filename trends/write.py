"""The week's page data: an introduction per topic, written from its signals and its docset's clusters."""

import datetime as dt
import logging
import os
from typing import Dict, List, Optional

import ClaudeCode
from Config import Config
from trends import store

logger = logging.getLogger(__name__)

MAX_SOURCES = 4
MAX_SUBTOPICS = 5
# a top-level cluster holding more than this share of the docset is shown by its own subdivisions
DOMINANT_SHARE = 0.6

WRITE_PROMPT = """You write for a website that introduces interested non-specialists to research fields that are in the news this week. The reader then explores the field's literature on an interactive map of its papers.

Topic: {title} ({field})

What the advance is about, from researching the coverage and the paper: {understanding}
{anchor}
This week's coverage (outlet, date, headline, summary):
{signals}

The literature: {papers} papers from {years}. They fall into these main research directions (name: description):
{clusters}

Write, in English:
- "headline": the topic as a short headline, at most 8 words, no clickbait, no question.
- "lead": 2 to 3 sentences on what the research field studies and why it matters, for a reader without a science degree.
- "why_now": 2 to 3 sentences on what happened this week, based only on the coverage and the paper above. Name the outlet when you refer to an item. Do not add facts, numbers or names that are not in the coverage or the paper. Write dates as words ("12 October"), never as 2026-10-12.
- "directions": for each research direction above, in the same order, one plain sentence of at most 20 words on what that research looks at."""

SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "lead": {"type": "string"}, "why_now": {"type": "string"},
                   "directions": {"type": "array", "items": {"type": "string"}}},
    "required": ["headline", "lead", "why_now", "directions"],
}


def top_clusters(docset_hash: str) -> (int, List[Dict]):
    """Paper count of the docset and its largest top-level clusters."""
    topics = store.load_json(os.path.join(store.data_dir(), docset_hash, f"{docset_hash}_topics.json"), [])
    root = next((t for t in topics if t.get('parent') is None), None)
    if root is None:
        return 0, []
    size = lambda t: len(t.get('papers', []))
    children_of = lambda parent: sorted((t for t in topics if t.get('parent') == parent['id']), key=size, reverse=True)
    children = children_of(root)
    # one cluster with most of the papers says little; its subdivisions are the field's main lines
    while children and size(children[0]) > DOMINANT_SHARE * size(root) and children_of(children[0]):
        children = children_of(children[0]) + children[1:]
        children.sort(key=size, reverse=True)
    return len(root.get('papers', [])), [
        {'name': t['name'], 'description': t.get('description', ''), 'papers': len(t.get('papers', [])),
         'color': t.get('color')}
        for t in children[:MAX_SUBTOPICS]
    ]


def _sources(signals: List[Dict]) -> List[Dict]:
    """The topic's coverage for the page: news before social posts, one item per outlet first."""
    ordered = sorted(signals, key=lambda s: (s['source'] != 'news', -(s.get('engagement') or 0)))
    picked, outlets = [], set()
    for s in ordered:
        if s['outlet'] not in outlets:
            picked.append(s)
            outlets.add(s['outlet'])
    picked += [s for s in ordered if s not in picked]
    picked = picked[:MAX_SOURCES]
    return [{k: s.get(k) for k in ('title', 'outlet', 'url', 'published')} for s in picked]


def write_topic(topic: Dict) -> Optional[Dict]:
    """The topic as shown on the landing page, or None if its docset has no clusters."""
    papers, clusters = top_clusters(topic['docset'])
    if not clusters:
        logger.warning(f"{topic['title']}: docset {topic['docset']} has no clusters, left out")
        return None
    years = sorted(int(y) for y, n in (topic.get('counts') or {}).items() if n)
    paper = topic.get('anchor')
    prompt = WRITE_PROMPT.format(
        title=topic['title'], field=topic.get('field', ''), understanding=topic.get('understanding', ''),
        anchor=f"The paper: {paper['title']} ({paper.get('journal') or 'journal unknown'}, {paper.get('publication_date')})\n"
        if paper else "",
        signals="\n".join(f"- {s['outlet']}, {(s.get('published') or '')[:10]}: {s['title']}"
                          + (f" | {s['summary'][:300]}" if s.get('summary') else "") for s in topic['signals'][:12]),
        papers=papers, years=f"{years[0]} to {years[-1]}" if years else "recent years",
        clusters="\n".join(f"- {c['name']}: {c['description'][:400]}" for c in clusters),
    )
    text = ClaudeCode.ask_json(prompt, SCHEMA, Config.TRENDS_LLM_MODEL)
    directions = text.get('directions') or []
    for c, sentence in zip(clusters, directions):
        c['summary'] = str(sentence).strip()
    return {
        'title': topic['title'],
        'field': topic.get('field', ''),
        'headline': str(text.get('headline') or topic['title']).strip(),
        'lead': str(text.get('lead', '')).strip(),
        'why_now': str(text.get('why_now', '')).strip(),
        'docset': topic['docset'],
        'query': topic['query'],
        'papers': papers,
        'counts': topic.get('counts', {}),
        'growth': topic.get('growth'),
        'paper': paper,
        'wikipedia': topic.get('wikipedia_views'),
        'directions': [{k: c.get(k) for k in ('name', 'summary', 'papers', 'color')} for c in clusters],
        'sources': _sources(topic['signals']),
    }


def publish(week: str, topics: List[Dict]) -> str:
    """Writes the week (featured/<week>.json) and makes it the landing page's (featured/current.json)."""
    page = {'week': week, 'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'topics': topics}
    path = os.path.join(store.featured_dir(), f"{week}.json")
    store.save_json(path, page)
    store.save_json(os.path.join(store.featured_dir(), 'current.json'), page)
    return path
