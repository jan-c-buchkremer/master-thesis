# gunicorn settings for the container (see Dockerfile).
# One worker only: pipeline runs are threads inside the web process and share one
# DocumentSetProcessor, so a second worker would not see (or would clash with) them.
import os

bind = f"0.0.0.0:{os.getenv('FLASK_PORT', '5001')}"
workers = 1
threads = int(os.getenv('GUNICORN_THREADS', '8'))
# Model loading at import can take a while on a cold start
timeout = 120
graceful_timeout = 30
accesslog = "-"
