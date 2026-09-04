# PRSmith Analysis Sandbox Image
#
# This image contains all static analysis and testing tools.
# It is used by DockerSandbox to run analysis in isolation.
# Build: docker build -f docker/analysis.Dockerfile -t prsmith-analysis:latest ./docker

FROM python:3.12-slim

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js (for JS/TS tools) ─────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python analysis tools ─────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    ruff==0.4.9 \
    mypy==1.10.0 \
    black==24.4.2 \
    pytest==8.2.0 \
    pytest-cov==5.0.0 \
    pytest-asyncio==0.23.7

# ── JS/TS analysis tools ──────────────────────────────────────────────────────
RUN npm install -g \
    eslint@9 \
    prettier@3 \
    typescript@5 \
    @typescript-eslint/parser@8 \
    @typescript-eslint/eslint-plugin@8 \
    && npm cache clean --force

# ── Non-root user ─────────────────────────────────────────────────────────────
# Matches user=1000:1000 in DockerSandbox.run()
RUN useradd -u 1000 -m -s /bin/bash prsmith

# ── Workspace ─────────────────────────────────────────────────────────────────
RUN mkdir -p /workspace && chown prsmith:prsmith /workspace
WORKDIR /workspace
USER prsmith

CMD ["sh"]
