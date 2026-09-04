FROM python:3.11-slim

# Create isolated non-root sandbox user
RUN useradd -m -s /bin/bash sandboxuser

WORKDIR /workspace

# Install common testing and linting tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    pytest \
    pytest-asyncio \
    ruff \
    mypy \
    black

USER sandboxuser
