import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # API Settings
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('FLASK_PORT', 5001))
    DEBUG = os.getenv('DEBUG', 'True').lower() in ['true', '1', 'yes']

    # Swagger Settings
    SWAGGER_URL = os.getenv('SWAGGER_URL', '/swagger')
    API_URL = os.getenv('API_URL', '/swagger.json')

    # Async Status States (Anpassbar gemäß Anweisung)
    STATUS_FINISHED = ("finished", "done", "success", "completed")
    STATUS_ERROR = ("error", "failed")
    STATUS_RUNNING = "running"
    STATUS_PENDING = "pending"

    # Detailed Pipeline Statuses
    STATUS_FETCHING = "fetching_metadata"
    STATUS_EMBEDDING = "computing_embeddings"
    STATUS_FILTERING = "filtering_network"
    STATUS_CLUSTERING = "clustering"
    STATUS_REDUCING = "reducing_dimensions"
    STATUS_FLOW = "calculating_flow"
    STATUS_SAVING = "saving_results"

    # OpenAlex Settings
    OPENALEX_API_KEY = os.getenv('OPENALEX_API_KEY')
    OPENALEX_BASE_URL = "https://api.openalex.org"
    DOCSET_MIN_SIZE = int(os.getenv('DOCSET_MIN_SIZE', 500))
    DOCSET_MAX_SIZE = int(os.getenv('DOCSET_MAX_SIZE', 5000))

    # Topic Modeling Settings
    TOPIC_MAX_LEAF_SIZE = int(os.getenv('TOPIC_MAX_LEAF_SIZE', 150))
    TOPIC_MIN_TOPIC_SIZE = int(os.getenv('TOPIC_MIN_TOPIC_SIZE', 15))
