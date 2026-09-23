# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # writable caches for the non-root user (numba via umap, matplotlib, huggingface)
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/matplotlib \
    HF_HOME=/tmp/huggingface

# CPU-only torch first; the PyPI wheel would pull in CUDA and several GB of nvidia-* packages.
# The pinned torch==2.9.0 in requirements.txt is then already satisfied.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch==2.9.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt
# WordNet for the lemmatizer in TopicModeling.py
RUN python -m nltk.downloader -d /usr/local/share/nltk_data wordnet

# uid 1000 matches the host user, so bind-mounted directories stay writable on both sides
RUN useradd --create-home --uid 1000 thesis
WORKDIR /app
COPY --chown=thesis:thesis . .
RUN mkdir -p data models && chown thesis:thesis data models
USER thesis

# data/ holds the processed docsets, models/ the SPECTER2 base model and adapter
VOLUME ["/app/data", "/app/models"]
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"FLASK_PORT\", \"5001\")}/healthz', timeout=4)"
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
