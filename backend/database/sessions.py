import os
from collections.abc import AsyncGenerator
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import settings

# Database URLs (honor os.environ overrides for tests)
database_url = os.environ.get("DATABASE_URL") or settings.DATABASE_URL
sync_database_url = os.environ.get("SYNC_DATABASE_URL") or settings.SYNC_DATABASE_URL

# For SQLite in tests/dev fallback if needed
is_sqlite = "sqlite" in database_url

async_db_url = database_url
if is_sqlite and not database_url.startswith("sqlite+aiosqlite:"):
    async_db_url = database_url.replace("sqlite://", "sqlite+aiosqlite://")

import sys
from sqlalchemy.pool import NullPool

engine_kwargs = {}
if not is_sqlite:
    is_celery = any("celery" in arg.lower() for arg in sys.argv) or os.environ.get("DB_POOL_CLASS") == "NullPool"
    if is_celery:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs.update({
            "pool_size": 20,
            "max_overflow": 10,
            "pool_pre_ping": True,
        })

default_sqlite_file = "test.db" if (os.environ.get("ENVIRONMENT") == "test" or settings.ENVIRONMENT == "test") else "prsmith.db"

# Async engine and sessionmaker
try:
    engine = create_async_engine(async_db_url, **engine_kwargs)
except Exception:
    async_db_url = f"sqlite+aiosqlite:///./{default_sqlite_file}"
    engine = create_async_engine(async_db_url)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Sync engine for Celery workers or migrations
sync_db_url = sync_database_url
if is_sqlite:
    sync_db_url = database_url.replace("+aiosqlite", "")

try:
    sync_engine = create_engine(
        sync_db_url,
        **(engine_kwargs if not is_sqlite else {})
    )
except Exception:
    sync_engine = create_engine(f"sqlite:///./{default_sqlite_file}")

sync_session_factory = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_sync_db_session() -> Session:
    """Sync session provider for Celery tasks."""
    return sync_session_factory()
