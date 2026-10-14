"""From signals to candidate topics: an LLM groups the week's coverage into research topics, ranked by attention."""

import logging
from typing import Dict, List

import ClaudeCode
from Config import Config
from trends import sources

logger = logging.getLogger(__name__)

PROPOSE_PROMPT = """You select research topics for a website that introduces the public to science that is in the news this week.

Below are this week's headlines and posts from science news, social media and video, numbered. Find the research topics behind them: scientific advances or questions that several items are about, or that one item covers with clear public interest.

Rules:
- A topic is a research advance or question with a body of scientific literature behind it (for example "sodium-ion batteries without cobalt", "GLP-1 drugs and addiction", "water in exoplanet atmospheres"), not a single event, product, company or person.
- Prefer items that report on a new study or paper.
- Leave out politics, business and celebrity news unless the research itself is the subject.
- Leave out these topics, featured in recent weeks: {recent}
- Propose up to {count} topics, the most covered first.

For each topic give a short name, the discipline in 1 to 3 words, the title of the English Wikipedia article on the field, the numbers of the items about it, and one sentence on what the coverage is about.

Items:
{items}"""


SCHEMA = {
    "type": "object",
    "properties": {"topics": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "field": {"type": "string"}, "wikipedia": {"type": "string"},
                       "items": {"type": "array", "items": {"type": "integer"}}, "reason": {"type": "string"}},
        "required": ["title", "field", "wikipedia", "items", "reason"],
    }}},
    "required": ["topics"],
}


def format_items(signals: List[Dict]) -> str:
    lines = []
    for i, s in enumerate(signals):
        summary = (s.get('summary') or '')[:160]
        lines.append(f"[{i}] ({s['outlet']}, {s.get('published', '')[:10]}) {s['title']}" + (f" | {summary}" if summary else ""))
    return "\n".join(lines)


def propose(signals: List[Dict], recent: List[Dict], count: int = 10) -> List[Dict]:
    """Candidate topics proposed by the LLM, with the signals each is based on."""
    prompt = PROPOSE_PROMPT.format(
        recent=", ".join(t['title'] for t in recent) or "none",
        count=count,
        items=format_items(signals),
    )
    candidates = []
    for c in ClaudeCode.ask_json(prompt, SCHEMA, Config.TRENDS_LLM_MODEL, timeout=900)['topics']:
        if not isinstance(c, dict) or not c.get('title'):
            continue
        ids = [i for i in c.get('items', []) if isinstance(i, int) and 0 <= i < len(signals)]
        if not ids:
            continue
        candidates.append({
            'title': str(c['title']).strip(),
            'field': str(c.get('field', '')).strip(),
            'reason': str(c.get('reason', '')).strip(),
            'wikipedia': str(c.get('wikipedia', '')).strip(),
            'signals': [signals[i] for i in ids],
        })
    return candidates


def score(candidate: Dict) -> float:
    """
    Attention: the outlets covering the topic (breadth weighs more than many items from one feed), plus a bonus
    when the field's Wikipedia article gets more views than usual.
    """
    outlets = {s['outlet'] for s in candidate['signals']}
    attention = len(outlets) + 0.25 * len(candidate['signals'])
    ratio = (candidate.get('wikipedia_views') or {}).get('ratio')
    if ratio and ratio > 1:
        attention += min(ratio - 1, 3.0)
    return round(attention, 2)


def rank(candidates: List[Dict]) -> List[Dict]:
    """Adds Wikipedia pageviews and the score to each candidate; best first."""
    for c in candidates:
        if c.get('wikipedia'):
            try:
                c['wikipedia_views'] = sources.wikipedia_attention(c['wikipedia'])
            except Exception as e:
                logger.warning(f"Wikipedia pageviews failed for {c['wikipedia']!r}: {e}")
        c['score'] = score(c)
    return sorted(candidates, key=lambda c: c['score'], reverse=True)
