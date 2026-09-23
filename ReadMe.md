# Finding the Needle in a Haystack

[![tests](https://github.com/jan-c-buchkremer/master-thesis/actions/workflows/tests.yml/badge.svg)](https://github.com/jan-c-buchkremer/master-thesis/actions/workflows/tests.yml)

For my Master Thesis I developed the Finding-The-Needle System (FTN-System), a visual analytics tool for large sets of research papers within the KATI-Lab of the Fraunhofer FKIE institute. Its source code is presented in this repository.

The system requires a direct connection to the KATI database to function, consider this repository mainly demonstrative.

## Motivation

Exponential growth of scientific literature output has created a significant "burden of knowledge" for researchers. Conventional search engines rely heavily on text-based keyword matching: either your query is to narrow, and you might miss important work, or your query is too broad, and relevant papers become few among many. While modern AI tools attempt to solve this by text retrieval and summary, they take human control out of the exploration phase, pushing researchers into bad habits and total reliance. 

The FTN-System addresses these limitations by providing a transparent, user-steered visual landscape. It transforms raw bibliographic metadata into an interactive topography, allowing researchers to explore both semantic relationships and structural citation networks to find relevant literature. You get what the broad query might return you, a couple of thousand papers, together  with the tools to make sense of them. 

You are looking for the needle in a haystack. 

## Visual Showcase
None of the views shown below are static, all of them are screenshots out of and otherwise interactive visualisation. Each datapoint represents a research paper, interaction is possible with every single visual element.
### Topic Landscape
<p align="center">
  <img src="gfx/topic_landscape.png" alt="Topic Landscape" width="500"/>
</p>
The Topic Landscape View of a document set without any filters applied.

### Citation Network 
<p align="center">
  <img src="gfx/citation_network.png" alt="Citation Network" width="500"/>
</p>
The Citation Flow View of a document set with rectangular cohorts visible in the background. All papers are made visible through a checkbox controlling the abstraction of the visualization. A singular paper is selected, revealing both its citations and references.

### Ego-Network 
<p align="center">
  <img src="gfx/ego-network-side-by-side.png" alt="Ego Network" width="500"/>
</p>
The Ego-Network View of a (highly-cited) publication, changing the view to a subset of papers in the most significant academic vicinity. All three different link modes between papers are shown side-by-side. The cosine similarity denotes the semantic proximity of a paper to the rest, links are drawn if similarity is below a certain threshold.

## System Architecture

The application is structured into three primary layers: a Flask API, a Python processing backend, and a D3.js frontend.

### 1. API & Orchestration (`app.py`, `worker.py`, `Database.py`)
A Flask-based REST API accepts processing requests, serves the frontend interface, and handles static file delivery.
Requests are recorded in a database (Postgres in production, SQLite locally) and queued as jobs; a separate
worker process (`worker.py`) takes jobs from the queue and runs the pipeline. Docset status, the task uuids
and the queue live in the database; the pipeline results stay JSON files under `data/<hash>/`.
The schema is managed with Alembic (`migrations/`).

### 2. Core Processing Backend
The central engine coordinates data acquisition, semantic embedding, clustering, and topic modeling:
* **`DocumentSetProcessor.py`**: Orchestrates the pipeline state transitions.
* **`DataHandler.py`**: Executes SPARQL queries to the KATI database and normalizes metadata.
* **`Analysis.py`**: Generates SPECTER2 semantic embeddings, performs UMAP dimensionality reduction, and calculates temporal citation flows.
* **`TopicModeling.py`**: Partitions the document set using hierarchical density-based clustering (HDBSCAN) and synthesizes human-readable topic labels using Large Language Models.

### 3. Interactive Visualization (Frontend)
The frontend is a Single-Page Application (SPA) built with D3.js. It utilizes a Coordinated Multiple Views (CMV) architecture, meaning interactions or filters applied in one view instantly update the others. The interface provides three distinct analytical perspectives:

* **Topic Landscape View**: A global 2D scatterplot projection where spatial proximity represents semantic similarity. It allows researchers to identify major thematic clusters and interdisciplinary overlaps.
* **Citation Flow View**: A chronological timeline that maps the flow of citations between different topic clusters over time, visualizing the evolution of research trends.
* **Ego-Network View**: A localized, force-directed graph focusing on the immediate neighborhood of a single selected paper. Users can adjust zero-sum sliders to weigh the importance of semantic similarity versus structural citation links.

The interface is driven by dynamic queries, allowing users to filter the corpus by publication year, impact thresholds, specific authors, or journals, with immediate visual feedback.

## Getting Started

### 1. Environment Variables
Create a `.env` file in the root directory. You will need API keys for the various services used.

```env
# API Keys for LLM Service
OPENROUTER_API_KEY=your_openrouter_key

# API Key for OpenAlex (free account at openalex.org -> openalex.org/settings/api)
OPENALEX_API_KEY=your_openalex_key

# Flask Configuration
FLASK_PORT=5001

```

### 2. Download Models
Before running the pipeline, ensure the SPECTER2 models are downloaded locally.
Run the helper script:
```bash
python SaveModel.py
```
This should populate the `models/` directory. (The worker also downloads them on first start if missing.)

## Running the Application

The application consists of the Flask web app and the pipeline worker, sharing one database.
Without `DATABASE_URL` both use a local SQLite file (`local.db`).

```bash
alembic upgrade head      # create / update the database schema (once, and after pulling changes)
python app.py             # web app
python worker.py          # in a second terminal: runs the queued pipeline jobs
```

Once running, the server exposes:
-   **Home Page**: `http://localhost:5001/`
-   **Swagger UI**: `http://localhost:5001/swagger/` (API Documentation)
-   **Visualization**: `http://localhost:5001/visualisations/index.html`

## Usage

### Starting a New Analysis
You can start a new analysis via the API.

**Endpoint**: `GET /start`
**Parameters**:
- `docset_iri`: The IRI of the document set to process (from the SPARQL endpoint).
- `docset_name` (optional): A human-readable name for the set.

**Example**:
```bash
curl "http://localhost:5001/start?docset_iri=http://example.org/my-docset&docset_name=MyResearchTopic"
```
This returns a `uuid` for the task.

### Starting a New Analysis from OpenAlex
Instead of a Fraunhofer docset IRI, you can define a docset as an OpenAlex search+filter query. The result is automatically padded (via a one-hop citation snowball) or trimmed (by internal citation connectivity + external citation count) to land between `DOCSET_MIN_SIZE` and `DOCSET_MAX_SIZE` (500-5000 by default).

**Endpoint**: `GET /start_openalex`
**Parameters**:
- `search` (optional): free-text query.
- `filter` (optional): a raw OpenAlex filter string, e.g. `publication_year:2018-2024,type:article` - the same format shown by the "API" link on openalex.org, so it can be copy-pasted directly. At least one of `search`/`filter` is required.
- `docset_name` (optional): human-readable name; defaults to the query itself.
- `min_size` / `max_size` (optional): override the default 500/5000 bounds.

**Example**:
```bash
curl "http://localhost:5001/start_openalex?search=topic+modeling+scientific+literature&filter=publication_year:2018-2024"
```
This also returns a `uuid`, pollable via the same `/status` and `/result` endpoints as above.

### Checking Status
Poll the status endpoint with the returned UUID.

**Endpoint**: `GET /status?uuid=<UUID>`

### Viewing Results
Once the status is `finished`, you can retrieve the visualization URL.

**Endpoint**: `GET /result?uuid=<UUID>`

### Manual Execution (CLI)
You can still run the processor manually for debugging or offline processing:

```bash
python DocumentSetProcessor.py
```
*Note: Ensure you modify the `__main__` block in `DocumentSetProcessor.py` to point to your desired document set hash or name.*

## Development

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

# 2. Install runtime + development dependencies
python -m pip install -r requirements.txt -r requirements-dev.txt

# 3. Configure environment variables
cp .env.example .env            # then fill in the API keys

# 4. Run the tests (offline; no API keys or downloaded models required)
python -m pytest -q
# against Postgres instead of SQLite (the tables in that database are dropped and recreated!):
# TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/thesis_test python -m pytest -q
```

After changing the tables in `Database.py`, add a migration with
`alembic revision --autogenerate -m "what changed"` and review it; `tests/test_migrations.py` fails
while the migrations and `Database.py` disagree.

The test suite runs automatically on GitHub Actions for every push and pull request (`.github/workflows/tests.yml`),
once with SQLite and once with Postgres.
On pushes to `main` and `deploy` it also builds the Docker image and publishes it to
`ghcr.io/jan-c-buchkremer/master-thesis` (tagged with the branch name and short commit SHA).

## Docker

```bash
docker compose up --build        # Postgres, migrations, web app and worker -> http://localhost:5001/
```

One image serves three roles: the web app (default command, gunicorn; see `gunicorn.conf.py`), the worker
(`python worker.py`) and the migrations (`alembic upgrade head`). The worker downloads the SPECTER2 models into
`/app/models` on first start (about 420 MB), so keep that directory on a volume. `GET /healthz` returns 200
while the database is reachable. Behind a reverse proxy the app can be mounted under a path prefix sent in
`X-Forwarded-Prefix` (e.g. `/thesis`); all frontend URLs are relative. `DEBUG` is off unless set in the environment.

Stopping the worker (SIGTERM) puts a running job back into the queue; if a worker dies without that, its job is
picked up again once its heartbeat is older than `WORKER_STALE_SECONDS` (default 180), at most
`WORKER_MAX_ATTEMPTS` (default 3) times. Several workers can run side by side.
