"""
Shared pytest fixtures.

Dummy environment variables are set *before* any project module is imported so
that Config.py / TopicModeling.py / OpenAlexDataHandler.py import cleanly without
a .env file. No test in this suite performs network I/O or loads the SPECTER2
model: the Flask app is imported with model loading patched out, and every
filesystem write is redirected to a pytest tmp_path.
"""
import os
import sys
from unittest.mock import patch

import pytest

# --- Dummy environment (must precede any project import) ---
os.environ.setdefault("OPENROUTER_API_KEY", "test")
os.environ.setdefault("OPENALEX_API_KEY", "test")
os.environ["FLASK_PORT"] = "5055"
os.environ["DEBUG"] = "no"
os.environ["SPARQL_ENDPOINT"] = "http://sparql.invalid/sparql"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class _DummyThread:
    """Stand-in for threading.Thread so /start never runs the real pipeline."""

    def __init__(self, target=None, args=(), kwargs=None, **_):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.started = False

    def start(self):
        self.started = True


@pytest.fixture(scope="session")
def app_module():
    """Imports app.py once, with SPECTER2 model loading disabled."""
    import DocumentSetProcessor as dsp

    with patch.object(dsp.DocumentSetProcessor, "_load_specter_model", lambda self: None):
        import app as app_mod
    return app_mod


@pytest.fixture
def client(app_module, tmp_path, monkeypatch):
    """Flask test client with data/ redirected to a temp dir and threads stubbed out."""
    from Config import Config

    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(app_module, "Thread", _DummyThread)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
