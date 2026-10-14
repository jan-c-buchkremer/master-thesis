"""
The research behind a topic, done by Claude as an agent (headless Claude Code, ClaudeCode.py): find the paper
the coverage reports on, read up on the advance, and design an OpenAlex query for the field around it, testing
and sharpening the query against OpenAlex until the set is the right size, on topic and contains the paper.
The agent's tools are `python -m trends probe` and `python -m trends paper` (tools.py) and web search.
Its answer is then checked here against OpenAlex before anything is queued.
"""

import json
import logging
import os
import sys
from typing import Dict, Optional

import ClaudeCode
from Config import Config
from trends import anchor, openalex

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = f"{sys.executable} -m trends"
MAX_COVERAGE = 10

PROMPT = """You are a researcher preparing a literature map for a public website. Each week it features science that is in the news. For each topic it shows a short introduction and an interactive map of the research field behind the news: its sub-directions, its key papers, and where this week's result fits. The map is built from the OpenAlex works your query finds. Its readers are researchers and interested non-specialists.

Topic: {title} ({field})
Why it was picked: {reason}

This week's coverage (number, outlet, headline, summary, link):
{coverage}
{doi_papers}
Tools (run them exactly like this, one command at a time):
- `{tool} paper --doi <DOI>`, `{tool} paper --title "<exact title>"`: a paper in OpenAlex (id, abstract, keywords, references).
- `{tool} paper --must "term|variant" --must "term|variant"`: this year's papers whose title or abstract contains a term of every group. Use the study's most distinctive method and material or system as the groups, not the disease or application (abstracts often abbreviate those).
- `{tool} probe '<query JSON>' --anchor <OpenAlex id>`: what a query finds: the number of works, works per year, the most cited and the newest titles, whether the anchor paper is in the set and how many of the papers it cites are.
- WebSearch and WebFetch: to read about the advance (a page may refuse to load; then use other sources).

Work in three steps.

1. Find the paper the coverage reports on, if there is one. It counts only if it matches the specifics of the coverage: the same method or technique, the same material, organism or system, the same finding. A related study or a review is not it. If you are not sure, go without a paper: a wrong paper is worse than none.

2. Understand the advance: which specific problem it addresses, with which methods, materials, organisms or systems, and which established research field it belongs to, under which technical terms.

3. Design the OpenAlex query for the field around the advance: not the whole discipline ("battery", "cancer"), not only this one result, but the research community that works on the same problem or with the same system. The query format:
   {{"all_of": [["term", "synonym", "variant"], ["term", "variant"]], "none_of": ["term"], "from_year": 2016}}
   Every group in all_of must match title or abstract; the terms inside a group are alternatives. Use the technical terms of abstracts, with variants (singular and plural, spelling, abbreviations). One or two groups, at most three; none_of at most three terms and usually empty; from_year 5 to 10 years back for a recent field.
   Probe it, read what it finds, and sharpen it until: {low} to {high} works, the titles belong to the field, the paper is in the set (if there is one) and a fair share of the papers it cites are too. Use at most 10 probes.

Answer with the final result: your understanding of the advance (3 to 4 sentences: the advance, the problem, the methods or systems, the field), the paper's OpenAlex id or null, the numbers of the coverage items that report on this advance or its field (leave out items about something else), the final query, and one sentence on how well the final set fits."""

SCHEMA = {
    "type": "object",
    "properties": {
        "understanding": {"type": "string"},
        "paper": {"type": ["string", "null"], "description": "OpenAlex id like W1234567890, or null"},
        "coverage": {"type": "array", "items": {"type": "integer"}},
        "query": {
            "type": "object",
            "properties": {
                "all_of": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                "none_of": {"type": "array", "items": {"type": "string"}},
                "from_year": {"type": "integer"},
            },
            "required": ["all_of", "none_of", "from_year"],
        },
        "assessment": {"type": "string"},
    },
    "required": ["understanding", "paper", "coverage", "query", "assessment"],
}


def _prompt(topic: Dict, low: int, high: int) -> str:
    coverage = "\n".join(f"[{i}] {s['outlet']}: {s['title']}" + (f" | {s['summary'][:400]}" if s.get('summary') else "")
                          + f" ({s['url']})" for i, s in enumerate(topic['signals'][:MAX_COVERAGE]))
    from_urls = anchor.from_urls(topic['signals'][:10])
    doi_papers = ("\nPapers linked directly from the coverage (by the DOI in a journal's own URL):\n"
                  + "\n".join(f"- {w['id'].rsplit('/', 1)[-1]}: {w['title']} ({w.get('journal')}, {w.get('publication_date')})"
                              for w in from_urls) + "\n") if from_urls else ""
    return PROMPT.format(title=topic['title'], field=topic.get('field', ''), reason=topic.get('reason', ''),
                         coverage=coverage, doi_papers=doi_papers, tool=TOOL, low=low, high=high)


def check(answer: Dict, low: int, high: int) -> (Optional[Dict], Dict, str):
    """Probes the agent's final query; returns (paper, probe, problem) with problem empty if it fits."""
    paper = openalex.work_by_id(answer['paper']) if answer.get('paper') else None
    if answer.get('paper') and paper is None:
        return None, {}, f"the paper {answer['paper']} is not in OpenAlex"
    probe = openalex.probe(answer['query'], paper)
    if not low <= probe['count'] <= high:
        return paper, probe, f"the query finds {probe['count']} works, not {low} to {high}"
    if paper and not probe.get('anchor_included'):
        return paper, probe, "the paper is not in the set"
    return paper, probe, ""


def build(topic: Dict, low: int = None, high: int = None) -> Optional[Dict]:
    """Researches the topic and works out its query; adds paper, understanding, query and probe to the topic."""
    low = low or Config.TRENDS_DOCSET_MIN_SIZE
    high = high or Config.TRENDS_DOCSET_MAX_SIZE
    prompt = _prompt(topic, low, high)
    tools = [f"Bash({TOOL} probe:*)", f"Bash({TOOL} paper:*)", "WebSearch", "WebFetch"]
    for attempt in (1, 2):
        try:
            # the tools run in the CLI's temporary directory, so they find the project through PYTHONPATH
            out = ClaudeCode.run(prompt, Config.TRENDS_LLM_MODEL, schema=SCHEMA, tools=tools, max_turns=60,
                                 env={"PYTHONPATH": PROJECT_ROOT}, timeout=1800, attempts=1)
        except ClaudeCode.ClaudeCodeError as e:
            logger.warning(f"{topic['title']}: research failed: {e}")
            return None
        answer = out["structured_output"]
        logger.info(f"{topic['title']}: {out.get('num_turns')} turns; paper {answer.get('paper')}; "
                    f"{json.dumps(answer['query'])}; {answer.get('assessment')}")
        paper, probe, problem = check(answer, low, high)
        if not problem:
            break
        logger.warning(f"{topic['title']}: {problem}")
        if attempt == 2:
            return None
        prompt += (f"\n\nYou answered before with paper {answer.get('paper')} and query {json.dumps(answer['query'])}, "
                   f"but {problem}. Continue from there and answer again.")
    # the coverage the texts may use: the items the agent found to be about this advance
    about = [topic['signals'][i] for i in dict.fromkeys(answer.get('coverage') or [])
             if isinstance(i, int) and 0 <= i < min(len(topic['signals']), MAX_COVERAGE)]
    topic['signals'] = about or topic['signals']
    topic.update(understanding=answer['understanding'], query_spec=answer['query'], assessment=answer['assessment'],
                 probe=probe, counts=probe.get('counts', {}), growth=openalex.growth(probe.get('counts', {})),
                 anchor={k: v for k, v in paper.items() if k in ('id', 'doi', 'title', 'journal', 'publication_date')}
                 if paper else None)
    return topic
