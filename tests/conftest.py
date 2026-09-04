"""Global pytest configuration and fixtures for PRSmith."""

import os
from pathlib import Path
import pytest

# Ensure test runs use isolated test.db with aiosqlite engine
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["SYNC_DATABASE_URL"] = "sqlite:///./test.db"
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-32-chars-minimum-length-ok"
os.environ["GITHUB_TOKEN_ENCRYPTION_KEY"] = "uX7vK3zY1u_r8o2WqH0pJ5mN4sB9cE6vL1aD3fG5hJ8="

TEST_DB_PATH = Path("./test.db")

@pytest.fixture(scope="session", autouse=True)
def init_and_cleanup_test_db():
    """Create fresh test database at session start and cleanup on exit."""
    from backend.database.models import Base
    from backend.database.sessions import sync_engine

    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except Exception:
            pass

    Base.metadata.create_all(bind=sync_engine)
    yield
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except Exception:
            pass


import pytest_asyncio


@pytest_asyncio.fixture
async def db_session():
    """Provide an async database session for a test."""
    from backend.database.sessions import async_session_factory
    async with async_session_factory() as session:
        yield session

