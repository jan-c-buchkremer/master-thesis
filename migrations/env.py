from logging.config import fileConfig

from alembic import context

import Database

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Database.metadata


def run_migrations_offline():
    """Emits SQL to stdout instead of running it (alembic upgrade head --sql)."""
    context.configure(
        url=Database.Config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with Database.get_engine().connect() as connection:
        # batch mode lets ALTER-style migrations work on SQLite too
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
