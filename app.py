import os
import logging
import socket
import uuid
from flask import Flask, request, jsonify, url_for, send_from_directory, render_template
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint
from werkzeug.middleware.proxy_fix import ProxyFix

from Config import Config
import Database
import DocsetHash

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, template_folder='templates')
app.config.from_object(Config)
CORS(app)
# Behind a reverse proxy (Caddy), trust its X-Forwarded-* headers, including the
# path prefix the app is mounted under (e.g. /thesis), so url_for builds correct URLs.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# --- Swagger Setup ---
# Point to the static YAML file instead of generating JSON
SWAGGER_YAML_URL = '/swagger.yaml'

# Asset and spec URLs are relative to the Swagger UI page, so they also resolve
# when the app is mounted under a path prefix; the blueprint route itself is set
# by url_prefix below.
swaggerui_blueprint = get_swaggerui_blueprint(
    '.',
    '..' + SWAGGER_YAML_URL,
    config={
        'app_name': "Docset Visualization API",
        'operationsSorter': None  # Disable sorting, use file order
    }
)
app.register_blueprint(swaggerui_blueprint, url_prefix=app.config['SWAGGER_URL'])

# Pipeline runs happen in the worker (worker.py): the web app only records them in the
# database and queues a job. Status and the uuid -> docset mapping live in the database too.


# --- Helper Functions ---
def get_data_dir():
    return Config.DATA_DIR if hasattr(Config, 'DATA_DIR') else 'data'


def has_processed_result(docset_hash):
    """Checks whether the expected result files exist for a docset hash."""
    docset_dir = os.path.join(get_data_dir(), docset_hash)
    docset_file = os.path.join(docset_dir, f"{docset_hash}_docset.json")
    topics_file = os.path.join(docset_dir, f"{docset_hash}_topics.json")
    return os.path.exists(docset_file) and os.path.exists(topics_file)


def start_run(docset_hash, kind, params, name, source, iri=None, query=None):
    """
    Returns a new task uuid for the docset, queuing a pipeline run unless the docset
    is already processed or a run for it is queued or in progress.
    """
    task_uuid = str(uuid.uuid4())
    existing = Database.get_docset(docset_hash)
    if existing and existing["status"] in Config.STATUS_FINISHED and has_processed_result(docset_hash):
        logging.info(f"Docset {docset_hash} already processed. Reusing existing result.")
    elif Database.request_run(docset_hash, kind, params, name=name, source=source, iri=iri, query=query) is None:
        logging.info(f"Docset {docset_hash} already queued or in progress. Reusing existing task state.")
    else:
        logging.info(f"Docset {docset_hash}: queued a {kind} run.")
    Database.save_task(task_uuid, docset_hash)
    return task_uuid


def resolve_task(task_uuid):
    """Returns (docset, error_response) for a task uuid."""
    docset_hash = Database.get_task_docset_hash(task_uuid)
    if not docset_hash:
        return None, (jsonify({"error": "Unknown uuid"}), 404)
    docset = Database.get_docset(docset_hash)
    if not docset:
        return None, (jsonify({"error": "Metadata not found for this task"}), 404)
    return docset, None


# --- API Routes ---

@app.route("/swagger.yaml")
def serve_swagger_spec():
    """Serves the static swagger.yaml file, with basePath set to the proxy prefix if there is one."""
    if not request.script_root:
        return send_from_directory(os.path.abspath(os.path.dirname(__file__)), 'swagger.yaml')
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'swagger.yaml'), encoding='utf-8') as f:
        spec = f.read().replace('basePath: "/"', f'basePath: "{request.script_root}"', 1)
    return app.response_class(spec, mimetype='application/yaml')


@app.route("/healthz")
def healthz():
    """Health check: 200 while the database is reachable, 503 otherwise. (The worker has its own check.)"""
    if not Database.ping():
        return jsonify({"status": "unavailable", "error": "database not reachable"}), 503
    return jsonify({"status": "ok"})


@app.route("/")
def home():
    """Renders the main startup page."""
    return render_template('home.html')


@app.route("/start", methods=["GET"])
def start():
    """
    Starts the processing of a new document set.
    """
    docset_iri = request.args.get('docset_iri')

    if not docset_iri:
        return jsonify({"error": "Missing required parameter: docset_iri"}), 400

    task_uuid = start_run(
        DocsetHash.hash_iri(docset_iri), "iri", {"docset_iri": docset_iri},
        # Use the IRI as the name; the worker replaces it with the name from SPARQL if there is one
        name=docset_iri, source="fraunhofer", iri=docset_iri,
    )
    return jsonify({"uuid": task_uuid}), 202


@app.route("/start_openalex", methods=["GET"])
def start_openalex():
    """
    Starts processing of a new document set sourced from an OpenAlex search+filter
    query, instead of a Fraunhofer docset IRI.
    """
    search = request.args.get('search')
    # Raw OpenAlex filter string, e.g. "publication_year:2015-2024,type:article" -
    # the same format the OpenAlex website's own "API" link produces, so it can be copy-pasted.
    raw_filter = request.args.get('filter')
    docset_name = request.args.get('docset_name')
    min_size = request.args.get('min_size', type=int) or Config.DOCSET_MIN_SIZE
    max_size = request.args.get('max_size', type=int) or Config.DOCSET_MAX_SIZE

    if not search and not raw_filter:
        return jsonify({"error": "At least one of 'search' or 'filter' is required."}), 400

    task_uuid = start_run(
        DocsetHash.hash_query(search=search, raw_filter=raw_filter), "openalex",
        {"search": search, "raw_filter": raw_filter, "docset_name": docset_name,
         "min_size": min_size, "max_size": max_size},
        name=docset_name or search or raw_filter, source="openalex",
        query={"search": search, "filters": None, "raw_filter": raw_filter},
    )
    return jsonify({"uuid": task_uuid}), 202


@app.route("/status", methods=["GET"])
def status():
    """
    Checks the status of a processing task.
    """
    task_uuid = request.args.get("uuid")
    if not task_uuid:
        return jsonify({"error": "Missing uuid parameter"}), 400

    docset, error_response = resolve_task(task_uuid)
    if error_response:
        return error_response

    response = {"uuid": task_uuid, "status": docset["status"]}
    if docset.get("error"):
        response["error"] = docset["error"]
    return jsonify(response)


@app.route("/result", methods=["GET"])
def result():
    """
    Retrieves the result URL for a finished task.
    """
    task_uuid = request.args.get("uuid")
    if not task_uuid:
        return jsonify({"error": "Missing uuid parameter"}), 400

    docset, error_response = resolve_task(task_uuid)
    if error_response:
        return error_response

    status = docset["status"]

    if status in Config.STATUS_FINISHED:
        # Construct the URL to the visualization
        viz_url = url_for('serve_visualisation_page', _external=True) + f"?docset={docset['hash']}"

        return jsonify({
            "uuid": task_uuid,
            "status": status,
            "url": viz_url
        })

    if status in Config.STATUS_ERROR:
        return jsonify({
            "uuid": task_uuid,
            "status": status,
            "error": docset.get("error")
        }), 500

    return jsonify({
        "uuid": task_uuid,
        "status": status,
        "message": "Process still in progress"
    }), 202


@app.cli.command("import-docsets")
def import_docsets():
    """Imports docsets processed before the database existed (data/<hash>/metadata.json)."""
    print(f"Imported {Database.import_file_metadata(get_data_dir())} docset(s).")


# --- Static File Serving ---

@app.route('/visualisations/index.html')
def serve_visualisation_page():
    """Serves the main visualization HTML page."""
    return send_from_directory(Config.VIS_DIR if hasattr(Config, 'VIS_DIR') else 'visualisations', 'index.html')


@app.route('/visualisations/<path:path>')
def serve_visualisation_files(path):
    """Serves static files for the visualisation (JS, CSS, etc.)."""
    return send_from_directory(Config.VIS_DIR if hasattr(Config, 'VIS_DIR') else 'visualisations', path)


@app.route('/data/<path:path>')
def serve_data_files(path):
    """Serves the processed data files required by the visualisation."""
    return send_from_directory(Config.DATA_DIR if hasattr(Config, 'DATA_DIR') else 'data', path)


if __name__ == '__main__':
    # Ensure Config has these paths set if not already
    if not hasattr(Config, 'VIS_DIR'):
        Config.VIS_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'visualisations')
    if not hasattr(Config, 'DATA_DIR'):
        Config.DATA_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'data')

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        print("----------------------------------------------------")
        print(f"Server is running!")
        print(f"Home Page:  http://{local_ip}:{Config.PORT}/")
        print(f"Swagger UI: http://{local_ip}:{Config.PORT}{Config.SWAGGER_URL}")
        print("----------------------------------------------------")
    except Exception:
        pass

    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)