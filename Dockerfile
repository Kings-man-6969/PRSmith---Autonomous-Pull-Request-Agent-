FROM python:3.11-slim

WORKDIR /app

# Install system dependencies & git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    pydantic \
    pydantic-settings \
    "sqlalchemy[asyncio]" \
    asyncpg \
    psycopg2-binary \
    "celery[redis]" \
    redis \
    openai \
    structlog \
    prometheus-client \
    gitpython \
    unidiff \
    pytest \
    pytest-asyncio \
    ruff \
    mypy \
    python-multipart \
    PyJWT \
    httpx \
    cryptography \
    alembic \
    docker \
    aiosqlite

COPY . .

EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
