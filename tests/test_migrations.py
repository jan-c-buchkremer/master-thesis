"""The Alembic migrations must build exactly the schema declared in Database.py."""
import os

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_migrations_match_models(db):
    # start from an empty database (the db fixture created the tables directly)
    db.metadata.drop_all(db.get_engine())
    with db.get_engine().begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    cfg = AlembicConfig(os.path.join(PROJECT_ROOT, "alembic.ini"))
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if Database.py has changes without a migration
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
