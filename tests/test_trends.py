"""The topics of the week (trends/): query format, scoring, merging, checks, and the landing page."""
import datetime as dt
import json
import os

import pytest

from trends import anchor, openalex, query, select, sources, store, write


def test_build_filter_groups_phrases_exclusions_and_years():
    f = openalex.build_filter({"all_of": [["ultrasound", "focused ultrasound"], ["sodium-ion", "Na"]],
                               "none_of": ["review article"], "from_year": 2018})
    clauses = f.split(",")
    assert clauses[0] == 'title_and_abstract.search:ultrasound|"focused ultrasound"'
    assert clauses[1] == 'title_and_abstract.search:"sodium-ion"|Na'
    assert clauses[2] == 'title_and_abstract.search:!"review article"'
    assert clauses[3] == f"publication_year:2018-{dt.date.today().year}"
    assert clauses[4].startswith("type:")


def test_build_filter_strips_syntax_from_terms_and_needs_one():
    f = openalex.build_filter({"all_of": [['a,b|c "d"']], "from_year": 1900})
    assert f.startswith('title_and_abstract.search:"a b c d",publication_year:')
    assert f"publication_year:{dt.date.today().year - 30}-" in f  # from_year is clamped
    with pytest.raises(ValueError):
        openalex.build_filter({"all_of": [[" "]]})


def test_growth_compares_complete_years():
    today = dt.date(2026, 10, 1)
    counts = {"2022": 50, "2023": 50, "2024": 100, "2025": 100, "2026": 10}
    assert openalex.growth(counts, today) == 2.0
    assert openalex.growth({"2025": 5}, today) is None


def test_merge_keeps_first_entry_and_higher_engagement():
    a = sources._signal("social", "Hacker News", "T", "https://x", engagement=120)
    b = dict(a, engagement=300, title="changed")
    c = sources._signal("news", "Phys.org", "Other", "https://y")
    merged = sources.merge([a], [b, c])
    assert [s["url"] for s in merged] == ["https://x", "https://y"]
    assert merged[0]["title"] == "T" and merged[0]["engagement"] == 300


def test_score_weighs_outlets_and_wikipedia_spikes():
    one_outlet = {"signals": [{"outlet": "Phys.org"}] * 4}
    two_outlets = {"signals": [{"outlet": "Phys.org"}, {"outlet": "Nature"}]}
    assert select.score(two_outlets) > select.score({"signals": [{"outlet": "Phys.org"}] * 2})
    assert select.score(one_outlet) == 2.0
    spiking = dict(two_outlets, wikipedia_views={"ratio": 2.5})
    assert select.score(spiking) == select.score(two_outlets) + 1.5


def test_doi_from_journal_urls():
    assert anchor.doi_from_url("https://www.nature.com/articles/s41586-026-11253-5") == "10.1038/s41586-026-11253-5"
    assert anchor.doi_from_url("https://www.science.org/doi/10.1126/sciadv.aeb0659?x=1") == "10.1126/sciadv.aeb0659"
    assert anchor.doi_from_url("https://phys.org/news/2026-10-x.html") is None


def test_check_requires_size_and_paper(monkeypatch):
    monkeypatch.setattr(openalex, "work_by_id", lambda i: {"id": i})
    probes = iter([{"count": 150}, {"count": 900, "anchor_included": False}, {"count": 900, "anchor_included": True}])
    monkeypatch.setattr(openalex, "probe", lambda q, paper: next(probes))
    answer = {"paper": "W1", "query": {"all_of": [["x"]]}}
    assert "150 works" in query.check(answer, 200, 2000)[2]
    assert query.check(answer, 200, 2000)[2] == "the paper is not in the set"
    assert query.check(answer, 200, 2000)[2] == ""


def test_sources_prefer_news_and_distinct_outlets():
    signals = [{"source": "social", "outlet": "Hacker News", "title": "h", "url": "u1", "engagement": 500},
               {"source": "news", "outlet": "Phys.org", "title": "p1", "url": "u2"},
               {"source": "news", "outlet": "Phys.org", "title": "p2", "url": "u3"},
               {"source": "news", "outlet": "Nature", "title": "n", "url": "u4"}]
    assert [s["url"] for s in write._sources(signals)] == ["u2", "u4", "u1", "u3"]


def test_recent_featured_skips_this_and_later_weeks(monkeypatch, tmp_path):
    from Config import Config
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path), raising=False)
    for week in ("2026-W40", "2026-W41", "2026-W42"):
        store.save_json(os.path.join(store.featured_dir(), f"{week}.json"), {"topics": [{"title": week}]})
    store.save_json(os.path.join(store.featured_dir(), "current.json"), {"topics": [{"title": "current"}]})
    assert [t["title"] for t in store.recent_featured("2026-W42")] == ["2026-W41", "2026-W40"]


FEATURED = {
    "week": "2026-W42", "generated_at": "2026-10-18T20:00:00+00:00",
    "topics": [{
        "title": "Ultrasound neuromodulation", "field": "Neuroscience", "headline": "Sound waves that steer neurons",
        "lead": "Lead text.", "why_now": "Phys.org reported a study.", "docset": "a" * 32,
        "query": {"search": None, "raw_filter": "x"}, "papers": 224, "counts": {"2025": 40, "2026": 61},
        "growth": 1.5, "paper": {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x",
                                 "title": "Sono-mechanical nanostructures", "journal": "Nature Communications",
                                 "publication_date": "2026-02-23"},
        "directions": [{"name": "Sonogenetic Toolkits", "summary": "Genetic tools.", "papers": 34, "color": "#eb3b3b"}],
        "sources": [{"title": "Nanostructure-assisted ultrasound", "outlet": "Phys.org", "url": "https://phys.org/x"}],
    }],
}


def test_landing_page_shows_the_week(client, tmp_path):
    store.save_json(os.path.join(tmp_path, "featured", "current.json"), FEATURED)
    html = client.get("/").get_data(as_text=True)
    assert "Sound waves that steer neurons" in html and "12 to 18 October 2026" in html
    assert f"visualisations/index.html?docset={'a' * 32}&amp;view=citation-network" in html
    assert "Sono-mechanical nanostructures" in html and "2026 so far" in html


def test_landing_page_without_a_week(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "being prepared" in resp.get_data(as_text=True)


def test_form_moved_to_new_and_trends_files_are_not_served(client, tmp_path):
    assert "New Docset" in client.get("/new").get_data(as_text=True)
    store.save_json(os.path.join(tmp_path, "trends", "2026-W42", "signals.json"), [])
    store.save_json(os.path.join(tmp_path, "featured", "current.json"), FEATURED)
    assert client.get("/data/trends/2026-W42/signals.json").status_code == 404
    assert client.get("/data/./trends/2026-W42/signals.json").status_code == 404
    assert client.get("/data/featured/current.json").status_code == 200


def test_works_chart_marks_the_current_year(app_module):
    year = dt.date.today().year
    chart = app_module.works_chart({str(year - 1): 100, str(year): 50})
    assert [b["partial"] for b in chart["bars"]] == [False, True]
    assert chart["bars"][0]["h"] == chart["height"] and chart["bars"][1]["h"] == chart["height"] // 2
    assert app_module.works_chart({}) is None


def test_dominant_cluster_is_shown_by_its_subdivisions(monkeypatch, tmp_path):
    from Config import Config
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path), raising=False)
    h = "b" * 32
    topics = [{"id": 0, "parent": None, "name": "All", "papers": list(range(100))},
              {"id": 1, "parent": 0, "name": "Big", "papers": list(range(90))},
              {"id": 2, "parent": 0, "name": "Small", "papers": list(range(10))},
              {"id": 3, "parent": 1, "name": "Big A", "papers": list(range(50))},
              {"id": 4, "parent": 1, "name": "Big B", "papers": list(range(40))}]
    store.save_json(os.path.join(tmp_path, h, f"{h}_topics.json"), topics)
    papers, clusters = write.top_clusters(h)
    assert papers == 100 and [c["name"] for c in clusters] == ["Big A", "Big B", "Small"]
