# Research Paper Analysis and Visualisation Pipeline

This project implements a comprehensive pipeline for extracting, analyzing, and visualizing sets of research papers. It combines a Python Flask backend for data processing (using LLMs and Graph Clustering) with a D3.js frontend for interactive exploration.

## System Architecture

The application consists of three main layers:

1.  **API & Orchestration (`app.py`)**: A Flask-based REST API that manages processing tasks, serves the visualization frontend, and handles static file delivery. It uses background threads to process document sets asynchronously.
2.  **Core Processor (`DocumentSetProcessor.py`)**: The central logic unit that coordinates data fetching, embedding generation, clustering, and topic modeling.
3.  **Visualization (Frontend)**: A D3.js web application that renders the processed data as an interactive 2D map of research papers.

### Key Modules

-   **`DocumentSetProcessor.py`**: Orchestrates the pipeline. It manages the lifecycle of a document set from IRI to processed JSON.
-   **`DataHandler.py`**: Handles SPARQL queries, file I/O, and metadata management.
-   **`Analysis.py`**: Performs heavy lifting: SPECTER2 embeddings, network filtering, UMAP dimensionality reduction, and citation flow calculation.
-   **`TopicModeling.py`**: Implements hierarchical clustering and uses LLMs (via OpenRouter) to generate topic labels and summaries.
-   **`TopicDictionary.py`**: Manages the hierarchical structure of topics.

## Setup and Installation

### 1. Environment Variables
Create a `.env` file in the root directory. You will need API keys for the various services used.

```env
# API Keys for LLM Service
OPENROUTER_API_KEY=your_openrouter_key

# Flask Configuration
FLASK_PORT=5001

```

### 2. Download Models
Before running the pipeline, ensure the SPECTER2 models are downloaded locally.
Run the helper script:
```bash
python SaveModel.py
```
This should populate the `models/` directory.

## Running the Application

The primary entry point is the Flask application.

```bash
python app.py
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
