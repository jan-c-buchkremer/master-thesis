# gunicorn settings for the container (see Dockerfile).
# The web process is stateless (state in the database, pipeline runs in worker.py),
# so any number of gunicorn workers can serve it.
import os

bind = f"0.0.0.0:{os.getenv('FLASK_PORT', '5001')}"
workers = int(os.getenv('GUNICORN_WORKERS', '2'))
threads = int(os.getenv('GUNICORN_THREADS', '4'))
timeout = 60
graceful_timeout = 30
accesslog = "-"
