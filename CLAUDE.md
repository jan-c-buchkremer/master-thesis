# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A research paper analysis and visualization pipeline (Master's thesis project). It combines a Python/Flask backend that turns a set of research papers (fetched via SPARQL) into embeddings, hierarchical topic clusters, and a 2D semantic map, with a vanilla D3.js frontend for interactively exploring the result.

## Running the application

```bash
# One-time: download SPECTER2 base model + adapter into models/
python SaveModel.py

# Run the Flask server (reads .env for config)
python app.py
```

Server endpoints once running (default port 5001, see `.env`):
- `http://localhost:5001/` — home page (docset submission form)
- `http://localhost:5001/swagger/` — Swagger UI (spec served from `swagger.yaml`)
- `http://localhost:5001/visualisations/index.html?docset=<hash>` — the D3 visualization

There is no test suite, linter, or build step in this repo — there's nothing to run beyond starting the Flask app and exercising it manually or via the API.

### Async processing model

Processing is kicked off via `GET /start?docset_iri=...`, which returns a `uuid` and starts a background `Thread` (see `app.py`). Poll `GET /status?uuid=...` for pipeline state, then `GET /result?uuid=...` once finished to get the visualization URL. Task UUIDs are mapped to a docset hash (MD5 of the IRI) via files in `data/tasks/`; a docset already fully processed for a given hash is reused rather than recomputed (see `has_processed_result` / metadata status checks in `app.py`).

### Manual/offline pipeline runs

`DocumentSetProcessor.py`'s `__main__` block can be edited to run the pipeline directly (useful for debugging without Flask/threads). `run_processing_steps(start_from=...)` accepts `'embed' | 'filter' | 'cluster' | 'reduce' | 'flow' | 'all' | 'auto'` to resume/re-run from a specific pipeline stage against data already on disk.

## Architecture

### Backend pipeline (Python)

Each docset (a set of papers identified by a SPARQL IRI) is hashed (MD5 of the IRI) and gets its own directory under `data/<hash>/` containing `metadata.json` (name, iri, status), `<hash>_docset.json` (papers + embeddings + cluster assignments), and `<hash>_topics.json` (the topic hierarchy). `data/index.json` is a generated index of all docsets, rebuilt by `DataHandler.update_docset_index` after every save.

Pipeline orchestration flows through these modules, in order:

1. **`app.py`** — Flask REST API + static file serving for `data/` and `visualisations/`. Owns the background-thread lifecycle and status polling (statuses defined in `Config.py`: `pending` → `fetching_metadata` → `computing_embeddings` → `filtering_network` → `clustering` → `reducing_dimensions` → `calculating_flow` → `finished`/`error`).
2. **`DocumentSetProcessor.py`** — the orchestrator class. Loads the SPECTER2 model once at startup, holds pipeline state (`docset_df`, `topic_dict`), and delegates each stage to `Analysis.py`/`DataHandler.py`. `run_processing_steps` is the step dispatcher; each `_run_*_step` method calls into `Analysis.py` and then persists via `_save_results`.
3. **`DataHandler.py`** — all I/O: SPARQL queries against the Fraunhofer publications ontology (fetches paper metadata + multi-valued properties like authors/orgs/journals, then collapses to one row per paper), JSON load/save for docsets and topics, and the `index.json` rebuild. Has no analysis logic.
4. **`Analysis.py`** — the numeric pipeline stages, called in this order by the processor: `compute_embeddings` (SPECTER2 CLS-token embeddings, title+abstract joined with `[SEP]`, L2-normalized) → `filter_connected_component` (keep only papers connected via internal citations) → `run_clustering` (delegates to `TopicModeling.py`) → `run_dimensionality_reduction` (UMAP to 2D for the map) → `calculate_and_add_citation_flow` (yearly per-cluster citation stats for the alluvial/flow view).
5. **`TopicModeling.py`** — the actual clustering logic: recursive top-down UMAP+HDBSCAN clustering (`run_clustering`/`_recursive_cluster_step`) that keeps splitting a cluster until it's small enough or the silhouette score is high enough, with a forced-retry pass (tighter HDBSCAN params) when a cluster refuses to split. Cluster labeling uses c-TF-IDF/term-frequency keyword extraction plus an LLM (`mistralai/mistral-small-3.2-24b-instruct` via **OpenRouter**, key in `OPENROUTER_API_KEY`) to generate human-readable names/descriptions per cluster, propagating parent-cluster context down into child descriptions.
6. **`TopicDictionary.py`** — the in-memory/JSON-serializable data structure for the topic hierarchy (flat dict of `{id: {parent, cluster_label, name, description, papers, yearly_stats, color, ...}}`, not a tree object). Also assigns leaf-node colors by building a tree-distance ordering of leaves and spacing hues evenly around the HSV wheel, so semantically-adjacent sub-topics get visually similar colors.

Cross-cutting notes:
- Every stage that mutates `docset_df` merges its results back into the full dataframe via a `paper`-keyed left join (see the `update_cols`/`pd.merge` pattern in `Analysis.py`), so partial failures don't silently drop unrelated columns.
- `DocumentSetProcessor.update_status` reads-modifies-writes `metadata.json` on every stage transition; `app.py`'s `/status` endpoint just reads that file back.
- The SPARQL queries in `DataHandler.py` are tied to a specific Fraunhofer RDF ontology (`fhg:` prefix) — field names there are not generic and depend on that schema.

### Frontend (`visualisations/`)

Plain ES6 modules loaded via `<script type="module">` (no bundler/build step; `GenerateHTML.py` exists solely to flatten the module graph into a single self-contained standalone HTML file per docset for offline sharing — see `standalone_output/`).

- **`main.js`** — entry point, instantiates `Visualization` and kicks off `loadDocsetIndex()`.
- **`visualisation.js`** (~1900 lines) — the `Visualization` class: owns all D3 SVG setup, shared state (filters, selection, zoom/pan, scales), the render loop (`update()`), and delegates per-mode rendering to the three view classes below. Uses a D3 quadtree for spatial queries and viewport culling / LOD thresholds for performance at scale.
- **`TopicLandscapeView.js`** — the default 2D semantic map (UMAP coordinates, similarity/citation links).
- **`CitationNetworkView.js`** — aggregated cluster×year citation flow (alluvial-style) view, supports drill-down into the landscape view.
- **`EgoNetworkView.js`** — force-directed immediate-neighborhood view for a single paper.
- **`dataWorker.js`** — offloads heavy O(n²) computations (k-NN, HITS scores) to a Web Worker so the main thread stays responsive.
- **`utils.js`** — shared formatting helpers.

The three view classes are composed into `Visualization`, not subclasses — each receives the parent instance and reads/writes its shared state directly.

## Key design decisions

`DecisionLog.md` documents the scientific/technical rationale (with citations) behind non-obvious choices in the pipeline — e.g. why UMAP+HDBSCAN over other clustering, why CLS-token pooling, why forced recursive splitting, why the citation-flow model is structured the way it is, why the frontend uses quadtrees/culling/LOD. Consult it before changing clustering, embedding, or rendering-performance logic, since several choices look arbitrary without that context.

## Configuration

Config is environment-driven via `.env` (loaded by `python-dotenv` in `Config.py`): `OPENROUTER_API_KEY` (required for topic naming/description LLM calls), `FLASK_PORT`, `HOST`, `DEBUG`, `SPARQL_ENDPOINT` (defaults to `http://ks2:8890/sparql`, set as a default arg in `DocumentSetProcessor.__init__`).
