import os
import logging
import socket
import json
import uuid
from threading import Thread
from flask import Flask, request, jsonify, url_for, send_from_directory, redirect, render_template
from flask_cors import CORS
from flask_swagger_ui import get_swaggerui_blueprint

from Config import Config
from DocumentSetProcessor import DocumentSetProcessor

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, template_folder='templates')
app.config.from_object(Config)
CORS(app)

# --- Swagger Setup ---
# Point to the static YAML file instead of generating JSON
SWAGGER_YAML_URL = '/swagger.yaml'

swaggerui_blueprint = get_swaggerui_blueprint(
    app.config['SWAGGER_URL'],
    SWAGGER_YAML_URL,
    config={
        'app_name': "Docset Visualization API",
        'operationsSorter': None  # Disable sorting, use file order
    }
)
app.register_blueprint(swaggerui_blueprint, url_prefix=app.config['SWAGGER_URL'])

# --- Initialize Backend Processor ---
try:
    processor = DocumentSetProcessor(
        base_model_dir="models/specter2_base_model",
        adapter_dir="models/specter2_adapter"
    )
    logging.info("DocumentSetProcessor initialized successfully.")
except Exception as e:
    processor = None
    logging.error(f"CRITICAL: Failed to initialize DocumentSetProcessor: {e}", exc_info=True)


# --- Helper Functions ---
def get_data_dir():
    return Config.DATA_DIR if hasattr(Config, 'DATA_DIR') else 'data'


def get_metadata_path(docset_hash):
    """Returns the path to the metadata.json file for a given hash."""
    return os.path.join(get_data_dir(), docset_hash, "metadata.json")


def read_metadata(docset_hash):
    """Reads the metadata.json file for a given hash."""
    metadata_path = get_metadata_path(docset_hash)
    if not os.path.exists(metadata_path):
        return None
    try:
        with open(metadata_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to read metadata for {docset_hash}: {e}")
        return None


def has_processed_result(docset_hash):
    """Checks whether the expected result files exist for a docset hash."""
    docset_dir = os.path.join(get_data_dir(), docset_hash)
    docset_file = os.path.join(docset_dir, f"{docset_hash}_docset.json")
    topics_file = os.path.join(docset_dir, f"{docset_hash}_topics.json")
    return os.path.exists(docset_file) and os.path.exists(topics_file)


# --- UUID Mapping Helpers ---
def get_tasks_dir():
    """Returns the directory where task mappings are stored."""
    tasks_dir = os.path.join(get_data_dir(), 'tasks')
    os.makedirs(tasks_dir, exist_ok=True)
    return tasks_dir


def save_task_mapping(task_uuid, docset_hash):
    """Saves a mapping from UUID to Docset Hash."""
    mapping_path = os.path.join(get_tasks_dir(), f"{task_uuid}.json")
    try:
        with open(mapping_path, 'w', encoding='utf-8') as f:
            json.dump({"docset_hash": docset_hash}, f)
    except Exception as e:
        logging.error(f"Failed to save task mapping for {task_uuid}: {e}")


def get_hash_from_uuid(task_uuid):
    """Retrieves the Docset Hash associated with a UUID."""
    mapping_path = os.path.join(get_tasks_dir(), f"{task_uuid}.json")
    if not os.path.exists(mapping_path):
        return None
    try:
        with open(mapping_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("docset_hash")
    except Exception as e:
        logging.error(f"Failed to read task mapping for {task_uuid}: {e}")
        return None


# --- Background Worker ---
def background_worker(docset_hash, docset_iri, docset_name):
    """
    Background task to process the document set.
    """
    try:
        if not processor:
            raise Exception("DocumentSetProcessor is not initialized.")

        logging.info(f"Task for {docset_hash}: Starting processing for {docset_name} ({docset_iri})")

        # Define callback to update status
        def update_status(new_status):
            processor.update_status(new_status)
            logging.info(f"Docset {docset_hash} status updated to: {new_status}")

        # Update status to running
        processor.docset_hash = docset_hash
        processor.docset_dir = os.path.join(processor.data_root, docset_hash)
        processor.metadata_path = os.path.join(processor.docset_dir, "metadata.json")
        processor.update_status(Config.STATUS_RUNNING)

        # This method now handles hashing and saving metadata internally
        processor.process_from_iri(
            docset_iri=docset_iri,
            docset_name=docset_name,
            status_callback=update_status
        )

        logging.info(f"Docset {docset_hash}: Processing complete.")

        # Update status to finished
        processor.update_status(Config.STATUS_FINISHED[0])  # "finished"

    except Exception as e:
        logging.error(f"Docset {docset_hash}: Failed with error: {e}", exc_info=True)
        # We need to manually set the paths if processor wasn't fully initialized or if it failed early
        if not processor.metadata_path:
            processor.docset_hash = docset_hash
            processor.docset_dir = os.path.join(processor.data_root, docset_hash)
            processor.metadata_path = os.path.join(processor.docset_dir, "metadata.json")

        processor.update_status(Config.STATUS_ERROR[0], str(e))  # "error"


# --- API Routes ---

@app.route("/swagger.yaml")
def serve_swagger_spec():
    """Serves the static swagger.yaml file."""
    return send_from_directory(os.path.abspath(os.path.dirname(__file__)), 'swagger.yaml')


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

    # Use the IRI as the name since the option to provide a name is removed
    docset_name = docset_iri

    # 1. Generate UUID for this specific request
    task_uuid = str(uuid.uuid4())

    # 2. Calculate hash for the document set
    docset_hash = DocumentSetProcessor.hash_iri(docset_iri)

    # 3. Save the mapping (UUID -> Hash)
    save_task_mapping(task_uuid, docset_hash)

    # 4. Reuse an existing task/result for this hash when possible.
    existing_metadata = read_metadata(docset_hash)
    if existing_metadata:
        existing_status = existing_metadata.get("status")
        existing_iri = existing_metadata.get("iri")
        iri_matches_hash = not existing_iri or existing_iri == docset_iri

        if iri_matches_hash:
            if existing_status in Config.STATUS_FINISHED and has_processed_result(docset_hash):
                logging.info(f"Docset {docset_hash} already processed. Reusing existing result.")
                return jsonify({"uuid": task_uuid}), 202

            if existing_status and existing_status not in Config.STATUS_ERROR:
                logging.info(
                    f"Docset {docset_hash} already in progress (status={existing_status}). Reusing existing task state.")
                return jsonify({"uuid": task_uuid}), 202

    # 5. Initialize metadata file for a new/restarted run
    metadata_path = get_metadata_path(docset_hash)
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)

    initial_metadata = {
        "name": docset_name,
        "iri": docset_iri,
        "hash": docset_hash,
        "status": Config.STATUS_PENDING
    }

    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(initial_metadata, f, indent=2)

    # 6. Start the worker (using the hash)
    thread = Thread(target=background_worker, args=(docset_hash, docset_iri, docset_name))
    thread.start()

    # 7. Return the UUID
    return jsonify({"uuid": task_uuid}), 202


@app.route("/status", methods=["GET"])
def status():
    """
    Checks the status of a processing task.
    """
    task_uuid = request.args.get("uuid")
    if not task_uuid:
        return jsonify({"error": "Missing uuid parameter"}), 400

    # 1. Resolve UUID to Hash
    docset_hash = get_hash_from_uuid(task_uuid)
    if not docset_hash:
        return jsonify({"error": "Unknown uuid"}), 404

    # 2. Read metadata using Hash
    metadata = read_metadata(docset_hash)
    if not metadata:
        return jsonify({"error": "Metadata not found for this task"}), 404

    return jsonify({
        "uuid": task_uuid,
        "status": metadata.get("status", "unknown")
    })


@app.route("/result", methods=["GET"])
def result():
    """
    Retrieves the result URL for a finished task.
    """
    task_uuid = request.args.get("uuid")
    if not task_uuid:
        return jsonify({"error": "Missing uuid parameter"}), 400

    # 1. Resolve UUID to Hash
    docset_hash = get_hash_from_uuid(task_uuid)
    if not docset_hash:
        return jsonify({"error": "Unknown uuid"}), 404

    # 2. Read metadata using Hash
    metadata = read_metadata(docset_hash)
    if not metadata:
        return jsonify({"error": "Metadata not found for this task"}), 404

    status = metadata.get("status")

    if status in Config.STATUS_FINISHED:
        # Construct the URL to the visualization
        viz_url = url_for('serve_visualisation_page', _external=True) + f"?docset={docset_hash}"

        return jsonify({
            "uuid": task_uuid,
            "status": status,
            "url": viz_url
        })

    if status in Config.STATUS_ERROR:
        return jsonify({
            "uuid": task_uuid,
            "status": status,
            "error": metadata.get("error")
        }), 500

    return jsonify({
        "uuid": task_uuid,
        "status": status,
        "message": "Process still in progress"
    }), 202


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