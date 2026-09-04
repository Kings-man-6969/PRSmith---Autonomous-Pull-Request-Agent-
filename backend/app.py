"""FastAPI application factory for PRSmith v2."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from backend.api.auth import router as auth_router
from backend.api.jobs import router as jobs_router
from backend.api.repositories import router as repositories_router
from backend.api.webhook import router as webhook_router
from backend.auth.token_encryption import validate_encryption_key
from backend.config import settings
from backend.database.models import Base
from backend.database.sessions import engine
from sqlalchemy import text
from backend.observability.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle hook to initialize database tables and resources."""
    configure_logging(settings.LOG_LEVEL)
    logger.info("Initializing PRSmith v2 backend", environment=settings.ENVIRONMENT)

    # 1. Eagerly validate required encryption key on startup (fail-fast)
    validate_encryption_key()

    # 2. Initialize tables if SQLite or local dev
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    logger.info("Shutting down PRSmith v2 backend")
    await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="PRSmith v2 — Autonomous Pull Request Review & Repair",
        version="2.0.0",
        description="Repository-aware autonomous code review and repair system powered by AST Knowledge Graph and Hybrid RAG.",
        lifespan=lifespan,
    )

    # CORS Middleware — explicit origins required when allow_credentials=True
    allowed_origins = [settings.FRONTEND_URL]
    if settings.ENVIRONMENT != "production":
        allowed_origins.extend([
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ])
    allowed_origins = sorted(list(set(filter(None, allowed_origins))))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Mount Routers
    app.include_router(webhook_router)
    app.include_router(auth_router, prefix=settings.API_PREFIX)
    app.include_router(jobs_router, prefix=settings.API_PREFIX)
    app.include_router(repositories_router, prefix=settings.API_PREFIX)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict:
        return {
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
            "version": "2.0.0",
        }

    return app


app = create_app()
