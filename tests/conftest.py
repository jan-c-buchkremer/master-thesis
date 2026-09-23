"""
Shared pytest fixtures.

Dummy environment variables are set *before* any project module is imported so
that Config.py / TopicModeling.py / OpenAlexDataHandler.py import cleanly without
a .env file. No test in this suite performs network I/O or loads the SPECTER2
model, and every filesystem write is redirected to a pytest tmp_path.

Each test gets an empty database: a SQLite file in tmp_path, or the Postgres
database in TEST_DATABASE_URL (CI), whose tables are dropped and recreated.
"""
import os
import sys

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


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Empty database for one test; yields the Database module."""
    import Database
    from Config import Config

    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setattr(Config, "DATABASE_URL", url)
    Database.reset_engine()
    Database.metadata.drop_all(Database.get_engine())
    Database.create_schema()
    yield Database
    Database.reset_engine()


@pytest.fixture(scope="session")
def app_module():
    """Imports app.py once (it does not load the model; the worker does)."""
    import app as app_mod
    return app_mod


@pytest.fixture
def client(app_module, db, tmp_path, monkeypatch):
    """Flask test client with data/ redirected to a temp dir and a fresh database."""
    from Config import Config

    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path), raising=False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
