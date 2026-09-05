"""Alembic environment — wired to the app's settings + model metadata.

- DATABASE_URL comes from app/core/config.py (env var / .env), NOT alembic.ini,
  so docker compose and local dev use the same source of truth.
- `import app.models` registers every table on Base.metadata for autogenerate.
- `compare_type=True` makes autogenerate detect column type changes.
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `app` importable when running alembic from any directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.base import Base  # noqa: E402
from app.core.config import settings  # noqa: E402
import app.models  # noqa: E402,F401  (registers all tables on Base.metadata)

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script without a live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()