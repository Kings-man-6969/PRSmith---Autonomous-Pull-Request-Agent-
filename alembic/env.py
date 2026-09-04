from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from backend.config import settings
from backend.database.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


import os

def get_url() -> str:
    """Resolve database URL for Alembic sync migrations."""
    db_url = os.environ.get("SYNC_DATABASE_URL") or os.environ.get("DATABASE_URL") or settings.SYNC_DATABASE_URL
    if "sqlite" in db_url:
        return db_url.replace("sqlite+aiosqlite://", "sqlite://")

    try:
        import psycopg2  # noqa
        return db_url
    except ImportError:
        default_sqlite = "test.db" if os.environ.get("ENVIRONMENT") == "test" else "prsmith.db"
        return f"sqlite:///./{default_sqlite}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
