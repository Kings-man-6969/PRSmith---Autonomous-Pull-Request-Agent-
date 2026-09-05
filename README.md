# 🛡️ PRSmith v2 — Repository-Aware Autonomous Code Review & Repair

> **An autonomous engineering system that understands repository-wide context via AST Knowledge Graphs and Hybrid RAG, reviews Pull Requests with strictly grounded evidence, and repairs defects in isolated execution environments — before human code review begins.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green?logo=fastapi)](https://fastapi.tiangolo.com)
[![Celery](https://img.shields.io/badge/Celery-5.4-brightgreen?logo=celery)](https://docs.celeryq.dev)
[![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-blue?logo=postgresql)](https://github.com/pgvector/pgvector)
[![React 19](https://img.shields.io/badge/React-19-cyan?logo=react)](https://react.dev)
[![Vite](https://img.shields.io/badge/Vite-8-purple?logo=vite)](https://vitejs.dev)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue?logo=docker)](https://docker.com)
[![CI Pipeline](https://img.shields.io/badge/CI-Passing-brightgreen?logo=github-actions)](.github/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/Coverage-%3E60%25-brightgreen)](pyproject.toml)
[![Security Policy](https://img.shields.io/badge/Security-Policy-blue)](SECURITY.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 🌟 What is PRSmith?

PRSmith is not an unrestricted AI coding agent and not a simple PR comment bot. It is a **repository-aware, evidence-grounded autonomous review and repair system**.

Given a Pull Request, PRSmith:

1. **Builds a Knowledge Graph** of the repository using deterministic AST parsers (functions, callers, callees, dependencies, API endpoints, database operations, and tests).
2. **Performs Hybrid RAG Retrieval** combining structural graph traversals and semantic vector search (`pgvector`).
3. **Conducts a Read-Only Diff Review** producing structured, evidence-backed findings. Every finding must point to verified repository entities (hallucination defense).
4. **Captures Pre-Repair Baseline Validation** on an immutable baseline snapshot.
5. **Generates Surgical Patches** constrained by strict scope and size policies.
6. **Validates Repairs in Isolated Worktrees** using 7 deterministic layers (patch syntax, formatting, linting, type-checking, targeted tests, integration tests, build).
7. **Performs Differential Validation** ensuring repairs introduce **zero new regressions**.
8. **Iterates Autonomously** with dynamic RAG error feedback (detecting loops and stalls).
9. **Posts Structured Review Results** to GitHub for human approval — **never modifying the target branch directly**.

---

## 🏛️ End-to-End Architecture

```text
                         GitHub PR Webhook
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ Webhook Gateway & HMAC│
                     │  Outbox Event (Atomic)│
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  Repository Snapshot  │
                     │  assert_head_sha_curr │
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  AST Knowledge Graph  │
                     │  Deterministic Parser │
                     │  Callers/Callees/Tests│
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │       PR Risk Gate    │
                     │LOW / MED / HIGH / CRIT│
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ Sanitized Projections │
                     │ Secret Scan & Redact  │
                     └───────────┬───────────┘
                                 │
                         ┌───────┴───────┐
                         ▼               ▼
                 ┌───────────────┐ ┌───────────────┐
                 │ Diff Reviewer │ │Impact Analyzer│
                 │  (Read-Only)  │ │ (Downstream)  │
                 └───────┬───────┘ └───────┬───────┘
                         │                 │
                         └───────┬─────────┘
                                 ▼
                     ┌───────────────────────┐
                     │   Structured Review   │
                     │Evidence Grounding Gate│
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │   Baseline Capture    │
                     │  Pre-repair Sandbox   │
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │     RepairService     │
                     │  Scoped Unified Diff  │
                     │  Patch Scope Policy   │
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  Isolated Validation  │
                     │  Differential Checks  │
                     │ (Zero New Regressions)│
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ Publication Concurrency│
                     │ Partial Unique Index  │
                     │ Option B PATCH or POST│
                     │ Atomic is_current Swap│
                     └───────────┬───────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │  GitHub Review Result │
                     │ Human Review Authority│
                     └───────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+ (for frontend)
- Docker Desktop (for sandboxed execution and compose)
- OpenAI API Key

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/Kings-man-6969/PRSmith---Autonomous-Pull-Request-Agent-.git
cd PRSmith---Autonomous-Pull-Request-Agent-

# Install Python backend dependencies from reproducible lockfile
pip install -r requirements.lock.txt
pip install -e ".[dev]"

# Install React frontend dependencies
cd frontend
npm ci
cd ..
```

### 2. Configure Environment

```bash
cp .env.example .env
```
Edit `.env` with your credentials:
- `SECRET_KEY`: Random 32-byte secret for JWT signing (`python -c "import secrets; print(secrets.token_hex(32))"`)
- `GITHUB_TOKEN_ENCRYPTION_KEY`: Fernet key for encrypting OAuth tokens at rest (**REQUIRED**; generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- `GITHUB_APP_ID`: Your GitHub App ID
- `GITHUB_APP_PRIVATE_KEY_PATH`: Path to `.pem` private key
- `GITHUB_WEBHOOK_SECRET`: Secret configured in your GitHub App webhook
- `OPENAI_API_KEY`: Your LLM API key (or configure Gemini / Claude / DeepSeek / Custom endpoints)

### 3. Run with Docker Compose (Recommended)

```bash
docker-compose up -d --build
```

All 5 core services start in containerized isolation:
- **Backend API**: `http://localhost:8000` (FastAPI Swagger docs at `/docs`, health at `/health`)
- **Dashboard UI**: `http://localhost:5173` (React 19 Vite SPA)
- **Database**: PostgreSQL 16 with `pgvector` on port `5432`
- **Queue Broker**: Redis 7 on port `6379`
- **Async Workers**: Celery workers processing `celery`, `graph_build`, `review`, `repair`, and `validation` queues
- **Prometheus Metrics**: `http://localhost:8000/metrics`

---

## 💻 Local Development Without Docker

### Run Backend
```bash
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

### Run Celery Worker
```bash
celery -A worker.celery_app worker --loglevel=info --concurrency=4 -Q celery,graph_build,review,repair,validation
```

### Run Frontend Dev Server
```bash
cd frontend
npm run dev
```

---

## 🧪 Testing & Verification

PRSmith's test suite runs completely standalone and offline without requiring active external databases, Redis queues, or live LLM credentials.

### Run Full Test Suite (136 Passing Tests)
```bash
pytest tests/ -v
```

### Run Test Suite with Coverage Gate (Fail Under 60%)
```bash
pytest tests/ -v --cov=backend --cov=worker --cov-report=term-missing --cov-fail-under=60
```

### Run Linters, Strict Type Checking & Dependency Audit
```bash
# Lint code with Ruff
ruff check .

# Strict type check with Mypy
mypy backend worker

# Scan dependencies for known vulnerabilities
pip-audit
```

### Run E2E Safety Invariant & Security Suites
```bash
pytest tests/e2e tests/security -v
```

### Run Unit & Integration Tests
```bash
pytest tests/unit tests/integration -v
```

### Build Frontend Production Bundle
```bash
cd frontend
npm run build
```

---

## 📂 Project Structure

```
prsmith/
├── alembic/                # Alembic database migrations
│   └── versions/           # Versioned migration scripts (OAuth multi-tenant + v2 hardening)
├── backend/
│   ├── api/                # FastAPI routers (webhook, auth, jobs, repositories, findings)
│   ├── auth/               # GitHub App RS256, OAuth 2.0, Fernet token encryption, credential provider
│   ├── database/           # SQLAlchemy models & async/sync session factories
│   ├── delivery/           # Transactional Outbox Dispatcher with exponential backoff
│   ├── graph/              # AST Knowledge Graph builder, queries, freshness, and GC
│   ├── languages/          # LanguageAnalyzer interface & PythonAnalyzer
│   ├── llm/                # Multi-provider LLM abstraction (OpenAI, Gemini, Claude, Custom) & router
│   ├── observability/      # structlog JSON logging with redaction & Prometheus metrics
│   ├── orchestration/      # JobMutationService, attempt_recovery, idempotency, leases, state machine
│   ├── repair/             # RepairService, patch policy, patcher, repairer, & iterative repair loop
│   ├── repository/         # Immutable snapshots, GitOps, & isolated worktrees
│   ├── retrieval/          # Semantic vector search, graph retrieval, sanitized projections, & ranker
│   ├── review/             # Schemas, evidence validator, snapshot service, canonical hash, & publisher
│   ├── risk/               # PR risk score & blast radius analyzer
│   ├── sandbox/            # Fail-closed Docker execution sandbox & security policies
│   ├── security/           # Secret scanner, PII redaction, denylist, & AST escalation
│   ├── validation/         # Baseline, differential comparison, & 7-layer runner
│   ├── app.py              # FastAPI application factory & lifespan key validation
│   └── config.py           # Pydantic Settings
├── worker/
│   ├── celery.py           # Celery application & queue routing
│   └── tasks/              # Celery tasks (graph, review, repair, validation)
├── frontend/
│   ├── src/
│   │   ├── api/            # API client & TypeScript interfaces
│   │   ├── components/     # UI components (ConfidenceBar, FindingCard, PatchDiff, ImpactGraph, RepairTimeline, RiskBar, StatusBadge)
│   │   ├── pages/          # Dashboard, JobDetail, RepositoryGraph, RepositoryHub, FindingsExplorer, Login
│   │   ├── App.tsx         # Layout & authenticated routing
│   │   └── index.css       # Glassmorphism dark theme
├── tests/                  # Test suite across e2e, security, unit, integration, llm, repairer
├── docker/                 # Hardened sandbox Dockerfile
├── evaluation/             # Benchmark harnesses & golden PR datasets
├── docker-compose.yml      # Multi-container orchestration (Postgres, Redis, Backend, Worker, Frontend)
├── Dockerfile              # Backend and worker container specification
├── pyproject.toml          # Project dependencies & tool configurations
├── MEMORY.md               # Persistent architectural memory & invariant index
├── PROJECT_DECISIONS.md    # Complete chronological architectural decisions log
├── DESIGN.md               # Frontend design tokens & component specifications
└── SCRIPTS.md              # CLI commands and standard workflows cheat-sheet
```

---

## 🛡️ Safety & Security Model

- **Reliability Guarantee Contract**: PRSmith guarantees **at-least-once delivery + at-most-one active logical owner + idempotent/reconcilable side effects**. The system explicitly rejects "exactly-once execution" claims across distributed network and process boundaries.
- **Fail-Closed Sandbox**: In production, untrusted code executes strictly inside hardened containers (`network=none`, `cap_drop=ALL`, `pids_limit=100`, read-only rootfs, non-root user). Subprocess execution is strictly forbidden in production; if Docker is unavailable, PRSmith fails closed with `SandboxUnavailableError`.
- **Zero Target Branch Mutation**: The target repository branch is never modified directly. All repairs occur on isolated Git worktrees.
- **Transactional Outbox & Two-Level Task Attempts**: Webhook deliveries write to an `outbox_events` table within the same DB transaction. Worker execution is deduplicated via `task_executions` and `task_execution_attempts`. Expired attempts transition to `ABANDONED` and spawn new `PENDING` attempts (no ownerless `CLAIMED` states).
- **Cryptographic Artifact Chain**: Every published review and repair patch is bound by an immutable `chain_hash` derived from canonical JSON serialization of the commit SHA, AST graph digest, finding hashes, patch diff, and validation results.
- **Layered Publication Concurrency & Marker Reconciliation**: Active claims are serialized via a PostgreSQL partial unique index on `(repository_id, pr_number)` for `CLAIMED` and `POSTING` states. Single active publication per PR is enforced by an atomic single-transaction `is_current` swap. Reviews reconcile via Option B (PATCH existing comment if HTML marker `<!-- prsmith:published_review:{chain_hash} -->` is present, else POST new comment). Stale `POSTING` claims and transient 429/5xx errors (`FAILED_RETRYABLE`) recover with exponential backoff.
- **Sanitized Projections (Mandatory Security Boundary)**: No raw repository-derived string may enter persistent graph/vector storage, vector embeddings, or an LLM prompt without passing through `SanitizedProjection`. Secret scanning and redaction cover file contents, diff hunks, file paths, symbol names, exception messages, and structlog log fields.
- **Mandatory Snapshot Immutability Checkpoints**: Head SHA is validated against `job.head_sha` via `assert_head_sha_current()` at three mandatory checkpoints: before review, before each repair iteration, and before publication claim. Any drift transitions the job to `STALE_SNAPSHOT`.
- **Optimistic Concurrency Control**: All job state changes occur via `JobMutationService.transition()` conditional version checks (`WHERE version = :expected_version`), preventing stale worker resurrection.
- **Repair Domain Service Layering**: Repair domain logic (`RepairService`) is strictly decoupled from Celery worker orchestration tasks, eliminating circular imports.
- **Strict Read-Only Reviewer**: The Reviewer agent cannot modify code or create commits; structured output schemas prevent hallucinated fixes during review.
- **Evidence Grounding Gate**: AI claims regarding callers, callees, or tests must exist in the deterministic AST Knowledge Graph (with edge provenance weighting) or they are demoted.
- **Differential Validation Guarantee**: AI repairs are accepted only if they introduce **zero new test failures** against the pre-repair baseline.
- **Patch Policy & AST Escalation**: Sensitive files (CI workflows, Dockerfiles, secrets) are protected by a categorized denylist; YAML files are semantically classified; AST calls involving system APIs trigger human escalation (`ESCALATED`) rather than silent application.
- **Session Revocation & Double-Submit CSRF**: User sessions track a database `session_version` embedded in JWTs for instantaneous logout revocation, paired with double-submit cookie CSRF validation.
- **Human Authority**: Human reviewers always have final approval over proposed patches.

---

## 📊 Evaluation & Confidence Model

PRSmith calculates an evidence-backed confidence score for every review:

$$\text{Confidence} = f(\text{Review Grounding}, \text{Context Coverage}, \text{Lint Status}, \text{Type Checks}, \text{Targeted Tests}, \text{Differential Regressions})$$

Uncertainty states are explicitly exposed in the UI:
- `VALIDATED`
- `PARTIALLY_VALIDATED`
- `INSUFFICIENT_CONTEXT`
- `ENVIRONMENT_FAILURE`
- `HUMAN_ESCALATION`

---

## 🤝 Community & Contributing

- **Contributing Guide**: See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, standalone testing workflows, and conventional commit guidelines.
- **Security Policy**: See [SECURITY.md](SECURITY.md) for our formal threat model, trust boundaries, and coordinated vulnerability disclosure policy.
- **Changelog**: See [CHANGELOG.md](CHANGELOG.md) for detailed version history and release notes.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
